"""Job agent status page: builds ~/.job-agent/status.html from state.json and launchd, and opens it with --open.

The "Job Agent" app in ~/Applications runs this with --open; run_cycle.py rebuilds it after every cycle,
and an open page reloads itself every minute.
"""

import base64
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from sponsorship import describe as h1b_description

HERE = Path(__file__).resolve().parent
JOBS = HERE.parents[1]
STATE = HERE / "state.json"
RESUME_CONFIG = HERE / "resume_builder.json"
OUT = Path.home() / ".job-agent" / "status.html"
LABEL = "com.pranav.jobagent"


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


SOURCES = [("builtinsf.com", "Built In SF (job board)"), ("builtin.com", "Built In (job board)"),
           ("linkedin.com", "LinkedIn"), ("ashbyhq.com", "Company board (Ashby)"),
           ("greenhouse.io", "Company board (Greenhouse)"), ("lever.co", "Company board (Lever)"),
           ("workable.com", "Company board (Workable)"), ("myworkdayjobs.com", "Company board (Workday)"),
           ("careerpuck.com", "Company board (Greenhouse)"), ("gh_jid=", "Company board (Greenhouse)")]


def source(url):
    for key, name in SOURCES:
        if key in url:
            return name
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    return f"Company site ({host})"


def agent_state():
    p = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], capture_output=True, text=True)
    if p.returncode != 0:
        return "stopped"
    return "checking" if re.search(r"^\s*state = running", p.stdout, re.M) else "idle"


def e(s):
    return html.escape(str(s if s is not None else ""))


def money(s):
    """'136000-155000' -> '$136K to $155K'; anything else unchanged."""
    nums = [int(n) for n in re.findall(r"\d{5,7}", s or "")]
    return " to ".join(f"${n // 1000}K" for n in nums[:2]) if nums else (s or "")


