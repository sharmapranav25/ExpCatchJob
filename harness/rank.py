"""Rank queued jobs with a small local model. Code decides; the model extracts.

Per job:
  0. code   JD text (jd.py) and facts (facts.py): engineering title, years bar,
            seniority words in title or URL, work model, sponsorship.
            A job that fails a gate gets its score here, with no model call.
  A. model  JD only -> the must-have requirements (short list of strings).
  B. model  resume + requirements -> for each: yes / partial / no, plus a quote
            copied from the resume. Code checks every quote against the resume;
            a quote that isn't there counts as "no".
  V. model  one short yes/no call per claimed match: does that resume line really
            show the requirement? Then a code floor: a requirement naming a skill
            the resume lists is at least "partial".
  -  code   score from the verified coverage, capped by the years bar and by
            mid-level duties (mentoring, leading projects).
  R. code   only for scores >= SUGGEST_AT: the skills to move forward and the
            verified bullets to lead with. With --reword the model also rewords
            up to 2 of those bullets, and code rejects a rewrite that adds a
            number, uses a banned word or changes the length a lot.

Output: listings/<date>-run-NN.md and .json, rewritten after every job.

Usage:
  python3 -I local/harness/rank.py                       # jobs in data/pipeline.md
  python3 -I local/harness/rank.py --listings F --jd-dir D   # frozen test set
"""

import argparse
import datetime as dt
import difflib
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bolt import BadAnswer, Bolt, MODEL  # noqa: E402
from facts import code_facts, parse_listing  # noqa: E402
from jd import fetch_jd  # noqa: E402

JOBS = Path(__file__).resolve().parents[2]
RESUME_DIR = Path(os.environ.get("RESUME_AGENT_DIR", "/absolute/path/to/resume agent"))

# About the candidate. needs_sponsorship: None = unknown, so sponsorship is shown but never scored.
CANDIDATE_YEARS = 1
NEEDS_SPONSORSHIP = None
SUGGEST_AT = 3.0
AGGREGATORS = re.compile(r"builtin[a-z]*\.com|linkedin\.com|indeed\.com|glassdoor\.com|wellfound\.com|ziprecruiter\.com")
BANNED = ["leverage", "utilize", "spearhead", "facilitate", "robust", "seamless", "cutting-edge",
          "state-of-the-art", "comprehensive", "innovative", "powerful", "significantly",
          "dramatically", "--", "—", "–"]

