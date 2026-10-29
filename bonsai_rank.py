"""Rank every job in data/pipeline.md with a local Ollama model, one job at a time.

For each job: fetch the full JD (fetch-jd.mjs, public ATS APIs), then ask the
model to score it against the master resume and evidence ledgers, explain why,
and suggest resume changes. Code decides facts (company, title, location tier,
role bucket, ordering); the model only judges fit.

The fixed context (rules + resume + evidence) is the same system prompt on every
call, so Ollama reuses it from cache and only reads each new JD.

Output: listings/<date>-run-<NN>.md and .json, rewritten after every job.

Usage: python3 -I local/bonsai_rank.py [--model bonsai-2:27b]
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

JOBS = Path(__file__).resolve().parent.parent
RESUME_DIR = Path(os.environ.get("RESUME_AGENT_DIR", "/absolute/path/to/resume agent"))
OLLAMA = "http://localhost:11434/api/chat"
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 16384}

TIER1 = ["san francisco", "bay area", "palo alto", "mountain view", "menlo park", "sunnyvale",
         "san jose", "redwood city", "san mateo", "oakland", "berkeley", "santa clara",
         "cupertino", "foster city", "fremont", "walnut creek"]
TIER2 = ["california", ", ca", "los angeles", "san diego", "irvine", "santa monica"]
# Words CONSTRAINTS.md bans; code flags them in the model's suggestions.
BANNED = ["leverage", "utilize", "spearhead", "facilitate", "robust", "seamless", "cutting-edge",
          "state-of-the-art", "comprehensive", "innovative", "powerful", "significantly",
          "dramatically", "--", "—", "–"]


def tier(loc):
    s = loc.lower()
    if any(c in s for c in TIER1):
        return 1
    if any(c in s for c in TIER2):
        return 2
    return 3


def role(title):
    s = title.lower()
    if "forward deployed" in s or "deployed engineer" in s:
        return "FDE"
    if re.search(r"\b(ai|ml|llm)\b engineer|machine learning engineer|applied ai", s):
        return "AI Engineer"
    return "SWE"


def load_pipeline():
    jobs = []
    for line in (JOBS / "data" / "pipeline.md").read_text().splitlines():
        if not line.startswith("- [ ] "):
            continue
        parts = [p.strip() for p in line[6:].split(" | ")]
        loc = next((p for p in parts[3:] if not p.startswith("posted:")), "")
        posted = next((p[7:].strip() for p in parts[3:] if p.startswith("posted:")), "")
        jobs.append({"url": parts[0], "company": parts[1], "title": parts[2], "location": loc,
                     "posted": posted, "tier": tier(loc), "role": role(parts[2])})
    return jobs


def evidence_text():
    return "\n\n".join(f"--- evidence/{f.name} ---\n{f.read_text()}"
                       for f in sorted((RESUME_DIR / "evidence").glob("*.md")) if f.name != "README.md")


SYSTEM = f"""You evaluate one job posting for one candidate.
Candidate: MS Computer Science, graduating December 2026, so new grad / early career.
Targets: Software Engineer, AI Engineer, Forward Deployed Engineer.
Prefers hybrid in the SF Bay Area, then California, then major US cities.

Read the job description and the candidate's resume and evidence, then return:
- score: 1.0 to 5.0 (one decimal) for overall fit. 4.0+ means worth applying.
  Score low if the role is not engineering, or clearly too senior for a new grad.
- verdict: one sentence.
- why: 2 to 4 specific reasons, each tied to something the JD asks for.
- gaps: JD requirements the candidate does not clearly meet.
- level_fit: whether the seniority suits a December 2026 new grad, citing the JD.
- work_model: hybrid, in-office, remote or unknown, as the JD states it.
- resume_changes: 2 to 4 changes to tailor the resume for THIS job. Each may only reorder,
  emphasize or reword claims that already exist in the resume or evidence. Never invent a metric.
  Name the evidence file or resume section it relies on.
Follow the resume rules: no em dashes or double hyphens, no filler words like leverage, robust, seamless.
Do not invent facts about the job or the candidate.

RESUME RULES (CONSTRAINTS.md):
{(RESUME_DIR / "CONSTRAINTS.md").read_text()}

THE CANDIDATE'S MASTER RESUME:
{(RESUME_DIR / "MASTER_RESUME.md").read_text()}