def build():
    state = json.loads(STATE.read_text()) if STATE.exists() else {"jobs": {}}
    jobs = list(state["jobs"].values())
    now = dt.datetime.now()
    today = str(now.date())
    last = (state.get("cycles") or [{}])[-1].get("at") or state.get("last_scan", {}).get("at") \
        or max((j.get("at", "") for j in jobs), default="")
    agent = agent_state()

    rejected = [j for j in jobs if j.get("status") == "rejected_code" or j.get("screening", {}).get("decision") == "reject"]
    rejected_urls = {j["url"] for j in rejected}
    ready = [j for j in jobs if j["url"] not in rejected_urls and j.get("status") == "local_ready" and j.get("resume_path")]
    review = [j for j in jobs if j["url"] not in rejected_urls and j.get("status") == "needs_review"]
    failed = [j for j in jobs if j["url"] not in rejected_urls and j.get("status") not in ("local_ready", "needs_review") and j.get("tries", 0) >= 3]
    failed_urls = {j["url"] for j in failed}
    pending = [j for j in jobs if j["url"] not in rejected_urls | failed_urls and j.get("status") not in ("local_ready", "needs_review")]
    stats = [("Jobs collected", len(jobs), "All postings found"),
             ("Rejected", len(rejected), "Stopped by screening rules"),
             ("Pending", len(pending), "Queued or processing"),
             ("Fetch / processing errors", len(failed), "Retries exhausted; needs attention"),
             ("Needs review", len(review), "Level unclear; not approved by screening"),
             ("Drafts ready", len(ready), "Word drafts ready for final polish")]

    def h1b_html(h):
        if not h:
            return ""
        return '<p class="meta">' + e(h1b_description(h)) + '</p>'

    def file_link(path, label, css="alt"):
        # A plain file:// link doesn't work here: WebKit refuses to navigate a file:// page to
        # another file:// path outside its own folder (the crash-recovery reload banner). The
        # Job Agent app's jobagent://file/ handler opens the real path with `open` instead.
        if not path:
            return ""
        full = (JOBS / path).resolve()
        if not full.exists():
            return f'<span class="meta dim">{label}: missing ({e(path)})</span>'
        b64 = base64.urlsafe_b64encode(str(full).encode()).decode().rstrip("=")
        return f'<a class="btn {css}" href="jobagent://file/{b64}">{label}</a>'

    def apply_link(j):
        if j.get("status") == "needs_review":
            return ""
        url = j.get("apply_url") or j.get("url")
        if not url:
            return ""
        return f'<a class="btn" href="{e(url)}" target="_blank" rel="noopener">Apply</a>'

    def refine_button(j):
        if j["url"] in rejected_urls or j.get("status") == "needs_review":
            return ""
        # Clipboard text, not a jobagent:// link: this one is deliberately model-agnostic, so it
        # works pasted into any LLM, not just a Claude Code session on this machine.
        root = str(JOBS)
        resume_config = json.loads(RESUME_CONFIG.read_text()) if RESUME_CONFIG.exists() else {}
        template_path = resume_config.get('template', '<configure resume_builder.json: template>')
        constraints_path = resume_config.get('constraints', '<configure resume_builder.json: constraints>')
        prompt = (
            (f"Code screening status: {j.get('screening', {}).get('decision', 'pending')}. "
             f"Reasons and flags: {json.dumps(j.get('screening', {}), ensure_ascii=False)}\n"
             f"{h1b_description(j['h1b']) if j.get('h1b') else 'H-1B lookup pending or not reached.'}\n\n")
            + f"Create and refine my Word resume for {j['company']} — {j['title']}. Posting: {j.get('apply_url') or j['url']}\n\n"
            f"Read all sources first:\n- Canonical resume (sole authority for candidate facts): {root}/cv.md\n"
            + (f"- Job description: {root}/{j['jd_file']}\n" if j.get('jd_file') else "- Job description not saved yet: retrieve it from the posting URL above.\n")
            + (f"- Resume evidence packet (verify matches): {root}/{j['report_path']}\n" if j.get('report_path') else "- Evidence packet pending: match the JD directly against cv.md.\n")
            + (f"- Existing tailored resume: {root}/{j['resume_path']}\n" if j.get('resume_path') else "")
            + (f"- Draft source ledger: {root}/{j['draft_evidence_path']}\n" if j.get('draft_evidence_path') else "")
            + f"- Original Word template: {template_path}\n"
            + f"- Jobs template configuration: {root}/data/templates/pranav/cv-template.pranav.html\n"
            + f"- House rules: {root}/modes/_custom.md and {constraints_path}\n\n"
            "Use the original Word template and its matching template instructions if present. Keep Times New Roman, centered name/contact row, ruled headings, and Skills, Experience, Projects, Education order. "
            "Use only verified cv.md facts. Select and polish relevant evidence; never invent skills, numbers, experience or achievements. "
            "Deliver a finished one-page DOCX. Render and inspect it, adjusting font size and spacing to fill the page cleanly without a large bottom gap or overflow. "
            "Do not score or rank this job, or recommend whether to apply. Verify evidence and posting availability. Do not submit the application."
        )
        return (f'<button class="btn alt" onclick="copyRefinePrompt(this)" '
                f'data-prompt="{e(prompt)}">Copy Refine Prompt</button>')

    def job_card(j):
        visa = {"no-sponsorship": '<span class="flag">JD states no sponsorship</span>',
                "sponsorship-available": '<span class="ok">JD mentions sponsorship</span>'}.get(j.get("visa"), "")
        gaps = "".join(f"<li>{e(g)}</li>" for g in j.get("gaps", []))
        matches = "".join(f"<li>{e(m)}</li>" for m in j.get("matches", []))
        legit = f' · Legitimacy: <b>{e(j["legitimacy"])}</b>' if j.get("legitimacy") else ""
        buttons = (file_link(j.get("resume_path"), "View Resume", "primary") + apply_link(j)
                  + file_link(j.get("report_path"), "View Report") + refine_button(j))
        return f"""<article class="job">

  <div class="body">
    <h3><a href="{e(j['url'])}" target="_blank" rel="noopener">{e(j['title'])}</a></h3>
    <p class="meta"><b>{e(j['company'])}</b> · {e(j.get('location', ''))}{(' · ' + e(money(j['salary']))) if j.get('salary') else ''}</p>
    <p class="meta">Found on <b>{e(source(j['url']))}</b> · {e(j.get('at', '').replace('T', ' '))} {visa}{legit}</p>
    {h1b_html(j.get('h1b'))}
    <p class="reason">{e(j.get('status', 'pending'))}: {e(j.get('reason', ''))}</p>
    <ul>{''.join('<li>' + e(x['rule'] + ': ' + x['evidence']) + '</li>' for x in j.get('screening', {}).get('flags', []))}</ul>
    <div class="btn-row">{buttons or '<p class="meta dim">resume/report still building or missing — check agent.log</p>'}</div>
    <details><summary>Requirements: preliminary matches and gaps</summary><div class="cols"><div><h4>Matches</h4><ul>{matches}</ul></div>
      <div><h4>Gaps</h4><ul>{gaps}</ul></div></div></details>
  </div>
</article>"""

    def rejected_card(j):
        reasons = j.get("screening", {}).get("reasons", [])
        evidence = ''.join('<li><b>' + e(x['rule']) + '</b><blockquote>' + e(x['evidence']) + '</blockquote></li>' for x in reasons)
        if not evidence:
            evidence = '<li>' + e(j.get('reason', 'Screening rejection; review report for details.')) + '</li>'
        return f"""<article class="job rejected-job"><div class="body">
        <h3>{e(j['company'])}: <a href="{e(j['url'])}" target="_blank" rel="noopener">{e(j['title'])}</a></h3>
        <p class="rejection-label">Rejected by screening rules</p>
        <ul>{evidence}</ul>
        <div class="btn-row">{file_link(j.get('report_path'), 'View screening report')}</div>
        </div></article>"""

    errors = state.get("last_scan", {}).get("errors", [])
    err_html = ("<section><h2>Boards that failed in the last scan</h2><ul>" +
                "".join(f"<li><b>{e(x['company'])}</b>: {e(x['error'])}</li>" for x in errors) + "</ul></section>") if errors else ""
    status_text = {"checking": "Checking now", "idle": "Running", "stopped": "Stopped"}[agent]

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="60">
<title>Job Agent</title>
<style>
:root {{ --bg:#f7f7f5; --card:#ffffff; --text:#1d1d1f; --muted:#6e6e73; --line:#e5e5e0; --accent:#2f6f4f;
        --good:#1f7a4d; --goodbg:#e6f4ec; --warn:#9a5b00; --warnbg:#fff4e0; --bad:#b42318; --badbg:#fde8e7; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141414; --card:#1e1e1e; --text:#ececec; --muted:#9a9a9a;
        --line:#2e2e2e; --accent:#7cc4a0; --good:#7cc4a0; --goodbg:#183326; --warn:#f0b35a; --warnbg:#3a2a10;
        --bad:#ff8a80; --badbg:#3a1714; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif; }}
main {{ max-width:980px; margin:0 auto; padding:28px 16px 60px; }}
header {{ display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }}
h1 {{ font-size:26px; margin:0; }} h2 {{ font-size:17px; margin:32px 0 12px; }} h3 {{ font-size:16px; margin:0 0 4px; }}
h4 {{ margin:8px 0 4px; font-size:13px; color:var(--muted); }}
a {{ color:inherit; }} a:hover {{ color:var(--accent); }}
.pill {{ display:inline-flex; align-items:center; gap:8px; padding:6px 12px; border-radius:999px; font-weight:600; font-size:14px; }}
.pill::before {{ content:""; width:9px; height:9px; border-radius:50%; background:currentColor; }}
.idle, .checking {{ color:var(--good); background:var(--goodbg); }} .stopped {{ color:var(--bad); background:var(--badbg); }}
.stale {{ color:var(--warn); background:var(--warnbg); }}
.sub {{ color:var(--muted); margin:6px 0 0; font-size:14px; }}
.stats {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(190px, 1fr)); gap:12px; margin-top:22px; }}
.stat {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }}
.stat .n {{ font-size:28px; font-weight:700; }} .stat .l {{ font-weight:600; }} .stat .d {{ color:var(--muted); font-size:13px; }}
.job {{ display:flex; gap:16px; background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:12px; }}
.rejected-job {{ border-left:4px solid var(--bad); }}
.rejection-label {{ color:var(--bad); font-weight:600; }}
blockquote {{ margin:4px 0 12px; padding-left:12px; border-left:2px solid var(--line); color:var(--muted); }}
.body {{ min-width:0; flex:1; }} .meta {{ margin:2px 0; color:var(--muted); font-size:14px; }} .reason {{ margin:8px 0 4px; }}
.flag {{ color:var(--warn); margin-left:6px; }} .ok {{ color:var(--good); margin-left:6px; }} .dim {{ opacity:.75; }}
details summary {{ cursor:pointer; color:var(--muted); font-size:14px; }}
.cols {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }} ul {{ margin:4px 0; padding-left:18px; }}
table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden; font-size:14px; }}
th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }} th {{ color:var(--muted); font-weight:600; }}
.scroll {{ overflow-x:auto; }} .empty {{ color:var(--muted); }}
.btn-row {{ display:flex; flex-wrap:wrap; gap:8px; margin:4px 0 8px; }}
.btn {{ display:inline-block; padding:7px 14px; border:none; border-radius:8px; background:var(--accent); color:var(--card);
       font-weight:600; font-size:14px; font-family:inherit; text-decoration:none; cursor:pointer; }}