RESUME = (RESUME_DIR / "MASTER_RESUME.md").read_text()


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[*_`]", "", s)).strip().lower()


RESUME_NORM = norm(RESUME)


RESUME_LINES = [(norm(line), line.strip("- ").strip()) for line in RESUME.splitlines() if line.strip()]


def resume_terms():
    """Named skills: the **Skills:** lines plus each project's tech line (the line after its ### heading)."""
    lines, raw = [], RESUME.splitlines()
    for i, line in enumerate(raw):
        if re.match(r"\*\*[^*]+:\*\*", line):
            lines.append(line.split(":**", 1)[1])
        elif line.startswith("### ") and i + 1 < len(raw) and "," in raw[i + 1] and "|" not in raw[i + 1]:
            lines.append(raw[i + 1])
    terms = set()
    for line in lines:
        for t in re.split(r"[,()]", line):
            t = t.strip().lower()
            if 2 <= len(t) <= 40 and not re.search(r"\d+%|\bto\b", t):
                terms.add(t)
                if t.endswith("s") and len(t) > 4:
                    terms.add(t[:-1])  # "rest apis" also matches "REST API integrations"
    return terms


TERMS = resume_terms()


def term_hits(req):
    r = req.lower()
    return sorted(t for t in TERMS if re.search(rf"(?<![\w+#]){re.escape(t)}(?![\w+#])", r))


def quote_line(q):
    """The full resume line the quote came from, or None when it isn't in the resume.
    Small copying slips are allowed: 80% of the quote must appear verbatim."""
    q = norm(q)
    if len(q.split()) < 2:
        return None
    best, size = None, 0
    for n, raw in RESUME_LINES:
        if q in n:
            return raw
        m = difflib.SequenceMatcher(None, n, q, autojunk=False).find_longest_match(0, len(n), 0, len(q))
        if m.size > size:
            best, size = raw, m.size
    return best if size >= 0.8 * len(q) else None


# ---------- gates (code) ----------

def gate(f):
    """(score, reason) when code alone settles the job, else None. Also returns the score cap."""
    if not f["engineering"]:
        return (1.0, "Title is not an engineering role."), None
    if f["seniority_flag"]:
        return (1.8, f"Title or URL says \"{f['seniority_flag']}\"."), None
    y = f["years_required"]
    if y is not None and y >= 5:
        return (1.8, f"Asks for {y}+ years; the resume shows about {CANDIDATE_YEARS}."), None
    if y is not None and y >= 3:
        return (2.2, f"Asks for {y}+ years; the resume shows about {CANDIDATE_YEARS}."), None
    if NEEDS_SPONSORSHIP and f["sponsorship"] == "no-sponsorship":
        return (1.0, "The JD rules out visa sponsorship."), None
    cap = 3.5 if y == 2 else 5.0
    if f["mid_level"]:
        cap = min(cap, 3.0)
    return None, cap


# ---------- model calls ----------

SYS_A = """You read job descriptions and list what the job requires.
Reply with only a JSON array of strings. No prose."""

USER_A = """List the must-have requirements of this job: the specific technologies, tools, domains and kinds of hands-on work it asks for.
Rules:
- 4 to 8 items, each a short phrase (under 10 words), most important first.
- Each item must name something concrete: a language, tool, platform, domain, kind of system, or kind of work (for example "customer-facing deployments").
- Leave out generic abilities every engineer claims: ownership, end-to-end delivery, design, architecture, scoping, problem solving, leading, collaboration.
- Only requirements of the candidate. Leave out facts about the company, its product or its customers' industries.
- Leave out: years of experience, degrees, soft skills, location, travel, benefits.
- Merge near-duplicates. Use the job's own words.
Example of the shape (for a different job): ["Python backend services", "PostgreSQL schema design", "AWS deployment", "payments domain experience"]

JOB DESCRIPTION:
{jd}"""

SYS_B = """You compare one candidate's resume against a list of job requirements.
You only use what the resume says. Reply with only a JSON array. No prose.

CANDIDATE RESUME:
{resume}"""

USER_B = """For each requirement below, first find the best evidence in the resume, then judge it. Be strict.
- "yes": a project or job bullet shows the candidate doing this exact kind of work, or using this exact technology, tool or domain.
- "partial": the resume only lists it as a skill keyword, or shows closely related work (for example Python REST APIs for "Node.js APIs").
- "no": nothing in the resume shows it. Different work that merely sounds similar is "no".
"quote": copy a short exact phrase (5 to 15 words) from the resume that is the evidence, or "" for "no".
Reply with one object per requirement, in the same order:
[{{"req": "...", "quote": "...", "match": "yes|partial|no"}}]

REQUIREMENTS:
{reqs}"""

USER_R = """Rewrite this resume bullet so a reader sees it shows the job requirement "{req}".
Rules:
- Keep every fact and every number exactly as they are. Do not add any skill, tool, number, result or claim that is not already in the bullet.
- You may reorder the bullet and swap in the job's wording for the same thing.
- Plain words. No dashes. Never use: leverage, utilize, spearhead, robust, seamless, comprehensive, innovative.
- About the same length as the original.
Reply with only the rewritten bullet.

Bullet: {line}"""


def check_reqs(v):
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v) or not v:
        raise BadAnswer("expected a JSON array of non-empty strings")
    return [x.strip() for x in v][:8]


USER_V = """Job requirement: {req}
Resume line: {line}

Does this resume line itself show the candidate has this requirement? Similar-sounding but different work does not count.
Answer with one word: yes or no."""


def check_matches(reqs):
    def check(v):
        if not isinstance(v, list) or len(v) != len(reqs):
            raise BadAnswer(f"expected an array of exactly {len(reqs)} objects, one per requirement")
        out = []
        for req, x in zip(reqs, v):
            if not isinstance(x, dict) or x.get("match") not in ("yes", "partial", "no"):
                raise BadAnswer('each object needs "match" set to "yes", "partial" or "no"')
            quote = str(x.get("quote") or "")
            line = quote_line(quote) if x["match"] != "no" else None
            out.append({"req": req, "match": x["match"] if line else "no", "said": x["match"], "quote": quote,
                        "line": line, "verified": x["match"] == "no" or line is not None})
        return out
    return check


def verify(bolt, matches):
    """One short yes/no call per claimed match: does the quoted resume line really show it?"""
    for m in matches:
        if m["match"] == "no":
            continue
        word = bolt.say(USER_V.format(req=m["req"], line=m["line"])).strip().lower()
        m["relevant"] = word.startswith("yes")
        if not m["relevant"]:
            m["match"] = "no"
    # Code floor: a requirement that names a skill the resume lists is at least partial.
    for m in matches:
        m["terms"] = term_hits(m["req"])
        if m["match"] == "no" and m["terms"]:
            m["match"] = "partial"


def check_rewrite(original):
    nums = set(re.findall(r"\d+(?:\.\d+)?", original))

    def check(text):
        text = text.strip().strip('"').removeprefix("- ").strip()
        new_nums = set(re.findall(r"\d+(?:\.\d+)?", text)) - nums
        if new_nums:
            raise BadAnswer(f"it adds numbers that are not in the original: {', '.join(sorted(new_nums))}")
        bad = [w for w in BANNED if w in text.lower()]
        if bad:
            raise BadAnswer(f"it uses banned words or dashes: {', '.join(bad)}")
        if len(text) > 1.3 * len(original) or len(text) < 0.5 * len(original):
            raise BadAnswer("it is much longer or shorter than the original")
        return text
    return check


def section_of(line):
    """The ### heading (or ## section) a resume line sits under."""
    head = None
    for raw in RESUME.splitlines():
        if raw.startswith("#"):
            head = raw.strip("# ").split("|")[0].strip()
        if line in raw:
            return head
    return None


def suggest(bolt, matches, reword):
    """Resume changes for a strong match. Code picks what to move. With `reword`, the model rewords up to
    2 bullets and code checks them; off by default because Bolt's rewrites drop punctuation and read worse."""
    have = [m for m in matches if m["match"] != "no"]
    skills = list(dict.fromkeys(t for m in have for t in m.get("terms", [])))
    bullets, seen = [], set()
    for m in have:
        line = m.get("line")
        if m.get("relevant") and line and not line.startswith("**") and line not in seen:
            seen.add(line)
            bullets.append({"req": m["req"], "line": line, "section": section_of(line)})
    for b in bullets[:2] if reword else []:
        try:
            b["rewrite"] = bolt.ask(None, USER_R.format(req=b["req"], line=b["line"]), check_rewrite(b["line"]),
                                    parse=lambda s: s)
        except BadAnswer as e:
            b["rejected"] = str(e)
    return {"skills_first": skills, "lead_bullets": bullets[:3]}


# ---------- scoring (code) ----------

def score(matches, cap):
    n = len(matches)
    got = sum(1.0 if m["match"] == "yes" else 0.5 if m["match"] == "partial" else 0.0 for m in matches)
    return round(min(1.0 + 4.0 * got / n, cap), 1)


def evaluate(job, jd, bolt, reword=False):
    f = code_facts(job, jd)
    r = {"facts": f, "aggregator": bool(AGGREGATORS.search(job["url"]))}
    gated, cap = gate(f)
    if gated:
        r["score"], r["gate"] = gated
        return r
    jd_short = jd[:7000]
    reqs = bolt.ask(SYS_A, USER_A.format(jd=jd_short), check_reqs)
    matches = bolt.ask(SYS_B.format(resume=RESUME), USER_B.format(reqs=json.dumps(reqs, indent=1)),
                       check_matches(reqs))
    verify(bolt, matches)
    r["matches"] = matches
    r["score"] = score(matches, cap)
    r["cap"] = cap
    if r["score"] >= SUGGEST_AT:
        r["suggest"] = suggest(bolt, matches, reword)
    return r


# ---------- output ----------

def summary(r):
    if "gate" in r:
        return f"Code gate: {r['gate']}"
    if "error" in r:
        return f"Not evaluated: {r['error']}"
    f = r["facts"]
    yes = sum(m["match"] == "yes" for m in r["matches"])
    part = sum(m["match"] == "partial" for m in r["matches"])
    miss = [m["req"] for m in r["matches"] if m["match"] == "no"]
    s = f"Shows {yes} of {len(r['matches'])} must-haves in project or job work"
    s += f", {part} more only as a listed skill or adjacent work. " if part else ". "
    s += f"Missing: {', '.join(miss)}. " if miss else ""
    if f["years_required"]:
        s += f"Asks for {f['years_required']}+ years. "
    if f["mid_level"]:
        s += f"Mid-level duties in the JD ({', '.join(f['mid_level'])}), so capped at {r['cap']}. "
    if f["sponsorship"] != "none":
        s += f"Visa: {f['sponsorship']}."
    return s.strip()


def write_listing(jobs, meta, path):
    done = [j for j in jobs if "result" in j]
    order = sorted(done, key=lambda j: (-(j["result"].get("score") or 0), j["tier"]))
    L = [f"# Listings run: {meta['started']}", "",
         f"Model: `{meta['model']}` · jobs: {len(jobs)} · evaluated: {len(done)} · "
         f"model calls: {meta['calls']} · runtime so far: {meta['minutes']:.1f} min", "",
         "Code decides the facts, the gates, the score and which resume lines to move. The model lists "
         "requirements, matches them to the resume (every quote and match checked), and rewords bullets "
         "for strong matches (checked for new numbers and banned words). Visa status is shown, never scored.", "",
         "| Score | Role | Tier | Company | Title | Work model | Years asked | Visa | Note |",
         "|---|---|---|---|---|---|---|---|---|"]
    for j in order:
        r, f = j["result"], j["result"].get("facts", {})
        note = "code gate" if "gate" in r else ""
        if r.get("aggregator"):
            note = (note + ", " if note else "") + "unconfirmed at employer"
        if f.get("hidden_employer"):
            note = (note + ", " if note else "") + "employer hidden (agency post)"
        L.append(f"| {r.get('score', '–')} | {j['role']} | {j['tier']} | {j['company']} | [{j['title']}]({j['url']}) | "
                 f"{f.get('work_model', '–')} | {f.get('years_required') or '–'} | {f.get('sponsorship', '–')} | {note} |")
    L.append("")
    for j in order:
        r = j["result"]
        L += [f"## {r.get('score', '?')} · {j['company']}: {j['title']}",
              f"{j['role']} · Tier {j['tier']} · {j['location']} · {j['salary'] or 'no salary listed'} · "
              f"posted {j['posted'] or '?'} · [posting]({j['url']})", "", f"**Verdict:** {summary(r)}", ""]
        if r.get("aggregator"):
            L += ["> Found on a job aggregator. Confirm the role on the employer's own careers page before applying.", ""]
        if r.get("matches"):
            L.append("**Requirements vs resume:**")
            for m in r["matches"]:
                mark = {"yes": "✅", "partial": "🟡", "no": "❌"}[m["match"]]
                extra = f' "{m["quote"]}"' if m["match"] != "no" else ""
                if m["match"] == "partial" and m.get("terms") and not m.get("relevant"):
                    extra = f" (resume lists {', '.join(m['terms'])})"
                elif not m["verified"]:
                    extra = f" (model said {m['said']}, but its quote isn't in the resume)"
                elif m.get("relevant") is False:
                    extra = f' (model cited "{m["quote"]}", but the check found that line doesn\'t show it)'
                L.append(f"- {mark} {m['req']}{extra}")
            L.append("")
        if "suggest" in r:
            s = r["suggest"]
            L.append("**Suggested resume changes** (reorder and reword only, nothing new):")
            if s["skills_first"]:
                L.append(f"- Move to the front of their skills lines: {', '.join(s['skills_first'])}.")
            for b in s["lead_bullets"]:
                L.append(f"- Lead **{b['section']}** with the bullet that shows \"{b['req']}\": _{b['line'][:90]}…_")
                if b.get("rewrite"):
                    L.append(f"  - Reworded toward the JD: {b['rewrite']}")
                elif b.get("rejected"):
                    L.append(f"  - (Reword dropped: {b['rejected']})")
            L.append("")
    path.write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--listings", help="file of pipeline.md lines (default: data/pipeline.md)")
    ap.add_argument("--jd-dir", help="folder of NN.txt JDs in listing order, instead of fetching")
    ap.add_argument("--out", help="output path stem (default: listings/<date>-run-NN)")
    ap.add_argument("--reword", action="store_true", help="also have the model reword the lead bullets")
    a = ap.parse_args()

    src = Path(a.listings) if a.listings else JOBS / "data" / "pipeline.md"
    jobs = [parse_listing(line) for line in src.read_text().splitlines() if line.startswith("- [ ] ")]
    if a.out:
        stem = Path(a.out)
    else:
        out_dir, day = JOBS / "listings", dt.date.today().isoformat()
        out_dir.mkdir(exist_ok=True)
        stem = out_dir / f"{day}-run-{len(list(out_dir.glob(f'{day}-run-*.md'))) + 1:02d}"
    md, js = stem.with_suffix(".md"), stem.with_suffix(".json")

    bolt = Bolt(a.model)
    start = time.time()
    meta = {"model": a.model, "started": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "minutes": 0, "calls": 0}
    log = lambda m: print(f"[{dt.datetime.now():%H:%M:%S}] {m}", flush=True)
    log(f"{len(jobs)} jobs with {a.model} -> {md}")
    for n, j in enumerate(jobs, 1):
        t, before = time.time(), len(bolt.calls)
        jd = (Path(a.jd_dir) / f"{n:02d}.txt").read_text() if a.jd_dir else fetch_jd(j["url"])
        if not jd.strip():
            j["result"] = {"error": "no JD from the ATS APIs or the page's structured data", "score": None}
        else:
            try:
                j["result"] = evaluate(j, jd, bolt, a.reword)
            except Exception as e:
                j["result"] = {"error": f"{type(e).__name__}: {e}", "score": None}
        j["result"]["calls"] = bolt.calls[before:]
        j["result"]["secs"] = round(time.time() - t, 1)
        r = j["result"]
        log(f"{n}/{len(jobs)} {j['company']} | {j['title']}: {r.get('score')} "
            f"({len(r['calls'])} calls, {r['secs']}s){' ' + r['error'] if 'error' in r else ''}")
        meta["minutes"], meta["calls"] = (time.time() - start) / 60, len(bolt.calls)
        js.write_text(json.dumps({"meta": meta, "jobs": jobs}, indent=1, ensure_ascii=False))
        write_listing(jobs, meta, md)
    log(f"done in {meta['minutes']:.1f} min -> {md}")


if __name__ == "__main__":
    sys.exit(main())
