import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'harness'))
from screening import screen,VERSION
from sponsorship import interpret,same_employer,lookup
import run_cycle as runner

class ScreeningTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(runner, 'entry_classify', return_value={'version':runner.ENTRY_VERSION, 'category':'entry_compatible', 'decision':'pass', 'evidence':[{'line':1,'quote':'Build APIs.'}]})
        p.start()
        self.addCleanup(p.stop)

    def test_rejections_and_exceptions(self):
        cases=[
        ('Senior Software Engineer','',True),
        ('Software Engineer','Bring 5+ years of engineering or technical deployment experience that includes customer-facing work',True),
        ('Software Engineer','5+ years of software engineering experience building integrations, backend systems, or AI-powered products',True),
        ('Software Engineer','Have an active U.S. government security clearance. While TS/SCI with polygraph is preferred, all levels will be considered.',True),
        ('Software Engineer','Minimum 1 years of experience leading a team or being part of team management.',True),
        ('Sales Development Representative','',True),
        ('Software Engineer','Bachelor degree and 5 years of experience required.',True),
        ('Software Engineer','Minimum Qualifications\n- 5+ years of software engineering experience building APIs.',True),
        ('Software Engineer','Candidates must be a U.S. citizen.',True),
        ('Software Engineer','U.S. citizenship is required.',True),
        ('Software Engineer','You will lead a team of engineers.',True),
        ('Software Engineer','No security clearance is required.',False),
        ('Software Engineer','We comply with export control regulations.',False),
        ('Software Engineer','You will work with engineers who set technical direction.',False),
        ('Software Engineer','Our team has 10 years of experience in software.',False),
        ('Software Engineer','Preferred Qualifications\n5+ years of experience\nResponsibilities\nBuild APIs.',False),
        ('Software Engineer','5 years of experience preferred.',False),
        ('Software Engineer','3 years of software engineering experience.',False),
        ('Software Engineer','4+ years of experience building APIs.',False),
        ('Software Engineer','Bachelor with 5 years of experience or Master with 2 years of experience.',False),
        ('Software Engineer','Must be a U.S. citizen or permanent resident.',False),
        ('Software Engineer','U.S. citizenship is not required.',False),
        ('Software Engineer','No visa sponsorship is available.',False),
        ('Software Engineer','Build an API integration.',False)]
        for title,jd,reject in cases:
            with self.subTest(title=title,jd=jd):
                result=screen(title,jd)
                self.assertEqual(result['decision']=='reject',reject)
                for row in result['reasons']+result['flags']:
                    self.assertTrue(row['evidence'] in jd or row['evidence']==title)
    def test_soft_flags(self):
        r=screen('Software Engineer','3+ years of experience.\nWork with mentors who set technical direction.\nSubject to ITAR export controls.')
        self.assertEqual(r['decision'],'pass');self.assertEqual(len(r['flags']),3)
    def test_name_mismatch_and_unknown_counts(self):
        self.assertFalse(same_employer('Hive','Hive Power Engineering LLC'))
        self.assertTrue(same_employer('Example','Example, Inc.'))
        self.assertFalse(same_employer('','LLC'))
        self.assertFalse(interpret('Hive',{'found':True,'displayName':'Hive Power Engineering LLC'})['verified'])
        r=interpret('Example',{'found':True,'displayName':'Example Inc','totals':{'n_lca':10,'n_perm':2}})
        self.assertTrue(r['verified']);self.assertIsNone(r['certified']);self.assertEqual(r['perm'],2)
    def test_lookup_failure_informational(self):
        with patch('sponsorship.subprocess.run',side_effect=TimeoutError('test')):
            self.assertFalse(lookup('Example','/tmp','/tmp/cache')['verified'])
    def test_reject_skips_model_and_lookup(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'reports').mkdir()
            job={'company':'Test','title':'Senior Software Engineer','url':'https://example.com'}
            with patch.object(runner,'JOBS',root),patch.object(runner,'fetch_jd',return_value='Build APIs.'),patch.object(runner,'save_jd',return_value='jds/test.md'),patch.object(runner,'evaluate') as model,patch.object(runner,'h1b_lookup') as h:
                r=runner.process(job,'active')
                self.assertEqual(r['status'],'rejected_code');model.assert_not_called();h.assert_not_called()
                self.assertFalse(runner.needs_work(r))
    def test_passing_keeps_sponsor_on_model_error(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'cv.md').write_text('CV')
            with patch.object(runner,'JOBS',root),patch.object(runner,'fetch_jd',return_value='Build APIs.'),patch.object(runner,'save_jd',return_value='jds/test.md'),patch.object(runner,'h1b_lookup',return_value={'verified':False,'tier':'unknown'}),patch.object(runner,'evaluate',side_effect=ValueError('test')):
                r=runner.process({'company':'Test','title':'Software Engineer','url':'https://example.com'},'uncertain')
                self.assertEqual(r['status'],'error');self.assertEqual(r['screening']['decision'],'pass');self.assertIn('h1b',r)
    def test_success_report_contains_context_no_rating(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'cv.md').write_text('CV');(root/'reports').mkdir()
            result={'model':'test','source_hash':'abc','calls':[],'requirements':[{'id':'R1','quote':'Build APIs.'}],
                    'matches':[{'id':'R1','level':'supported','fact_ids':['F1']}],'facts':{'F1':'Built APIs.'}}
            h={'verified':True,'employer':'Test Inc','lca':10,'certified':9,'perm':2,'years':'2020 to 2026'}
            with patch.object(runner,'JOBS',root),patch.object(runner,'fetch_jd',return_value='Build APIs.'),patch.object(runner,'save_jd',return_value='jds/test.md'),patch.object(runner,'h1b_lookup',return_value=h),patch.object(runner,'evaluate',return_value=result),patch.object(runner,'build_draft',return_value={'resume_path':'output/draft.docx'}) as builder:
                r=runner.process({'company':'Test','title':'Software Engineer','url':'https://example.com'},'active')
                text=(root/r['report_path']).read_text()
                self.assertEqual(r['status'],'local_ready');self.assertNotIn('score',r)
                builder.assert_called_once();self.assertEqual(r['resume_path'],'output/draft.docx')
                self.assertIn('10 H-1B LCA filings',text);self.assertIn('2 PERM filings',text)
                self.assertIn('Passed deterministic gates',text)
    def test_existing_ready_rescreened(self):
        self.assertTrue(runner.needs_work({'status':'local_ready'}))
        self.assertFalse(runner.needs_work({'status':'local_ready','screening':{'version':runner.SCREEN_VERSION}}))
if __name__=='__main__':unittest.main()
