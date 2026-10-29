"""Test the step-4 screener (screen.py) against Claude's verdicts on every job scored so far.

Sets: testset (15, used to tune the old Bolt harness), heldout (10), today's run 2 (7, local/screen/gold.json).
Should PASS: Claude scored it 3.0 or higher and it asks for under 3 years (the step-4 rules: fit, entry level,
not citizen-only). Everything else should FAIL.
What matters most for a screen: no good job may FAIL; a weak job that PASSes only costs one Claude evaluation.

Usage: python3 -I local/harness/eval_screen.py [--model qwen3.5:4b]
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bolt import Bolt  # noqa: E402
from facts import parse_listing  # noqa: E402
from screen import JOBS, MODEL, screen  # noqa: E402

LOCAL = JOBS / "local"


def cases():
    for name in ("testset", "heldout"):
        gold = json.load(open(LOCAL / name / "gold.json"))["jobs"]
        lines = (LOCAL / name / "listings.txt").read_text().splitlines()
        for g, line in zip(gold, lines):
            want = "PASS" if g["score"] >= 3.0 and (g["years_required"] or 0) < 3 else "FAIL"
            yield name, parse_listing(line), (LOCAL / name / "jds" / f"{g['n']:02d}.txt").read_text(), want, g["score"]
    for g in json.load(open(LOCAL / "screen" / "gold.json"))["jobs"]:
        text = (JOBS / "jds" / g["file"]).read_text()
        head = dict(line.split(": ", 1) for line in text.splitlines()[2:6] if ": " in line)
        title = text.splitlines()[0].lstrip("# ").rsplit(" — ", 1)[0]
        job = parse_listing(f"- [ ] {head['URL']} | {g['company']} | {title} | {head['Location']}")
        want = "PASS" if g["verdict"] == "PASS" else "FAIL"  # Didero (MARGINAL) asks 3+ years
        yield "run2", job, text.split("\n---\n", 1)[1], want, g["score"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    a = ap.parse_args()
    brief = (JOBS / "modes" / "_brief.md").read_text()
    client = Bolt(a.model)
    rows, start = [], time.time()
    for name, job, jd, want, gold_score in cases():
        t = time.time()
        r = screen(job, jd, client, brief)
        x = {"set": name, "company": job["company"], "title": job["title"], "want": want, "gold": gold_score, **r,
             "secs": round(time.time() - t, 1)}
        rows.append(x)
        mark = "✓" if r["verdict"] == want else ("✗✗" if want == "PASS" else "✗")
        print(f"{mark:2} {name:7} {job['company'][:18]:18} {job['title'][:40]:40} want {want:4} got {r['verdict']:4} "
              f"{r['score']!s:>4} {r['by']:5} {x['secs']:>5}s  {r['reason'][:80]}", flush=True)
    subprocess.run(["ollama", "stop", a.model], capture_output=True)
    json.dump(rows, open(LOCAL / "screen" / f"eval-{a.model.replace(':', '_')}.json", "w"), indent=1)

    good = [x for x in rows if x["want"] == "PASS"]
    weak = [x for x in rows if x["want"] == "FAIL"]
    model_rows = [x for x in rows if x["by"] == "model"]
    print(f"\nverdict match:                {sum(x['verdict'] == x['want'] for x in rows)}/{len(rows)}")
    print(f"good jobs kept:               {sum(x['verdict'] == 'PASS' for x in good)}/{len(good)}   "
          f"wrongly failed: {[x['company'] for x in good if x['verdict'] == 'FAIL'] or 'none'}")
    print(f"weak jobs let through:        {sum(x['verdict'] == 'PASS' for x in weak)}/{len(weak)}   "
          f"{[x['company'] for x in weak if x['verdict'] == 'PASS'] or ''}")
    passed = sorted((x for x in rows if x["verdict"] == "PASS"), key=lambda x: -x["score"])
    print("passed, by fit score (Claude's score):  " + ", ".join(f"{x['company']} {x['score']} ({x['gold']})" for x in passed))
    print(f"decided by code / by model:   {len(rows) - len(model_rows)} / {len(model_rows)}")
    print(f"time: {time.time() - start:.0f}s total, "
          f"{sum(x['secs'] for x in model_rows) / max(len(model_rows), 1):.1f}s per model-screened job")


if __name__ == "__main__":
    main()
