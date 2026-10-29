"""Local DOL history; informational only, with conservative name matching."""
import datetime as dt
import json
import os
import re
import subprocess

def normalize(name):
    s=re.sub(r'[^a-z0-9]+',' ',name.lower()).strip()
    return re.sub(r'(?:\s+(?:inc|incorporated|llc|corp|corporation|ltd|limited|llp|lp|company))+$','',s).strip()

def same_employer(a,b):
    return bool(normalize(a)) and normalize(a)==normalize(b)

def interpret(company,data):
    matched=data.get('displayName','')
    base={'checked_at':dt.datetime.now().isoformat(timespec='minutes'),'source':data.get('source'),
          'verified':False,'tier':'unknown','employer':matched}
    if not data.get('found') or not same_employer(company,matched):return base
    t=data.get('totals') or {}
    return {**base,'verified':True,'tier':data.get('friendlinessTier','unknown'),
            'lca':t.get('n_lca'),'certified':t.get('n_certified'),'perm':t.get('n_perm'),
            'years':f"{t.get('first_year') or 'unknown'} to {t.get('last_year') or 'unknown'}",
            'green_card':t.get('does_gc')}

def lookup(company,root,cache):
    env=os.environ.copy()
    env.pop('H1B_API_BASE',None);env.pop('H1B_API_TOKEN',None)
    try:
        p=subprocess.run(['node','plugins/h1b-sponsor/check.mjs',company,'--json','--cache-dir',str(cache)],
                         cwd=root,env=env,capture_output=True,text=True,timeout=120)
        if p.returncode:raise RuntimeError((p.stderr or p.stdout)[-300:])
        return interpret(company,json.loads(p.stdout))
    except Exception as exc:
        return {'verified':False,'tier':'unknown','error':str(exc)[:300],
                'checked_at':dt.datetime.now().isoformat(timespec='minutes')}

def describe(h):
    if not h.get('verified'):
        reason=f" Name lookup returned {h['employer']}; the employer match is unverified." if h.get('employer') else ''
        return 'H-1B/PERM history: unknown.'+reason+(' Lookup failed: '+h['error'] if h.get('error') else '')
    val=lambda k:'unknown' if h.get(k) is None else str(h[k])
    return (f"DOL filing history for {h['employer']}: {val('lca')} H-1B LCA filings, {val('certified')} certified; "
            f"{val('perm')} PERM filings; years {h['years']}. Historical filings do not confirm sponsorship for this opening.")