.btn:hover {{ color:var(--card); filter:brightness(1.1); }} .btn.small {{ padding:4px 10px; font-size:13px; white-space:nowrap; }}
.btn.alt {{ background:transparent; color:var(--accent); border:1px solid var(--accent); }}
.btn.alt:hover {{ color:var(--accent); filter:none; background:var(--goodbg); }}
.btn.primary {{ background:var(--good); }}
.btn.copied {{ background:var(--good) !important; color:var(--card) !important; }}
footer {{ margin-top:36px; color:var(--muted); font-size:13px; }}
@media (max-width:640px) {{ .cols {{ grid-template-columns:1fr; }} .job {{ flex-direction:column; }} }}
</style></head><body><main>
<header><h1>Job Agent</h1><span id="pill" class="pill {agent}">{status_text}</span></header>
<p class="sub" id="when" data-last="{e(last)}" data-agent="{agent}">Last check: {e(last.replace('T', ' ')) or 'never'} ·
local-only processing every 15 minutes; final resume polish is manual · alerts for every completed resume draft from 6am to midnight</p>
<div class="stats">{''.join(f'<div class="stat"><div class="n">{n}</div><div class="l">{e(l)}</div><div class="d">{e(d)}</div></div>' for l, n, d in stats)}</div>
<section id="ready"><h2>Drafts ready ({len(ready)})</h2>
<p class="sub">Passed screening. Nemotron Word drafts are ready for your final factual and visual polish.</p>
{''.join(job_card(j) for j in ready) or '<p class="empty">None yet.</p>'}</section>
<section id="failed"><h2>Fetch / processing errors ({len(failed)})</h2>
<p class="sub">Retries exhausted. These jobs are not approved and are no longer queued.</p>
{''.join(job_card(j) for j in failed) or '<p class="empty">None.</p>'}</section>
<section id="review"><h2>Needs review ({len(review)})</h2>
<p class="sub">Seniority could not be established. These jobs have not passed screening.</p>
{''.join(job_card(j) for j in review) or '<p class="empty">None.</p>'}</section>
<section id="pending"><h2>Pending ({len(pending)})</h2>
{''.join(job_card(j) for j in pending) or '<p class="empty">No jobs waiting.</p>'}</section>
<section id="rejected"><h2>Rejected ({len(rejected)})</h2>
<p class="sub">Stopped by screening rules. Each reason and its supporting text are shown below.</p>
{''.join(rejected_card(j) for j in rejected) or '<p class="empty">No rejected jobs.</p>'}</section>
{err_html}
<footer>Updated {now:%Y-%m-%d %H:%M} · data: local/agent/state.json · log: local/agent/agent.log ·
daily list: listings/agent-{today}.md</footer>
</main>
<script>
function copyRefinePrompt(btn) {{
  const text = btn.dataset.prompt;
  const done = () => {{
    const original = btn.textContent;
    btn.textContent = "Copied!"; btn.classList.add("copied");
    setTimeout(() => {{ btn.textContent = original; btn.classList.remove("copied"); }}, 1800);
  }};
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  }} else {{
    fallbackCopy(text, done);
  }}
}}
function fallbackCopy(text, done) {{
  const ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  try {{ document.execCommand("copy"); done(); }} catch (e) {{ alert("Copy failed — select and copy manually."); }}
  document.body.removeChild(ta);
}}
const w = document.getElementById("when"), pill = document.getElementById("pill");
const last = w.dataset.last ? new Date(w.dataset.last) : null;
if (w.dataset.agent !== "stopped" && last) {{
  const mins = Math.round((Date.now() - last) / 60000);
  const next = new Date(last.getTime() + 15 * 60000);
  if (mins > 25) {{ pill.className = "pill stale"; pill.textContent = "No check for " + mins + " min"; }}
  else w.innerHTML += " · next check about " + next.toLocaleTimeString([], {{hour: "numeric", minute: "2-digit"}});
}}
</script></body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page)
    return OUT


if __name__ == "__main__":
    path = build()
    if "--open" in sys.argv:
        subprocess.run(["open", str(path)])
