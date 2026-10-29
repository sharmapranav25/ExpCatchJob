"""Sequential, fresh Resume Matcher draft handoff after screening passes."""
import hashlib
import json
import subprocess
import uuid
from pathlib import Path

CONFIG = Path(__file__).with_name('resume_builder.json')
MODEL = 'nemotron-3.5-bolt:e4b-nvfp4'


def build(record, jobs):
    config = json.loads(CONFIG.read_text())
    REPO = Path(config['repo'])
    TEMPLATE = Path(config['template'])
    if record.get('screening', {}).get('decision') != 'pass' or record.get('entry_filter', {}).get('decision') != 'pass':
        raise ValueError('Both screening stages must pass before drafting')
    output = jobs / 'output' / 'drafts' / (hashlib.sha256(record['url'].encode()).hexdigest()[:16] + '-' + uuid.uuid4().hex[:12])
    output.mkdir(parents=True)
    step3 = output / 'step3.json'
    step3.write_text(json.dumps(record, indent=2))
    # Inputs must remain outside the adapter output directory; retain them for polish.
    artifacts = output / 'artifacts'
    args = [str(REPO / '.builder-venv/bin/python'), '-m', 'app.job_builder',
            '--cv', str(jobs / 'cv.md'), '--jd', str(jobs / record['jd_file']),
            '--record', str(step3), '--template', str(TEMPLATE),
            '--slots', str(REPO / config['slots']),
            '--output', str(artifacts), '--model', MODEL]
    result = subprocess.run(args, cwd=REPO / 'apps/backend', capture_output=True, text=True, timeout=1500)
    (output / 'builder.log').write_text(result.stdout + '\n' + result.stderr)
    path = artifacts / 'manifest.json'
    manifest = json.loads(path.read_text()) if path.exists() else {}
    if result.returncode or manifest.get('status') != 'draft_ready':
        raise RuntimeError(manifest.get('error') or f'Resume Matcher failed (exit {result.returncode}); log: {output / "builder.log"}')
    if manifest.get('model') != MODEL or manifest.get('layout', {}).get('pages') != 1:
        raise ValueError('Draft model/page verification failed')
    for filename in ('resume-draft.docx', 'resume-draft.pdf', 'evidence.md', 'refine-prompt.txt'):
        if not (artifacts / filename).is_file():
            raise ValueError('Missing draft artifact: ' + filename)
    return {'resume_path': str((artifacts / 'resume-draft.docx').relative_to(jobs)),
            'draft_evidence_path': str((artifacts / 'evidence.md').relative_to(jobs)),
            'draft_manifest_path': str(path.relative_to(jobs)), 'draft_model': MODEL}
