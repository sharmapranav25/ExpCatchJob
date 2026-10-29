"""Step 4 as a decision tree of single yes/no questions, one small-model call per question.

Each node asks qwen3.5:4b exactly one question about one JD and expects one word back. A job PASSes only
if every node answers the "pass" way. The JD goes in the system message and the question in the user
message, so a job's calls share a prompt prefix.

  coding       Is this a hands-on software engineering role?            no  -> FAIL
  citizen      Does it require US citizenship / clearance / U.S. person? yes -> FAIL
  years3       Does it require 3 or more years of experience?            yes -> FAIL
  experienced  Is it clearly written for experienced hires?              yes -> FAIL
  archetype    Is the main work one of the three target roles?           no  -> FAIL
  stack        Is the core required stack mostly in the candidate's skills? no -> FAIL

Usage: python3 -I local/harness/tree.py --test     (all nodes on the 32 scored jobs, vs local/screen/node_gold.json)
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bolt import Bolt  # noqa: E402

JOBS = Path(__file__).resolve().parents[2]
MODEL = "qwen3.5:4b"


def candidate_skills():
    cv = (JOBS / "cv.md").read_text()
    skills = cv.split("## Technical Skills", 1)[1]
    raw = cv.splitlines()
    tech = [raw[i + 1] for i, line in enumerate(raw[:-1]) if line.startswith("### ") and "," in raw[i + 1]]
    return re.sub(r"\*\*", "", skills).strip() + "\nAlso used in projects: " + "; ".join(tech)


SKILLS = candidate_skills()

# (name, question, the answer that FAILs the job)
NODES = [
    ("coding", "Is this a hands-on software engineering role, where the person hired writes code as a main part "
               "of the job? Sales, design, recruiting and pure customer-success roles are not.", "no"),
    ("citizen", "Does this job require US citizenship, a US security clearance, or being a \"U.S. person\"? "
                "Needing work authorization, or \"no visa sponsorship\", does NOT count. Answer yes only if the "
                "text says so explicitly.", "yes"),
    ("years3", "Does this job require 3 or more years of professional experience? Count only required "
               "qualifications, not preferred or nice-to-have ones. Answer yes only if the text states it.", "yes"),
    ("experienced", "Is this job clearly written for an experienced hire rather than someone early in their "
                    "career? Signs: senior or staff scope, leading or mentoring other engineers, setting technical "
                    "direction. A job that just says \"own projects end to end\" is not enough.", "yes"),
    ("archetype", "Is the main work of this job one of these: (a) building AI, ML or LLM systems; (b) a forward "
                  "deployed engineer who builds and deploys software for customers; (c) backend or ML-adjacent "
                  "software engineering? Frontend-only, mobile-only, kernel, networking, storage-systems and "
                  "SRE/IT support work are not.", "no"),
    ("stack", "Here are the candidate's skills:\n" + SKILLS + "\n\nAre the job's core REQUIRED technologies "
              "mostly covered by these skills? Ignore nice-to-haves. Answer no if the core of the job is a "
              "language, platform or domain the candidate has not used.", "no"),
]

SYSTEM = "You read one job description and answer one question about it with a single word: yes or no.\n\nJOB DESCRIPTION:\n{jd}"


def ask(client, jd, question):
    out = client._chat([{"role": "system", "content": SYSTEM.format(jd=jd[:7000])},
                        {"role": "user", "content": question + "\n\nAnswer with one word: yes or no."}])
    word = re.sub(r"[^a-z]", " ", out.lower()).split()
    return word[0] if word and word[0] in ("yes", "no") else ("yes" if "yes" in word else "no")


def run_tree(jd, client, stop_early=True):
    answers = {}
    for name, question, fail_on in NODES:
        answers[name] = ask(client, jd, question)
        if stop_early and answers[name] == fail_on:
            return "FAIL", name, answers
    failed = [n for n, _, f in NODES if answers.get(n) == f]
    return ("FAIL", failed[0], answers) if failed else ("PASS", None, answers)


def test():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from eval_screen import cases
    labels = json.load(open(JOBS / "local" / "screen" / "node_gold.json"))["labels"]
    client = Bolt(MODEL)
    subprocess.run(["ollama", "stop", MODEL], capture_output=True)
    rows, start = [], time.time()
    for (name, job, jd, want, gold), lab in zip(cases(), labels):
        t, before = time.time(), len(client.calls)
        verdict, failed_at, answers = run_tree(jd, client, stop_early=False)
        secs = round(time.time() - t, 1)
        rows.append({"company": lab["company"], "want": want, "verdict": verdict, "failed_at": failed_at,
                     "answers": answers, "labels": lab, "secs": secs, "calls": client.calls[before:]})
        wrong = [n for n, *_ in NODES if lab[n] is not None and (answers[n] == "yes") != lab[n]]
        mark = "✓" if verdict == want else ("✗✗" if want == "PASS" else "✗")
        print(f"{mark:2} {lab['company'][:18]:18} want {want:4} got {verdict:4} at {failed_at or '-':11} "
              f"{secs:>5}s  wrong nodes: {', '.join(wrong) or '-'}", flush=True)
    subprocess.run(["ollama", "stop", MODEL], capture_output=True)
    json.dump(rows, open(JOBS / "local" / "screen" / "tree-test.json", "w"), indent=1)

    print("\nper-question accuracy (vs Claude's answers; ambiguous ones left out):")
    for n, *_ in NODES:
        scored = [r for r in rows if r["labels"][n] is not None]
        ok = sum((r["answers"][n] == "yes") == r["labels"][n] for r in scored)
        print(f"  {n:12} {ok}/{len(scored)}")
    good = [r for r in rows if r["want"] == "PASS"]
    weak = [r for r in rows if r["want"] == "FAIL"]
    print(f"\nwhole tree: match {sum(r['verdict'] == r['want'] for r in rows)}/{len(rows)}, "
          f"good jobs kept {sum(r['verdict'] == 'PASS' for r in good)}/{len(good)} "
          f"(wrongly failed: {[(r['company'], r['failed_at']) for r in good if r['verdict'] == 'FAIL'] or 'none'}), "
          f"weak let through {sum(r['verdict'] == 'PASS' for r in weak)}/{len(weak)} "
          f"{[r['company'] for r in weak if r['verdict'] == 'PASS'] or ''}")
    calls = [c for r in rows for c in r["calls"]]
    print(f"time: {time.time() - start:.0f}s for {len(calls)} calls, {sum(c['secs'] for c in calls) / len(calls):.1f}s per call, "
          f"{sum(r['secs'] for r in rows) / len(rows):.0f}s per job with all 6 questions")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    if ap.parse_args().test:
        test()
