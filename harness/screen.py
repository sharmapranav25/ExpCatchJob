"""Step 4: quick screen of queued jobs with a local model (default qwen3.5:4b). Code first, then one model call.

The screen answers only three questions; everything that survives PASSes with a fit score, and Claude picks
from those in step 5. Pay, location and visa sponsorship are left to Claude.

  FAIL  US-citizen-only: citizenship, security clearance or "U.S. person" wording (code)
  FAIL  not entry level: 5+ years required, a senior title, or mid-level duties like mentoring others (code);
        3 or 4 years is a soft flag in modes/_brief.md, not a hard gate -- it still reaches the model
  FAIL  not a role the candidate wants or fits (model, against modes/_brief.md)
  PASS  everything else, with the model's 1 to 5 fit score

Code alone decides citizenship and level. Asked those questions too, qwen3.5:4b invented citizenship
requirements and called entry-level roles senior, failing 5 of 9 good jobs (2026-10-27 test); code made no
mistakes on them across 32 jobs. Visa lines are hidden from the model so they can't drag the fit score.
Writes no tracker rows or reports.

Usage:
  python3 -I local/harness/screen.py                      # pending jobs in data/pipeline.md
  python3 -I local/harness/screen.py --listings FILE      # pipeline-format lines from another file
Output: listings/<date>-screen-NN.md and .json
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bolt import BadAnswer, Bolt  # noqa: E402
from facts import SPONSOR, VISA, code_facts, parse_listing  # noqa: E402
from jd import fetch_jd  # noqa: E402

JOBS = Path(__file__).resolve().parents[2]
MODEL = "qwen3.5:4b"
USER = """Screen this job for the candidate in the brief (a new grad: MS CS, December 2026, about 1 year of internships).
Answer three questions about the job itself. Ignore pay, location and visa sponsorship: they are decided later.

1. fit: is this a role the candidate wants and fits? "yes" when the actual work matches one of the target
   archetypes (AI Engineer, Forward Deployed Engineer, Software Engineer) and draws on what the resume shows.
   "no" when it is a different job (sales, design, support) or its core stack or domain is one the resume
   never touches.
2. entry_level: "no" only when the JD is clearly written for experienced hires. Otherwise "yes".
3. citizen_only: "yes" only when the JD requires US citizenship, a security clearance, or being a "U.S. person".

Reply with only this JSON object:
{{"fit": "yes|no", "entry_level": "yes|no", "citizen_only": "yes|no",
 "score": <fit from 1.0 to 5.0, one decimal>, "reason": "<at most 25 words: the deciding facts from the JD>"}}

(Only "fit" decides here; code owns level and citizenship. Asking all three keeps the model's fit judgment
stricter: with "fit" alone it rated kernel and Next.js roles a 4.5 match, 2026-10-27.)

