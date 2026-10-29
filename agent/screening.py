"""Conservative eligibility gates. Ambiguous evidence is flagged, never rejected."""
import re
VERSION = 'eligibility-v1'
ENGINEERING = re.compile(r'\b(engineer|engineering|developer|swe|programmer|scientist)\b',re.I)
NON_ENGINEERING = re.compile(r'\b(recruiter|account executive|account development representative|sales development representative|marketing manager|product manager|accountant|sales representative|customer success manager)\b',re.I)
SENIOR = re.compile(r'\b(senior|sr\.?|staff|principal|director|head of|engineering manager)\b',re.I)
EARLY = re.compile(r'\b(new grad|early career|entry level|intern(?:ship)?)\b',re.I)
PREFERRED = re.compile(r'preferred|nice[- ]to[- ]have|bonus|desired|not required|a plus',re.I)
WORDS = {'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10}
YEARS = re.compile(r'\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:\+|(?:to|[-–])\s*\d+)?\s*(?:or more\s+)?years?\s+(?:of\s+)?(?:[a-zA-Z/-]+\s+){0,8}experience\b',re.I)
CITIZEN = re.compile(r'(?:must|required to|need to)\s+be\s+(?:a\s+)?(?:u\.?s\.?|united states)\s+citizen|(?:u\.?s\.?|united states)\s+citizens?\s+only|(?:u\.?s\.?|united states)\s+citizenship\s+(?:is\s+)?required|requires?\s+(?:u\.?s\.?|united states)\s+citizenship',re.I)
DUTY = re.compile(r'(?:you\s+(?:will|must|are expected to)|this role (?:will|requires you to))\s+(?:directly\s+)?(?:manage|lead)\s+(?:a|the)\s+team\s+of\s+(?:software\s+)?engineers|(?:responsible for|you will own)\s+(?:hiring|performance reviews)\s+(?:and|of|for)\s+',re.I)

def screen(title,jd):
    rejects=[];flags=[]
    def add(dst,rule,quote):
        entry={'rule':rule,'evidence':quote.strip()}
        if entry not in dst:dst.append(entry)
    if not ENGINEERING.search(title):
        add(rejects if NON_ENGINEERING.search(title) else flags,'non-engineering title' if NON_ENGINEERING.search(title) else 'unclear engineering role',title)
    if SENIOR.search(title):
        add(flags if EARLY.search(title) else rejects,'senior title',title)
    elif re.search(r'\b(lead|architect|manager)\b',title,re.I):
        add(flags,'ambiguous level in title',title)
    preferred=False
    for raw in jd.splitlines():
        line=raw.strip();bare=line.strip('#*-• :')
        if not bare:continue
        if len(bare)<80 and not re.search(r'[.!?]|\d',bare):
            if PREFERRED.search(bare):preferred=True
            elif re.search(r'requirements|qualifications|responsibilities|minimum|what you|what we|about|benefits',bare,re.I):preferred=False
        # Preserve U.S. abbreviations; split ordinary sentences and semicolons.
        parts=re.split(r'(?<!U\.S)(?<!U\.S\.)(?<=[!?;])\s+|(?<=[a-z0-9]\.)(?=\s+[A-Z])\s+',line)
        for quote in parts:
            optional=preferred or bool(PREFERRED.search(quote))
            negated=bool(re.search(r'not required|no .{0,25}(?:required|necessary)|do not (?:need|require)|does not require|not .{0,12}require',quote,re.I))
            citizen=CITIZEN.search(quote)
            alternative=bool(re.search(r'\bor\b.{0,70}(?:permanent resident|green card|u\.?s\.? person|authoriz)',quote,re.I))
            if citizen and not negated:
                add(flags if optional or alternative else rejects,'citizenship restriction',quote)
            elif re.search(r'(?:have|hold|possess|maintain) (?:an? )?(?:active|current) .{0,35}security clearance|(?:active|current) .{0,35}security clearance (?:is )?required',quote,re.I) and not negated and not optional:
                add(rejects,'explicit active clearance requirement',quote)
            elif re.search(r'clearance|\bITAR\b|export control|u\.?s\.? person',quote,re.I) and not negated:
                add(flags,'verify clearance/export eligibility',quote)
            for m in YEARS.finditer(quote):
                n=WORDS.get(m.group(1).lower(),int(m.group(1)) if m.group(1).isdigit() else 0)
                # Company/team history and benefits are not candidate requirements.
                if re.search(r'(?:our|the) (?:team|company|founders)|we have|combined|collective|in business|founded',quote,re.I):continue
                # Alternatives need review rather than treating the largest number as mandatory.
                alternatives=(len(list(YEARS.finditer(quote))) > 1 and bool(re.search(r'\bor\b',quote,re.I))) or bool(re.search(r'\bor equivalent\b',quote,re.I))
                if optional:continue
                if n>=5:add(flags if alternatives else rejects,'experience requirement',quote)
                elif n>=3:add(flags,'3 or 4 years requested',quote)
            if (DUTY.search(quote) or re.search(r'experience (?:in )?(?:leading|managing) (?:a |an |the )?team|^[-*• ]*Mentor (?:Principal|Senior) (?:and Senior )?(?:Associates|Engineers)',quote,re.I)) and not negated and not optional:
                add(rejects,'explicit team leadership duties',quote)
            elif re.search(r'mentor|technical direction|lead.{0,15}(?:team|project)',quote,re.I) and not optional:
                add(flags,'verify scope of leadership duties',quote)
    return {'version':VERSION,'decision':'reject' if rejects else 'pass','reasons':rejects,'flags':flags}
