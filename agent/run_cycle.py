"""Local-only job agent. Ollama evidence review; final DOCX polish is manual."""
import datetime as dt
import hashlib
import shlex
import fcntl
import json
import re
import subprocess
import sys
import time
import traceback
import tempfile
import os
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
JOBS = HERE.parents[1]
sys.path.insert(0, str(JOBS / "local" / "harness"))
sys.path.insert(0, str(HERE))
from facts import parse_listing  # noqa: E402
from local_evaluator import evaluate, write_report, atomic_json
from resume_builder import build as build_draft
from jd import fetch_jd, fetch_jd_result  # noqa: E402
from jobspy_jd import read_handoff, merge_discovery, description as jobspy_description

from screening import screen, VERSION as CODE_SCREEN_VERSION
from entry_screen import classify as entry_classify, VERSION as ENTRY_VERSION
SCREEN_VERSION = CODE_SCREEN_VERSION + "+" + ENTRY_VERSION
TERMINAL = ("local_ready", "rejected_code", "rejected_screen", "needs_review")
from sponsorship import lookup as h1b_lookup, describe as h1b_description
from status import slug  # noqa: E402

STATE = HERE / "state.json"
NOTIFY_FROM, NOTIFY_UNTIL = 6, 24
MAX_TRIES = 3
CURRENT_CYCLE = None


def log(msg):
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def notify(title, subtitle, message, execute=None, group=None):
    cmd = ["terminal-notifier", "-title", title, "-subtitle", subtitle, "-message", message, "-sound", "default"]
    if execute:
        cmd += ["-execute", execute]
    if group:
        cmd += ["-group", group]
    subprocess.run(cmd, capture_output=True)


def boot_time():
    out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout
    m = re.search(r"sec = (\d+)", out)
    return m.group(1) if m else ""


def scan():
    global CURRENT_CYCLE
    CURRENT_CYCLE = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="jobspy-discovery-") as directory:
        env = {**os.environ, "JOBSPY_HANDOFF_DIR":directory, "JOBSPY_CYCLE":CURRENT_CYCLE}
        p = subprocess.run(["node", "scan.mjs", "--quiet", "--json"], cwd=JOBS, capture_output=True, text=True, timeout=1800, env=env)
        fresh = read_handoff(directory, CURRENT_CYCLE)
    for line in p.stderr.splitlines():
        if line.startswith(("[jobspy]", "[jobspy-info]")):
            log(line)
    try:
        receipt = json.loads(p.stdout)
    except ValueError:
        return {"added": 0, "errors": [{"company": "scan", "error": (p.stderr or "no output")[-200:]}]}
    return {"jobspy_jobs": fresh, "added": receipt.get("added", 0), "errors": receipt.get("errors", []) + [{"company": "JobSpy", "error": line[len("[jobspy] "):]} for line in p.stderr.splitlines() if line.startswith("[jobspy] ")]}


def liveness(urls):
    if not urls:
        return {}
    p = subprocess.run(["node", "check-liveness.mjs", *urls], cwd=JOBS, capture_output=True, text=True, timeout=900)
    status = {}
    for line in p.stdout.splitlines():
        for u in urls:
            if line.rstrip().endswith(u):
                status[u] = "expired" if "expired" in line else "uncertain" if "uncertain" in line else "active"
    return status


def save_jd(job, jd):
    path = JOBS / "jds" / f"{slug(job['company'])}-{hashlib.sha256(job['url'].encode()).hexdigest()[:12]}.md"
    path.write_text(f"# {job['title']} — {job['company']}\n\nURL: {job['url']}\nLocation: {job['location']}\n"
                        f"Salary (listing): {job['salary'] or '—'}\nPosted: {job['posted']}\n"
                        f"Captured: {dt.date.today()}\n\n---\n\n{jd}\n")
    return str(path.relative_to(JOBS))


def capture_for(url):
    for p in (JOBS / "jds").glob("*.md"):
        text = p.read_text()
        if f"URL: {url}\n" in text[:600]:
            return text.split("\n---\n", 1)[-1], str(p.relative_to(JOBS))
    return "", None


