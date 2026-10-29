"""Facts about a job that plain code can decide, with no LLM.

Everything here is a regex over the listing line or the JD text, so it is
deterministic and testable. The model never re-decides these.
"""

import re

TIER1 = ["san francisco", "bay area", "palo alto", "mountain view", "menlo park", "sunnyvale",
         "san jose", "redwood city", "san mateo", "oakland", "berkeley", "santa clara",
         "cupertino", "foster city", "fremont", "walnut creek"]
TIER2 = ["california", ", ca", "los angeles", "san diego", "irvine", "santa monica"]

ENGINEERING = re.compile(r"\b(engineer|engineering|developer|swe|programmer|scientist)\b", re.I)
SENIOR = re.compile(r"\b(senior|sr|staff|principal|lead|manager|director|head|architect)\b", re.I)
YEARS = re.compile(r"(at least |minimum of |min\. )?\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s*(\+?)\s*"
                   r"(?:-\s*\d{1,2}\s*)?(?:or more\s+)?years?\b", re.I)
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
# "For 10 years, Scale has..." is company history: a count only counts in a sentence about experience, or as "N+".
EXPERIENCE = re.compile(r"experience|expertise", re.I)
# A US security clearance needs citizenship, so it rules out an F-1 / OPT candidate.
CLEARANCE = re.compile(r"\bcleared\b|security clearance|clearance (?:is )?required|\b(?:active|current) (?:secret|top secret|ts/sci)\b|\bts/sci\b", re.I)
# Citizen-only wording. EEO lines like "regardless of citizenship status" must not match.
CITIZEN = re.compile(r"\b(?:must|required to|need to) be (?:a )?u\.?s\.? (?:citizen|person)|u\.?s\.? citizens? only|"
                     r"u\.?s\.? citizenship (?:is )?required|requires? u\.?s\.? citizenship|\bitar\b|export control", re.I)
# Sentences where a year count is not an experience bar.
NOT_EXPERIENCE = re.compile(r"degree|equivalent|associate|graduat|enrolled|bachelor|master'?s|phd|"
                            r"founded|in business|history|old\b|average of", re.I)
# Duties that signal a mid-level hire even when the title and years don't say so.
# The hire must be the one mentoring: "you'll work with mentors" is a new-grad perk, not a level signal.
MID_LEVEL = re.compile(r"\bmentor(?:ing)? (?:other|junior|teammates|engineers|team members|the team)\b|"
                       r"technical direction|\blead(?:ing)? (?:projects|teams?|a team)\b", re.I)
# Agency posts that hide the employer (an AGENTS.md legitimacy signal).
HIDDEN_EMPLOYER = re.compile(r"on behalf of (?:our|a|an) client|\bour client is\b|confidential (?:company|client|employer)", re.I)
SPONSOR = re.compile(r"sponsor", re.I)
# "sponsorship intelligence" (a product) is not a visa statement.
VISA = re.compile(r"visa|(?:work|employment) authori[sz]ation|immigration|h-?1b|green card", re.I)
# Section headings: years listed under "Preferred" or "Nice to have" are not a bar.
PREFERRED = re.compile(r"preferred|nice[- ]to[- ]have|bonus|pluses|\bplus\b|desired|not required", re.I)
SECTION = re.compile(r"required|basic|minimum|qualifications|requirements|what you|you have|about|benefits|"
                     r"compensation|responsibilities|who you are|what we", re.I)
NO_SPONSOR = re.compile(r"\b(not|no|unable|cannot|can't|without|won't|will not|does not|ineligible)\b", re.I)


def tier(location):
    s = location.lower()
    if any(c in s for c in TIER1):
        return 1
    if any(c in s for c in TIER2):
        return 2
    return 3


