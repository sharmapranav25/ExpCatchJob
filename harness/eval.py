"""Compare harness runs on the frozen test set with Claude's reference (testset/gold.json).

Usage: python3 -I local/harness/eval.py [--gold GOLD.json] RUN.json [RUN2.json]
(a second run checks repeatability; the default gold is the tuning set, local/heldout/gold.json is the held-out one)
"""

import json
import sys
from pathlib import Path

GOLD = Path(__file__).resolve().parents[1] / "testset" / "gold.json"


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else 0.0


def main():
    args = sys.argv[1:]
    gold_path = GOLD
    if args[:1] == ["--gold"]:
        gold_path, args = Path(args[1]), args[2:]
    gold = json.load(open(gold_path))["jobs"]
    runs = [json.load(open(p))["jobs"] for p in args]
    run = runs[0]
    print(f"{'#':>2} {'company':16} {'gold':>4} {'harness':>7} {'diff':>5}  {'calls':>5} {'secs':>5}  quotes  note")
    g_scores, h_scores, facts_ok, facts_n, q_ok, q_n = [], [], 0, 0, 0, 0
    for g, j in zip(gold, run):
        r = j["result"]
        s = r.get("score")
        f = r.get("facts", {})
        for key, want in (("engineering", g["function"] == "engineering"), ("years_required", g["years_required"]),
                          ("sponsorship", g["sponsorship"]), ("work_model", g["work_model"])):
            facts_n += 1
            facts_ok += f.get(key) == want
        ms = [m for m in r.get("matches", []) if m["said"] != "no"]
        q_n += len(ms)
        q_ok += sum(m["verified"] for m in ms)
        note = r.get("gate") or r.get("error") or ""
        g_scores.append(g["score"])
        h_scores.append(s if s is not None else 0)
        print(f"{g['n']:>2} {g['company'][:16]:16} {g['score']:>4} {s!s:>7} {(s or 0) - g['score']:>+5.1f}  "
              f"{len(r.get('calls', [])):>5} {r.get('secs', 0):>5}  {sum(m['verified'] for m in ms)}/{len(ms):<4}  {note[:60]}")

    top = lambda xs: {i for i in sorted(range(len(xs)), key=lambda i: -xs[i])[:4]}
    gt, ht = top(g_scores), top(h_scores)
    calls = [c for j in run for c in j["result"].get("calls", [])]
    model_jobs = [j for j in run if j["result"].get("calls")]
    print()
    print(f"code facts correct:        {facts_ok}/{facts_n}")
    print(f"rank agreement (Spearman): {spearman(g_scores, h_scores):.2f}")
    print(f"top-4 overlap with gold:   {len(gt & ht)}/4  gold={sorted(i + 1 for i in gt)} harness={sorted(i + 1 for i in ht)}")
    print(f"mean abs score error:      {sum(abs(a - b) for a, b in zip(g_scores, h_scores)) / len(gold):.2f}")
    print(f"apply calls (>=4.0) agree: {sum((a >= 4) == (b >= 4) for a, b in zip(g_scores, h_scores))}/{len(gold)}")
    print(f"model quotes verified:     {q_ok}/{q_n}")
    print(f"model calls:               {len(calls)} over {len(model_jobs)} jobs "
          f"({sum(c['in'] for c in calls)} tok in, {sum(c['out'] for c in calls)} out)")
    print(f"time:                      {sum(j['result'].get('secs', 0) for j in run):.0f}s total, "
          f"{sum(j['result'].get('secs', 0) for j in model_jobs) / max(len(model_jobs), 1):.0f}s per model-scored job")
    if len(runs) > 1:
        same = sum(a["result"].get("score") == b["result"].get("score") for a, b in zip(runs[0], runs[1]))
        print(f"repeat run, same score:    {same}/{len(gold)}")


if __name__ == "__main__":
    main()