def process(job, alive):
    r = {**job, "at": dt.datetime.now().isoformat(timespec="minutes"), "availability": alive,
         "apply_url": job["url"], "notified": False}
    r.pop("resume_path", None)
    jd, jd_source = jobspy_description(job, CURRENT_CYCLE)
    if not jd.strip():
        jd = fetch_jd(job["url"])
        if jd.strip():
            jd_source = {"method":"ats-or-builtin", "source_url":job["url"]}
    if not jd.strip():
        return {**r, "status": "no_jd", "reason":"JD retrieval failed: " + jd_source.get("error", "No description returned")}
    r["jd_source"] = jd_source
    jd_file = save_jd(job, jd)
    r["jd_file"] = jd_file
    r["screening"] = screen(job["title"], jd)
    r["screening"]["version"] = SCREEN_VERSION
    if r["screening"]["decision"] == "reject":
        r.update(status="rejected_code",reason="; ".join(x["rule"] + ": " + x["evidence"] for x in r["screening"]["reasons"]))
        path=JOBS / "reports" / ("screen-" + hashlib.sha256(job["url"].encode()).hexdigest()[:16] + ".md")
        path.write_text("# Code screening\n\nPosting: " + job["url"] + "\n\n" + "\n".join("- " + x["rule"] + ": " + x["evidence"] for x in r["screening"]["reasons"]) + "\n")
        r["report_path"]=str(path.relative_to(JOBS))
        return r
    level = entry_classify(job["title"], jd)
    r["entry_filter"] = level
    r["screening"]["level"] = level["category"]
    if level["decision"] != "pass":
        r["screening"]["decision"] = level["decision"]
        r["screening"]["reasons"] = [{"rule": "Entry-level filter: " + level["category"],
                                          "evidence": row["quote"]} for row in level["evidence"]]
        r.update(status="rejected_screen" if level["decision"] == "reject" else "needs_review",
                 reason=level.get("error") or ("Required experience is beyond entry level." if level["decision"] == "reject" else "Seniority is unclear. Review the quoted requirements before proceeding."))
        path = JOBS / "reports" / ("screen-" + hashlib.sha256(job["url"].encode()).hexdigest()[:16] + ".md")
        path.write_text("# Entry-level screening\n\nPosting: " + job["url"] + "\n\n" + r["reason"] + "\n\n" +
                        "\n".join("- " + row["evidence"] for row in r["screening"]["reasons"]) + "\n")
        r["report_path"] = str(path.relative_to(JOBS))
        if r["status"] == "needs_review":
            with tempfile.TemporaryDirectory(prefix="job-h1b-fresh-") as cache:
                r["h1b"] = h1b_lookup(job["company"], JOBS, Path(cache))
            with path.open("a") as f:
                f.write("\n## H-1B and PERM history\n\n" + h1b_description(r["h1b"]) + "\n")
        return r
    r["screening"]["flags"].append({"rule": "Entry-level filter: " + level["category"],
                                     "evidence": "; ".join(row["quote"] for row in level["evidence"])})
    with tempfile.TemporaryDirectory(prefix="job-h1b-fresh-") as cache:
        r["h1b"] = h1b_lookup(job["company"], JOBS, Path(cache))
    try:
        result = evaluate(jd, (JOBS / "cv.md").read_text(), HERE / "local-cache")
    except Exception as exc:
        return {**r,"status":"error","reason":f"Evidence matching failed: {type(exc).__name__}: {exc}"}
    name = "local-" + hashlib.sha256(job["url"].encode()).hexdigest()[:16] + ".md"
    report = JOBS / "reports" / name
    write_report(report, job, result)
    with report.open("a") as f:
        f.write("\n## Code screening\n\nPassed deterministic gates.\n")
        for flag in r["screening"]["flags"]:
            f.write("- " + flag["rule"] + ": " + flag["evidence"] + "\n")
        f.write("\n## H-1B and PERM history\n\n" + h1b_description(r["h1b"]) + "\n")
    quotes = {q["id"]: q["quote"] for q in result["requirements"]}
    r.update(status="local_ready", report_path=str(report.relative_to(JOBS)),
             reason="Local preliminary evidence review. Use Copy Refine Prompt for final Word resume and verification.",
             matches=[quotes[m["id"]] for m in result["matches"] if m["level"] == "supported"],
             gaps=[quotes[m["id"]] for m in result["matches"] if m["level"] != "supported"],
             model=result["model"], source_hash=result["source_hash"])
    try:
        r.update(build_draft(r, JOBS))
    except Exception as exc:
        r.pop("resume_path", None)
        r.update(status="draft_error", reason=f"Resume draft failed: {type(exc).__name__}: {exc}")
        return r
    r.update(reason="Nemotron Word draft ready. Use Copy Refine Prompt for final factual and visual polish.")
    return r


