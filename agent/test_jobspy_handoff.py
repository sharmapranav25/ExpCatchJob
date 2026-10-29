import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import jobspy_jd
import run_cycle as runner
TEXT='Requirements\n3 years of Python software engineering experience.\nBuild APIs and deliver production software integrations for customers.'
class HandoffTests(unittest.TestCase):
    def test_known_job_is_requeued_and_current_description_used(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);url='https://www.linkedin.com/jobs/view/123'
            (root/'linkedin.json').write_text(json.dumps({'cycle':'fresh','jobs':[{'url':url,'sourceUrl':url,'description':TEXT,'source':'jobspy:linkedin'}]}))
            jobs={url:{'url':url,'status':'no_jd','tries':3}}
            jobspy_jd.merge_discovery(jobs,jobspy_jd.read_handoff(root,'fresh'),'fresh')
            self.assertEqual(jobs[url]['tries'],0)
            with patch('jobspy_jd.subprocess.run') as network:
                self.assertEqual(jobspy_jd.description(jobs[url],'fresh')[0],TEXT)
                network.assert_not_called()
            with patch('jobspy_jd.CONFIG',root/'missing'):
                self.assertEqual(jobspy_jd.description(jobs[url],'other-cycle')[0],'')
    def test_wrong_cycle_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'board.json';p.write_text('{"cycle":"old","jobs":[]}')
            with self.assertRaises(ValueError):jobspy_jd.read_handoff(d,'new')
    def test_actual_worker_consumes_handoff_before_unsupported_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'reports').mkdir()
            job={'url':'https://www.linkedin.com/jobs/view/123','title':'Senior Software Engineer','company':'Test', 'discovery':{'cycle':'new','description':TEXT}}
            with patch.object(runner,'CURRENT_CYCLE','new'),patch.object(runner,'JOBS',root),patch.object(runner,'save_jd',return_value='jds/test.md') as save,patch.object(runner,'fetch_jd') as oldfetch,patch.object(runner,'evaluate') as model:
                result=runner.process(job,'active')
            self.assertEqual(result['status'],'rejected_code')
            self.assertEqual(result['jd_source']['method'],'jobspy-discovery')
            self.assertEqual(save.call_args.args[1],TEXT)
            oldfetch.assert_not_called();model.assert_not_called()