JOB DESCRIPTION:
{jd}"""


def rules(f):
    """Why code alone fails the job, or None."""
    if not f["engineering"]:
        return "not an engineering title"
    if f["citizen_only"]:
        return "US-citizen-only (citizenship, clearance or U.S. person)"
    if f["seniority_flag"]:
        return f"not entry level: title or URL says \"{f['seniority_flag']}\""
    if f["years_required"] and f["years_required"] >= 5:
        return f"not entry level: {f['years_required']}+ years required"
    if f["mid_level"]:
        return f"not entry level: mid-level duties ({', '.join(f['mid_level'])})"
    return None


def check(v):
    if not isinstance(v, dict) or not isinstance(v.get("score"), (int, float)):
        raise BadAnswer('expected {"fit": "yes|no", "score": number, "reason": string}')
    if str(v.get("fit")).lower() not in ("yes", "no"):
        raise BadAnswer('"fit" must be "yes" or "no"')
    if not 1.0 <= v["score"] <= 5.0:
        raise BadAnswer("score must be between 1.0 and 5.0")
    return {"fit": v["fit"].lower() == "yes", "score": round(float(v["score"]), 1),
            "reason": str(v.get("reason", ""))[:300]}


def screen(job, jd, client, brief):
    f = code_facts(job, jd)
    r = {"facts": f}
    fail = rules(f)
    if fail:
        r.update(score=None, verdict="FAIL", reason=f"Code: {fail}.", by="code")
        return r
    shown = "\n".join(line for line in jd.splitlines() if not (SPONSOR.search(line) and VISA.search(line)))
    m = client.ask(brief, USER.format(jd=shown[:7000]), check)
    r.update(model=m, score=m["score"], by="model", verdict="PASS" if m["fit"] else "FAIL",
             reason=("" if m["fit"] else "Model: not a fit. ") + m["reason"])
    return r


def capture_for(url):
    """A JD already saved in jds/ for this URL (the header carries 'URL: ...')."""
    for p in (JOBS / "jds").glob("*.md"):
        head = p.read_text()[:600]
        if f"URL: {url}\n" in head:
            return p.read_text().split("\n---\n", 1)[-1]
    return ""


def write_out(rows, meta, stem):
    rank = {"PASS": 0, "FAIL": 1, "ERROR": 2}
    order = sorted(rows, key=lambda x: (rank[x["result"].get("verdict", "ERROR")], -(x["result"].get("score") or 0)))
    L = [f"# Screen: {meta['started']}", "",
         f"Step 4 · model `{meta['model']}` · jobs: {len(rows)} · "
         f"PASS {sum(x['result'].get('verdict') == 'PASS' for x in rows)} · "
         f"FAIL {sum(x['result'].get('verdict') == 'FAIL' for x in rows)} · {meta['minutes']:.1f} min", "",
         "FAIL = US-citizen-only, not entry level (5+ years, senior), or not a fit. Every PASS goes to step 5 "
         "with its fit score; Claude picks.", "",
         "| Verdict | Fit | Company | Title | Years | Visa | Reason |", "|---|---|---|---|---|---|---|"]
    for x in order:
        j, r, f = x["job"], x["result"], x["result"].get("facts", {})
        L.append(f"| {r.get('verdict', 'ERROR')} | {r.get('score', '–')} | {j['company']} | [{j['title']}]({j['url']}) | "
                 f"{f.get('years_required') or '–'} | {f.get('sponsorship', '–')} | {r.get('reason', r.get('error', ''))} |")
    stem.with_suffix(".md").write_text("\n".join(L) + "\n")
    stem.with_suffix(".json").write_text(json.dumps({"meta": meta, "jobs": rows}, indent=1, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--listings", help="pipeline-format lines (default: data/pipeline.md)")
    a = ap.parse_args()

    src = Path(a.listings) if a.listings else JOBS / "data" / "pipeline.md"
    jobs = [parse_listing(line) for line in src.read_text().splitlines() if line.startswith("- [ ] ")]
    out_dir, day = JOBS / "listings", dt.date.today().isoformat()
    out_dir.mkdir(exist_ok=True)
    stem = out_dir / f"{day}-screen-{len(list(out_dir.glob(f'{day}-screen-*.md'))) + 1:02d}"
    brief = (JOBS / "modes" / "_brief.md").read_text()
    client = Bolt(a.model)
    meta = {"model": a.model, "started": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "minutes": 0}
    start, rows = time.time(), []
    print(f"{len(jobs)} jobs with {a.model} -> {stem}.md", flush=True)
    for n, job in enumerate(jobs, 1):
        t = time.time()
        jd = capture_for(job["url"]) or fetch_jd(job["url"])
        try:
            r = screen(job, jd, client, brief) if jd.strip() else {"error": "no JD", "verdict": "ERROR"}
        except Exception as e:
            r = {"error": f"{type(e).__name__}: {e}", "verdict": "ERROR"}
        r["secs"] = round(time.time() - t, 1)
        rows.append({"job": job, "result": r})
        print(f"{n}/{len(jobs)} {r['verdict']:8} {r.get('score', '–')!s:>4} {job['company']} | {job['title']} "
              f"({r['secs']}s) {r.get('reason', r.get('error', ''))[:90]}", flush=True)
        meta["minutes"] = (time.time() - start) / 60
        write_out(rows, meta, stem)
    subprocess.run(["ollama", "stop", a.model], capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