def role(title):
    s = title.lower()
    if "forward deployed" in s or "deployed engineer" in s:
        return "FDE"
    if re.search(r"\b(ai|ml|llm)\b engineer|machine learning engineer|applied ai", s):
        return "AI Engineer"
    return "SWE"


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def required_text(jd):
    """The JD without its preferred / nice-to-have sections and inline 'Nice-to-have:' bullets."""
    keep, preferred = [], False
    for line in jd.splitlines():
        bare = line.strip().lstrip("-*• ").strip()
        heading = bare.endswith(":") and len(bare) < 100 or 0 < len(bare) < 40
        if heading and PREFERRED.search(bare):
            preferred = True
            continue
        if heading and SECTION.search(bare):
            preferred = False
        if not preferred and not re.match(r"(nice[- ]to[- ]have|preferred|bonus)\s*:", bare, re.I):
            keep.append(line)
    return "\n".join(keep)


def years_required(jd):
    """Highest explicit 'N+ years' bar in the required part of the JD, or None. Degree clauses don't count."""
    found = []
    for s in sentences(required_text(jd)):
        if NOT_EXPERIENCE.search(s):
            continue
        for m in YEARS.finditer(s):
            n = WORDS.get(m.group(2).lower()) or int(m.group(2))
            if 0 < n <= 15 and (m.group(1) or m.group(3) or EXPERIENCE.search(s)):
                found.append(n)
    return max(found) if found else None


def sponsorship(jd):
    """'no-sponsorship', 'sponsorship-available' or 'none' (the JD is silent)."""
    said = [s for s in sentences(jd) if SPONSOR.search(s) and VISA.search(s)]
    if not said:
        return "none"
    if any(NO_SPONSOR.search(s) for s in said):
        return "no-sponsorship"
    return "sponsorship-available"


def work_model(listing_tags, jd):
    """The listing tag wins (Built In shows 'Hybrid ·', 'In-Office ·'); otherwise the JD text."""
    for text in (listing_tags, jd):
        s = text.lower()
        if "hybrid" in s:
            return "hybrid"
        if re.search(r"in-office|on-site|onsite|in person|in-person|\b5 days in", s):
            return "in-office"
        if re.search(r"\bremote\b", s) and not re.search(r"remote work|wfh stipend", s):
            return "remote"
    return "unknown"


def seniority_flag(title, url):
    """Seniority words in the title or the URL slug (Built In slugs keep 'senior-' when titles drop it)."""
    slug = re.sub(r"[-_/]", " ", url.split("//", 1)[-1].split("?", 1)[0])
    for text in (title, slug):
        m = SENIOR.search(text)
        if m:
            return m.group(1).lower()
    return None


def parse_listing(line):
    """One `- [ ] url | company | title | location | [salary] | posted: date` line from pipeline.md."""
    parts = [p.strip() for p in line.removeprefix("- [ ] ").split(" | ")]
    rest = parts[3:]
    posted = next((p[7:].strip() for p in rest if p.startswith("posted:")), "")
    loc = next((p for p in rest if not p.startswith("posted:")), "")
    salary = next((p for p in rest[1:] if re.search(r"\d{5,}", p) and not p.startswith("posted:")), "")
    return {"url": parts[0], "company": parts[1], "title": parts[2], "location": loc, "salary": salary,
            "posted": posted, "tier": tier(loc), "role": role(parts[2])}


def code_facts(job, jd):
    return {
        "engineering": bool(ENGINEERING.search(job["title"])),
        "years_required": years_required(jd),
        "sponsorship": sponsorship(jd),
        "work_model": work_model(job["location"], jd),
        "seniority_flag": seniority_flag(job["title"], job["url"]),
        "mid_level": sorted({m.group(0).lower() for m in MID_LEVEL.finditer(jd)}),
        "hidden_employer": bool(HIDDEN_EMPLOYER.search(jd)),
        "clearance": bool(CLEARANCE.search(job["title"]) or CLEARANCE.search(jd)),
        "citizen_only": bool(CLEARANCE.search(job["title"]) or CLEARANCE.search(jd) or CITIZEN.search(jd)),
    }
