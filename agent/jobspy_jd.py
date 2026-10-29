"""Fresh JobSpy descriptions and provenance across the discovery/worker handoff."""
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

CONFIG = Path(__file__).with_name('jobspy.json')


def usable(value):
    return isinstance(value, str) and len(value.strip()) >= 100


def read_handoff(directory, cycle):
    jobs = {}
    for path in Path(directory).glob('*.json'):
        data = json.loads(path.read_text())
        if data.get('cycle') != cycle:
            raise ValueError('Wrong JobSpy discovery cycle')
        for row in data.get('jobs', []):
            for url in (row.get('url'), row.get('sourceUrl')):
                if url and (url not in jobs or usable(row.get('description'))):
                    jobs[url] = row
    return jobs


def merge_discovery(jobs, fresh, cycle):
    for url, job in jobs.items():
        row = fresh.get(url)
        if not row:
            continue
        job['discovery'] = {'cycle': cycle, 'description': row.get('description', ''),
                            'source': row.get('source'), 'source_url': row.get('sourceUrl'),
                            'apply_url': row.get('url'), 'fetched_at': row.get('discoveredAt'),
                            'provenance': row.get('provenance', [])}
        if job.get('status') == 'no_jd' and usable(row.get('description')):
            job.update(status='pending', tries=0, reason='Fresh JobSpy description available.')


def description(job, cycle):
    discovery = job.get('discovery', {})
    if cycle and discovery.get('cycle') == cycle and usable(discovery.get('description')):
        return discovery['description'].strip(), {'method':'jobspy-discovery','source_url':discovery.get('source_url') or job['url'], 'cycle':cycle}
    config = None
    errors = []
    # Current direct URL and the preserved board URL may differ.
    for url in dict.fromkeys([job['url'], discovery.get('source_url')]):
        if not url or (urlsplit(url).hostname or '').lower() not in ('www.linkedin.com','linkedin.com','www.glassdoor.com','glassdoor.com','www.ziprecruiter.com','ziprecruiter.com'):
            continue
        try:
            config = config or json.loads(CONFIG.read_text())
            p = subprocess.run([config['python'], '-m', 'jobspy.career_details'], cwd=config['repo'],
                               input=json.dumps({'url':url}), text=True, capture_output=True, timeout=45, check=True)
            result = json.loads(p.stdout)
            if usable(result.get('description')):
                return result['description'].strip(), {'method':'jobspy-detail','source_url':url,'cycle':cycle}
            errors.append(result.get('error','No full description') + ('; '+p.stderr[-700:] if p.stderr else ''))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            errors.append(f'{type(exc).__name__}: {exc}')
    return '', {'error':'; '.join(errors) or 'No supported JobSpy detail URL; ATS fetch also required.'}