EVIDENCE LEDGERS (what is provable):
{evidence_text()}"""

SCHEMA = {"type": "object", "properties": {
    "score": {"type": "number", "minimum": 1, "maximum": 5},
    "verdict": {"type": "string"},
    "why": {"type": "array", "items": {"type": "string"}},
    "gaps": {"type": "array", "items": {"type": "string"}},
    "level_fit": {"type": "string"},
    "work_model": {"type": "string", "enum": ["hybrid", "in-office", "remote", "unknown"]},
    "resume_changes": {"type": "array", "items": {"type": "object", "properties": {
        "change": {"type": "string"}, "relies_on": {"type": "string"}},
        "required": ["change", "relies_on"]}}},
    "required": ["score", "verdict", "why", "gaps", "level_fit", "work_model", "resume_changes"]}


def chat(model, user):
    body = json.dumps({"model": model, "stream": False, "think": False, "keep_alive": "30m",
                       "format": SCHEMA, "options": OPTIONS,
                       "messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=1800))
    return json.loads(d["message"]["content"]), (
        d.get("prompt_eval_count", 0), d.get("eval_count", 0), d.get("total_duration", 0) / 1e9)


def fetch_jd(url):
    p = subprocess.run(["node", "fetch-jd.mjs", url], cwd=JOBS, capture_output=True, text=True, timeout=60)
    return p.stdout.strip() if p.returncode == 0 else ""


def write_listing(jobs, meta, path):
    done = [j for j in jobs if "result" in j]
    L = [f"# Listings run: {meta['started']}", "",
         f"Model: `{meta['model']}` · jobs: {len(jobs)} · evaluated: {len(done)} · "
         f"runtime so far: {meta['minutes']:.0f} min", "",
         "Code decides company, title, location, tier, role and order. "
         "The model decides score, reasons and resume suggestions.", "",
         "| Score | Role | Tier | Company | Title | Work model |", "|---|---|---|---|---|---|"]
    order = sorted(done, key=lambda j: (-(j["result"].get("score") or 0), j["tier"]))
    for j in order:
        r = j["result"]
        L.append(f"| {r.get('score', '–')} | {j['role']} | {j['tier']} | {j['company']} | "
                 f"[{j['title']}]({j['url']}) | {r.get('work_model', '–')} |")
    L.append("")
    for j in order:
        r = j["result"]
        L.append(f"## {r.get('score', '?')} · {j['company']}: {j['title']}")
        L.append(f"{j['role']} · Tier {j['tier']} · {j['location']} · posted {j['posted'] or '?'} · [posting]({j['url']})")
        if "error" in r:
            L += ["", f"_Not evaluated: {r['error']}_", ""]
            continue
        L += ["", f"**Verdict:** {r['verdict']}", "",
              f"**Level fit:** {r['level_fit']}", f"**Work model (from JD):** {r['work_model']}", "", "**Why:**"]
        L += [f"- {w}" for w in r["why"]] + ["", "**Gaps:**"] + [f"- {g}" for g in r["gaps"]]
        L += ["", "**Suggested resume changes:**"]
        L += [f"- {c['change']} _(relies on: {c['relies_on']})_" for c in r["resume_changes"]]
        if r.get("flags"):
            L.append(f"\n> ⚠️ Code check found banned resume words: {', '.join(r['flags'])}")
        L.append("")
    path.write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="bonsai-2:27b")
    a = ap.parse_args()

    start = time.time()
    meta = {"model": a.model, "started": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "minutes": 0}
    out_dir = JOBS / "listings"
    out_dir.mkdir(exist_ok=True)
    day = dt.date.today().isoformat()
    run = len(list(out_dir.glob(f"{day}-run-*.md"))) + 1
    md, js = out_dir / f"{day}-run-{run:02d}.md", out_dir / f"{day}-run-{run:02d}.json"
    log = lambda m: print(f"[{dt.datetime.now():%H:%M:%S}] {m}", flush=True)

    jobs = load_pipeline()
    log(f"{len(jobs)} jobs; evaluating one at a time with {a.model} -> {md.name}")
    for n, j in enumerate(jobs, 1):
        jd = fetch_jd(j["url"])
        if not jd:
            j["result"] = {"error": "JD not fetchable through a public ATS API"}
            log(f"{n}/{len(jobs)} {j['company']}: no JD, skipped")
        else:
            user = (f"JOB: {j['company']} | {j['title']} | {j['location']}\nURL: {j['url']}\n\n"
                    f"JOB DESCRIPTION:\n{jd[:7000]}")
            try:
                r, (pin, pout, secs) = chat(a.model, user)
                r["flags"] = [w for w in BANNED if w in json.dumps(r["resume_changes"]).lower()]
                j["result"] = r
                log(f"{n}/{len(jobs)} {j['company']} | {j['title']}: {r['score']} "
                    f"({pin} tok in, {pout} out, {secs:.0f}s)")
            except Exception as e:
                j["result"] = {"error": f"model call failed: {e}"}
                log(f"{n}/{len(jobs)} {j['company']}: failed {e}")
        meta["minutes"] = (time.time() - start) / 60
        js.write_text(json.dumps({"meta": meta, "jobs": jobs}, indent=1))
        write_listing(jobs, meta, md)
    log(f"done in {meta['minutes']:.0f} min -> {md}")


if __name__ == "__main__":
    sys.exit(main())