def needs_work(record):
    if record.get("screening", {}).get("version") != SCREEN_VERSION:
        return record.get("tries", 0) < MAX_TRIES
    return record.get("status") not in TERMINAL and record.get("tries", 0) < MAX_TRIES


def write_day(state, day):
    rows = [j for j in state["jobs"].values() if j.get("at", "").startswith(day)]
    lines = [f"# Job agent: {day}", "", "Jobs in collection order", ""]
    for j in rows:
        lines.append(f"- {j['company']}: [{j['title']}]({j['url']}) [{j['status']}] — {j.get('reason', '')}")
    path = JOBS / "listings" / f"agent-{day}.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def announce(state, now):
    if not NOTIFY_FROM <= now.hour < NOTIFY_UNTIL:
        return
    ready = [j for j in state["jobs"].values() if j.get("status") == "local_ready" and not j.get("notified")]
    if not ready:
        return
    notify("Job agent", f"{len(ready)} resume drafts ready",
           "Drafts are ready for your final polish. Open Job Agent to continue.", group="job-agent-ready")
    for j in ready:
        j["notified"] = True


def main():
    lock = open(HERE / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("previous cycle still running; skipping")
        return
    state = json.loads(STATE.read_text()) if STATE.exists() else {"jobs": {}}
    now = dt.datetime.now()
    try:
        receipt = scan()
    except Exception as exc:
        receipt = {"added": 0, "errors": [{"company": "scan", "error": str(exc)}]}
    fresh_jobs = receipt.pop("jobspy_jobs", {})
    state["last_scan"] = {"at": now.isoformat(timespec="minutes"), **receipt}
    log(f"scan: {receipt['added']} new, {len(receipt['errors'])} board errors")
    lines = (JOBS / "data" / "pipeline.md").read_text().splitlines()
    for line in lines:
        if line.startswith("- [ ] "):
            job = parse_listing(line)
            state["jobs"].setdefault(job["url"], {**job, "status": "pending", "at": now.isoformat(timespec="minutes")})
    merge_discovery(state["jobs"], fresh_jobs, CURRENT_CYCLE)
    # Jobs stay in collection order; screening version changes trigger rechecking.
    pending = [j for j in state["jobs"].values() if needs_work(j)]
    atomic_json(STATE, state)
    try:
        alive = liveness([j["url"] for j in pending])
    except Exception:
        alive = {}
    completed = 0
    processed = 0
    import status
    while pending:
        job = pending.pop(0)
        processed += 1
        started = time.time()
        defaults = {"salary": "", "posted": "", "location": ""}
        try:
            result = process({**defaults, **job}, alive.get(job["url"], "uncertain"))
        except Exception as exc:
            jd_text, jd_file = capture_for(job["url"])
            result = {**job, "status": "error", "reason": f"{type(exc).__name__}: {exc}",
                      "jd_file": jd_file, "at": now.isoformat(timespec="minutes")}
            traceback.print_exc()
        if result["status"] not in TERMINAL:
            result["tries"] = job.get("tries", 0) + 1
        else:
            result["tries"] = 0
            completed += int(result["status"] == "local_ready")
        state["jobs"][job["url"]] = result
        atomic_json(STATE, state)
        status.build()
        if needs_work(result):
            pending.append(result)
        if pending:
            time.sleep(2)
        log(f"{result['status']}: {job['company']} | {job['title']} ({time.time() - started:.0f}s)")
    write_day(state, str(now.date()))
    announce(state, now)
    cycles = state.setdefault("cycles", [])
    cycles.append({"at": now.isoformat(timespec="minutes"), "processed": processed, "ready": completed})
    del cycles[:-200]
    atomic_json(STATE, state)
    import status
    status.build()
    log(f"cycle done: {processed} processed, {completed} resume drafts ready")


if __name__ == "__main__":
    main()
