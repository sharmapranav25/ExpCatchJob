"""Get a job description as plain text, with no LLM.

1. fetch-jd.mjs (public ATS APIs: Greenhouse, Ashby, Lever, ...)
2. Built In pages (builtinsf.com, builtin.com, ...): the schema.org JobPosting JSON-LD
   in the page HTML carries the full description and the real employer name.
"""

import html
import json
import re
import subprocess
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

JOBS = Path(__file__).resolve().parents[2]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128 Safari/537.36")
BLOCK = {"p", "br", "li", "ul", "ol", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.out.append("\n- ")
        elif tag in BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        self.out.append(data)


def html_to_text(s):
    p = _Text()
    p.feed(html.unescape(s) if "&lt;" in s else s)
    text = "".join(p.out).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _job_posting(page):
    # Built In writes the type as "application/ld&#x2B;json".
    for m in re.finditer(r'<script[^>]*application/ld(?:\+|&#x2B;|&#43;)json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        for item in d.get("@graph", [d]) if isinstance(d, dict) else d:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


_last_builtin = [0.0]


def builtin_jd(url):
    # A burst of requests got every Built In page refused on 2026-10-27: keep 2s between requests, retry twice.
    for attempt in range(3):
        wait = _last_builtin[0] + 2 - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_builtin[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            page = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    jp = _job_posting(page)
    if not jp or not jp.get("description"):
        return ""
    org = (jp.get("hiringOrganization") or {}).get("name", "")
    head = f"{jp.get('title', '')} at {org}\n" if org else ""
    return head + html_to_text(jp["description"])


def _ats_jd(url):
    p = subprocess.run(["node", "fetch-jd.mjs", url], cwd=JOBS, capture_output=True, text=True, timeout=60)
    return p.stdout.strip() if p.returncode == 0 else ""


class _PublicRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _public_url(url):
    import ipaddress
    import socket
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Invalid public job URL")
    addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError("Job URL does not resolve to a public address")


def fetch_jd_result(url):
    """Fetch afresh; return description and auditable source or failure reason."""
    try:
        text = _ats_jd(url)
        if text:
            return text, {"method": "ats", "source_url": url}
        if re.search(r"//(www\.)?builtin[a-z]*\.com/job/", url):
            text = builtin_jd(url)
            return text, {"method": "builtin-jsonld", "source_url": url, **({} if text else {"error": "No Built In JobPosting description"})}
        _public_url(url)
        opener = urllib.request.build_opener(_PublicRedirect())
        with opener.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30) as response:
            final_url = response.url
            raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("Job page exceeds size limit")
        if final_url != url:
            text = _ats_jd(final_url)
            if text:
                return text, {"method": "redirect-ats", "source_url": final_url, "original_url": url}
        page = raw.decode("utf-8", "replace")
        # A listing page with multiple postings must never become one job's JD.
        postings = []
        def visit(value):
            if isinstance(value, list):
                for item in value:
                    visit(item)
            elif isinstance(value, dict):
                kind = value.get("@type")
                if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
                    postings.append(value)
                if "@graph" in value:
                    visit(value["@graph"])
        for match in re.finditer(r'<script[^>]*application/ld(?:\+|&#x2B;|&#43;)json[^>]*>(.*?)</script>', page, re.S | re.I):
            try:
                visit(json.loads(match.group(1)))
            except ValueError:
                continue
        if len(postings) == 1 and isinstance(postings[0].get("description"), str):
            posting = postings[0]
            text = html_to_text(posting["description"])
            if len(text) >= 100:
                return posting.get("title", "") + "\n" + text, {"method": "jobposting-jsonld", "source_url": final_url}
        return "", {"error": "No unique full JobPosting description on fetched page", "source_url": final_url}
    except Exception as exc:
        return "", {"error": f"{type(exc).__name__}: {exc}", "source_url": url}


def fetch_jd(url):
    """Compatibility interface for existing consumers."""
    return fetch_jd_result(url)[0]
