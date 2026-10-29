"""Step 4 with Claude Haiku: one call per job, through the Claude Code subscription (`claude -p`, no API key).

Haiku answers three questions in one JSON reply; the job PASSes only if all three are fine:
  role_fit      a role the candidate wants (one of the three archetypes, not an excluded kind of work)
  entry_level   not 5+ years required, not clearly written for experienced hires (3 or 4 years is a soft flag
                for Sonnet to weigh in modes/_profile.md, not a reason to fail here)
  citizen_only  requires US citizenship, a clearance or being a "U.S. person"
Skill and stack fit is left to step 5 (Sonnet).

Usage: python3 -I local/harness/haiku_screen.py --test   (all 32 scored jobs, vs Claude's verdicts)
"""

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bolt import parse_json  # noqa: E402

JOBS = Path(__file__).resolve().parents[2]
# An empty folder outside the repo, so the headless session doesn't pick up this project's CLAUDE.md / AGENTS.md.
CWD = Path.home() / ".job-agent" / "claude-cwd"
CWD.mkdir(parents=True, exist_ok=True)

SYSTEM = """You screen job postings for one candidate. You answer with one JSON object and nothing else.

CANDIDATE BRIEF:
{brief}"""

USER = """Screen this job. Answer three questions about the job itself. Do not judge pay, location, visa
sponsorship or how well the candidate's specific skills match: those are decided later.

1. role_fit: true if this is a role the candidate wants: a hands-on engineering role whose main work is one of
   the target archetypes in the brief (AI Engineer, Forward Deployed Engineer, Software Engineer). false for
   other kinds of jobs (sales, design, support, recruiting) and for work the brief excludes (kernel,
   hypervisor or packet-processing systems work, frontend-only, mobile-only).
2. entry_level: false if the REQUIRED qualifications ask for 5 or more years of experience (ignore preferred or
   nice-to-have items), or if the job is clearly written for an experienced hire (senior or staff scope,
   mentoring or leading other engineers). Asking for 1, 2, 3 or 4 years is fine, even though 3 or 4 is a real
   gap for this candidate, because that gets weighed as a score, not a screen-out, later. Otherwise true.
3. citizen_only: true only if the text explicitly requires US citizenship, a US security clearance, or being a
   "U.S. person". Needing work authorization or "no visa sponsorship" does NOT count.

Reply with only:
{{"role_fit": true|false, "entry_level": true|false, "citizen_only": true|false,
 "reason": "<at most 25 words: the deciding facts from the job>"}}

JOB:
{jd}"""


def screen(jd, brief):
    cmd = ["claude", "-p", "--model", "haiku", "--tools", "", "--setting-sources", "", "--no-session-persistence",
           "--system-prompt", SYSTEM.format(brief=brief), "--output-format", "json"]
    t = time.time()
    p = subprocess.run(cmd, input=USER.format(jd=jd[:12000]), capture_output=True, text=True, cwd=CWD, timeout=180)
    out = json.loads(p.stdout)
    a = parse_json(out["result"])
    ok = a["role_fit"] and a["entry_level"] and not a["citizen_only"]
    why = [w for bad, w in ((not a["role_fit"], "not a role you want"), (not a["entry_level"], "not entry level"),
                            (a["citizen_only"], "US-citizen-only")) if bad]
    return {"verdict": "PASS" if ok else "FAIL", "why": why, "answers": a, "secs": round(time.time() - t, 1),
            "cost_list_usd": out.get("total_cost_usd"),
            "model": max(out.get("modelUsage", {"?": {"outputTokens": 0}}).items(), key=lambda kv: kv[1]["outputTokens"])[0]}


def test():
    from eval_screen import cases
    from facts import code_facts
    brief = (JOBS / "modes" / "_brief.md").read_text()
    items = list(cases())
    start = time.time()
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda c: screen(c[2], brief), items))
    rows = []
    for (name, job, jd, want, gold), r in zip(items, results):
        f = code_facts(job, jd)
        code = [k for k, bad in (("not engineering", not f["engineering"]), ("citizen", f["citizen_only"]),
                                 ("senior title", f["seniority_flag"]), ("3+ yrs", (f["years_required"] or 0) >= 3),
                                 ("mid-level duties", bool(f["mid_level"]))) if bad]
        rows.append({"company": job["company"], "title": job["title"], "want": want, "gold": gold, "code": code, **r})
        mark = "✓" if r["verdict"] == want else ("✗✗" if want == "PASS" else "✗")
        print(f"{mark:2} {job['company'][:18]:18} {job['title'][:38]:38} want {want:4} got {r['verdict']:4} "
              f"{r['secs']:>5}s  {', '.join(r['why']) or '-':22} {r['answers'].get('reason', '')[:80]}")
    json.dump(rows, open(JOBS / "local" / "screen" / "haiku-test.json", "w"), indent=1)

    def report(label, verdict):
        good = [x for x in rows if x["want"] == "PASS"]
        weak = [x for x in rows if x["want"] == "FAIL"]
        print(f"\n{label}: match {sum(verdict(x) == x['want'] for x in rows)}/{len(rows)}, "
              f"good kept {sum(verdict(x) == 'PASS' for x in good)}/{len(good)} "
              f"{[x['company'] for x in good if verdict(x) == 'FAIL'] or ''}, "
              f"weak let through {sum(verdict(x) == 'PASS' for x in weak)}/{len(weak)} "
              f"{[x['company'] for x in weak if verdict(x) == 'PASS'] or ''}")
    report("Haiku alone", lambda x: x["verdict"])
    report("code checks, then Haiku", lambda x: "FAIL" if x["code"] else x["verdict"])
    print(f"\nmodel: {rows[0]['model']} · {time.time() - start:.0f}s wall for {len(rows)} jobs (4 at a time) · "
          f"{sum(x['secs'] for x in rows) / len(rows):.1f}s per call · list-price equivalent "
          f"${sum(x['cost_list_usd'] or 0 for x in rows):.3f} total (covered by the subscription)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    if ap.parse_args().test:
        test()
