import json
import tempfile
import unittest
from pathlib import Path
from local_evaluator import evaluate

JD='Build customer software integrations.\nDeploy reliable production systems for customers.\nCommunicate closely with customer teams.'
CV='# Resume\n- Built customer integrations.\n- Deployed production systems.'
class Fake:
    def __init__(self, bad=False): self.calls=[]; self.n=0; self.bad=bad
    def ask(self,prompt,check):
        self.n+=1
        if 'Select 6' in prompt:
            return check({'requirement_ids':['J1','J2','J3']})
        return check({'matches':[{'id':f'R{i}','level':'partial','fact_ids':['FAKE' if self.bad else 'F2']} for i in range(1,4)]})
class Tests(unittest.TestCase):
    def test_existing_cache_is_not_reused(self):
        with tempfile.TemporaryDirectory() as d:
            c=Fake(); r=evaluate(JD,CV,d,c); self.assertEqual(c.n,2); self.assertNotIn('score',r)
            c=Fake(); self.assertNotIn('score',evaluate(JD,CV,d,c)); self.assertEqual(c.n,2)
            evaluate(JD,CV+'\nNew fact',d,c); self.assertEqual(c.n,4)
    def test_invalid_fact_does_not_reuse_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError): evaluate(JD,CV,d,Fake(True))
            c=Fake(); evaluate(JD,CV,d,c); self.assertEqual(c.n,2)
    def test_no_cloud(self):
        s=Path(__file__).with_name('run_cycle.py').read_text()
        for forbidden in ('from haiku_screen','from sonnet_eval','run_full_pipeline','"claude"'):
            self.assertNotIn(forbidden,s)
    def test_status_all_reviews_visible(self):
        import status
        with tempfile.TemporaryDirectory() as d:
            status.STATE=Path(d)/'state.json'; status.OUT=Path(d)/'status.html'; status.agent_state=lambda:'idle'
            rows=[{'company':'Test','title':str(n),'url':'https://example.com/'+str(n),'status':s,'resume_path':'output/draft.docx' if s=='local_ready' else None,'score':n,'at':'2026-10-27'} for n,s in [(1,'local_ready'),(4,'local_ready'),(5,'scored')]]
            status.STATE.write_text(json.dumps({'jobs':{r['url']:r for r in rows}}))
            html=status.build().read_text()
            self.assertEqual(html.count('Copy Refine Prompt'),3)
            self.assertIn('https://example.com/5',html)
    def test_report_without_rating(self):
        from local_evaluator import write_report
        with tempfile.TemporaryDirectory() as d:
            r=evaluate(JD,CV,d,Fake()); p=Path(d)/'report.md'
            write_report(p,{'company':'Test','title':'Engineer','url':'https://example.com'},r)
            self.assertNotIn('Coverage estimate',p.read_text())
            self.assertIn('Built customer integrations',p.read_text())
    def test_all_jobs_eligible_and_notified_without_rating(self):
        import sys
        from unittest.mock import patch
        import datetime as dt
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'harness'))
        import run_cycle as runner
        for state in ('failed_code','failed_screen','scored','built','pending','expired'):
            self.assertTrue(runner.needs_work({'status':state}))
        self.assertFalse(runner.needs_work({'status':'error','tries':3}))
        state={'jobs':{'one':{'status':'local_ready'}, 'two':{'status':'local_ready'}}}
        with patch.object(runner,'notify') as notify:
            runner.announce(state,dt.datetime(2026,10,27,12))
            self.assertEqual(notify.call_count,1)
            self.assertIn('2 resume drafts',notify.call_args.args[1])
            runner.announce(state,dt.datetime(2026,10,27,12))
            self.assertEqual(notify.call_count,1)
        self.assertTrue(all(j['notified'] for j in state['jobs'].values()))
    def test_drains_more_than_three_and_bounds_failures(self):
        import run_cycle as runner
        import status
        from unittest.mock import patch
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'data').mkdir()
            (root/'data/pipeline.md').write_text('\n'.join('- [ ] '+str(i) for i in range(5)))
            calls=[]
            def process(job, alive):
                calls.append(job['url'])
                return {**job,'status':'error' if job['url']=='4' else 'local_ready',
                        'screening':{'version':runner.SCREEN_VERSION}}
            with ExitStack() as stack:
                for name,value in [('HERE',root),('JOBS',root),('STATE',root/'state.json')]:
                    stack.enter_context(patch.object(runner,name,value))
                stack.enter_context(patch.object(runner,'scan',return_value={'added':5,'errors':[]}))
                stack.enter_context(patch.object(runner,'parse_listing',side_effect=lambda line: {'url':line.split()[-1],'company':'Test','title':'Engineer'}))
                stack.enter_context(patch.object(runner,'liveness',return_value={}))
                stack.enter_context(patch.object(runner,'process',side_effect=process))
                stack.enter_context(patch.object(runner,'write_day'))
                stack.enter_context(patch.object(runner,'announce'))
                stack.enter_context(patch.object(status,'build'))
                stack.enter_context(patch.object(runner.time,'sleep'))
                runner.main()
            self.assertEqual(calls[:5],['0','1','2','3','4'])
            self.assertEqual(calls.count('4'),3)
            state=json.loads((root/'state.json').read_text())
            self.assertFalse(any(runner.needs_work(j) for j in state['jobs'].values()))
if __name__=='__main__': unittest.main()
