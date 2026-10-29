"""Step 5 test: Claude Sonnet scores the jobs Haiku passed in step 4, one call each, via `claude -p` (subscription).

Sonnet gets the candidate's full context (cv.md, article-digest.md, modes/_profile.md) and the JD, and returns a
1 to 5 score, apply yes/no, matches and gaps. Compared with Claude's reference scores (testset, heldout, run 2).
This checks judgment only; the production step 5 can be career-ops' oferta mode, which also writes the report.

Usage: python3 -I local/harness/sonnet_eval.py --test
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
from eval import spearman  # noqa: E402
from haiku_screen import CWD  # noqa: E402

JOBS = Path(__file__).resolve().parents[2]

SYSTEM = """You evaluate job postings for one candidate and answer with one JSON object and nothing else.
Use only facts from the candidate files below. Never invent experience.

=== modes/_profile.md (targeting, location policy, scoring notes) ===
{profile}

=== cv.md ===
{cv}

=== article-digest.md (proof points and what not to claim) ===
{digest}"""

USER = """Evaluate this job for the candidate. It already passed a screen (engineering role, entry level,
not citizen-only). Now judge fit in depth:
- skills and stack: how much of the core required work the candidate has actually done, by the resume
- level: whether a December 2026 MS new grad with about a year of internships is credible for it
- the target archetypes, comp against the $110K floor, location and work model, per the profile
- visa: a JD that never sponsors gets a flag in the reason, not a score penalty. If an H-1B history line is
  appended after the job, mention it in the reason only when it matters (a strong sponsor, or none); it never
  changes the score

Score 1.0 to 5.0 (one decimal). 4.0+ means worth applying; below 3.0 means don't.

Reply with only:
{{"score": <number>, "apply": "yes|no",
 "matches": ["up to 3 things the job needs that the resume clearly shows"],
 "gaps": ["up to 3 required things the resume does not show"],
 "reason": "<at most 30 words>"}}

JOB:
{jd}"""


def evaluate(jd, system):
    cmd = ["claude", "-p", "--model", "sonnet", "--tools", "", "--setting-sources", "", "--no-session-persistence",
           "--system-prompt", system, "--output-format", "json"]
    t = time.time()
    p = subprocess.run(cmd, input=USER.format(jd=jd[:12000]), capture_output=True, text=True, cwd=CWD, timeout=300)
    out = json.loads(p.stdout)
    a = parse_json(out["result"])
    return {**a, "secs": round(time.time() - t, 1), "cost_list_usd": out.get("total_cost_usd"),
            # Claude Code also makes a tiny Haiku side call; the evaluating model is the one that wrote the most.
            "model": max(out.get("modelUsage", {"?": {"outputTokens": 0}}).items(), key=lambda kv: kv[1]["outputTokens"])[0]}


def test():
    from eval_screen import cases
    system = SYSTEM.format(profile=(JOBS / "modes" / "_profile.md").read_text(), cv=(JOBS / "cv.md").read_text(),
                           digest=(JOBS / "article-digest.md").read_text())
    passed = {(x["company"], x["title"]) for x in json.load(open(JOBS / "local" / "screen" / "haiku-test.json"))
              if x["verdict"] == "PASS"}
    items = [c for c in cases() if (c[1]["company"], c[1]["title"]) in passed]
    start = time.time()
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda c: evaluate(c[2], system), items))
    rows = []
    for (name, job, jd, want, gold), r in zip(items, results):
        rows.append({"company": job["company"], "title": job["title"], "gold": gold, **r})
    rows.sort(key=lambda x: -x["score"])
    print(f"{'company':18} {'title':40} {'Sonnet':>6} {'Claude':>6}  apply  reason")
    for x in rows:
        print(f"{x['company'][:18]:18} {x['title'][:40]:40} {x['score']:>6} {x['gold']:>6}  {x['apply']:5}  {x['reason'][:90]}")
        print(f"{'':66}gaps: {'; '.join(x['gaps'])[:110]}")
    json.dump(rows, open(JOBS / "local" / "screen" / "sonnet-test.json", "w"), indent=1)

    g, s = [x["gold"] for x in rows], [x["score"] for x in rows]
    print(f"\nrank agreement with Claude (Spearman): {spearman(g, s):.2f}")
    print(f"mean abs score gap: {sum(abs(a - b) for a, b in zip(g, s)) / len(rows):.2f}")
    print(f"apply calls agree (Claude >= 4.0 vs Sonnet apply=yes): "
          f"{sum((a >= 4.0) == (x['apply'] == 'yes') for a, x in zip(g, rows))}/{len(rows)}")
    print(f"weak jobs Haiku let through, now: " + ", ".join(f"{x['company']} {x['score']} apply={x['apply']}"
                                                           for x in rows if x["gold"] < 3.0))
    print(f"model: {rows[0]['model']} · {time.time() - start:.0f}s wall for {len(rows)} jobs (4 at a time) · "
          f"{sum(x['secs'] for x in rows) / len(rows):.0f}s per call · list-price equivalent "
          f"${sum(x['cost_list_usd'] or 0 for x in rows):.2f} total (covered by the subscription)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    if ap.parse_args().test:
        test()
