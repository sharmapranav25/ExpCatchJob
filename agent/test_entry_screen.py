import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import run_cycle as runner
import entry_screen

class IntegrationTests(unittest.TestCase):
    def test_review_and_reject_never_start_resume_matching(self):
        for category,decision,status in [('unclear','review','needs_review'),('not_entry_level','reject','rejected_screen')]:
            with self.subTest(category=category),tempfile.TemporaryDirectory() as d:
                root=Path(d);(root/'reports').mkdir()
                level={'version':entry_screen.VERSION,'category':category,'decision':decision,'evidence':[{'line':1,'quote':'Required deep Linux internals expertise.'}]}
                with patch.object(runner,'JOBS',root),patch.object(runner,'fetch_jd',return_value='Required deep Linux internals expertise.'),patch.object(runner,'save_jd',return_value='jds/test.md'),patch.object(runner,'entry_classify',return_value=level),patch.object(runner,'evaluate') as evaluate,patch.object(runner,'h1b_lookup',return_value={'verified':False,'tier':'unknown'}):
                    result=runner.process({'company':'Test','title':'Software Engineer','url':'https://example.com'},'active')
                    self.assertEqual(result['status'],status)
                    self.assertFalse(runner.needs_work(result))
                    evaluate.assert_not_called()
    def test_bridge_rejects_bad_citations_and_version(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config.json';path.write_text(json.dumps({'python':'python','repo':d}))
            for result in [{'version':entry_screen.VERSION,'category':'not_entry_level','decision':'reject','evidence':[{'line':1,'quote':'invented requirement'}]}, {'version':'wrong','category':'entry_compatible','decision':'pass','evidence':[]}]:
                with patch.object(entry_screen,'CONFIG',path),patch.object(entry_screen.subprocess,'run',return_value=SimpleNamespace(stdout=json.dumps(result))):
                    self.assertEqual(entry_screen.classify('Engineer','Build APIs.')['decision'],'review')
    def test_review_section_has_no_apply_or_refine(self):
        import status
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'state.json';path.write_text(json.dumps({'jobs':{'x':{'company':'Test','title':'Engineer','url':'https://example.com','status':'needs_review'}}}))
            with patch.object(status,'STATE',path),patch.object(status,'OUT',Path(d)/'status.html'),patch.object(status,'agent_state',return_value='idle'):
                html=status.build().read_text()
                review=html.split('<section id="review">')[1].split('</section>')[0]
                self.assertIn('have not passed screening',review)
                self.assertNotIn('>Apply<',review)
                self.assertNotIn('Copy Refine Prompt',review)
