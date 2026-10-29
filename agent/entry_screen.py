"""Bridge to the isolated Simple-JEV environment; never start a second model."""
import json
import subprocess
from pathlib import Path

VERSION = 'entry-filter-context-v2.1'
CONFIG = Path(__file__).with_name('simple_jev.json')


def classify(title, jd):
    try:
        config = json.loads(CONFIG.read_text())
        p = subprocess.run([config['python'], '-m', 'entry_filter'],
                           cwd=config['repo'], input=json.dumps({'title': title, 'jd': jd}),
                           text=True, capture_output=True, timeout=150, check=True)
        result = json.loads(p.stdout)
        categories = {'entry_compatible':'pass', 'stretch':'pass', 'not_entry_level':'reject', 'unclear':'review'}
        if result.get('version') != VERSION or categories.get(result.get('category')) != result.get('decision'):
            raise ValueError('Unexpected classifier version or category/decision')
        if result['decision'] != 'review' and not result.get('evidence'):
            raise ValueError('Missing screening evidence')
        lines = jd.splitlines()
        for row in result.get('evidence', []):
            start, end = row['line'], row.get('end_line', row['line'])
            if start == 0:
                valid = row['quote'] == title
            else:
                valid = 1 <= start <= end <= len(lines) and row['quote'] == '\n'.join(x.strip() for x in lines[start-1:end] if x.strip())
            if not valid:
                raise ValueError('Screening citation does not match the source')
        return result
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        return {'version': VERSION, 'category':'unclear', 'decision':'review', 'evidence':[],
                'error':f'{type(exc).__name__}: {exc}'}
