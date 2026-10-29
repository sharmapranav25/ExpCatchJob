import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import resume_builder
class BuilderBridgeTests(unittest.TestCase):
 def record(self):return {'url':'https://example.com/job','jd_file':'jd.md','screening':{'decision':'pass'},'entry_filter':{'decision':'pass'}}
 def test_rejected_never_calls_model(self):
  with patch.object(resume_builder.subprocess,'run') as run:
   with self.assertRaises(ValueError):resume_builder.build({'screening':{'decision':'reject'}},Path('/tmp'))
   run.assert_not_called()
 def test_success_requires_manifest_and_artifacts(self):
  with tempfile.TemporaryDirectory() as d:
   def run(args,**kw):
    out=Path(args[args.index('--output')+1]);out.mkdir()
    (out/'manifest.json').write_text(json.dumps({'status':'draft_ready','model':resume_builder.MODEL,'layout':{'pages':1}}))
    for n in ['resume-draft.docx','resume-draft.pdf','evidence.md','refine-prompt.txt']:(out/n).write_text('artifact')
    return Mock(returncode=0,stdout='',stderr='')
   with patch.object(resume_builder.subprocess,'run',side_effect=run):
    a=resume_builder.build(self.record(),Path(d));b=resume_builder.build(self.record(),Path(d))
   self.assertNotEqual(a['resume_path'],b['resume_path'])
   self.assertTrue((Path(d)/a['resume_path']).is_file())
 def test_failed_builder_is_not_ready(self):
  with tempfile.TemporaryDirectory() as d,patch.object(resume_builder.subprocess,'run',return_value=Mock(returncode=1,stdout='',stderr='failure')):
   with self.assertRaises(RuntimeError):resume_builder.build(self.record(),Path(d))
if __name__=='__main__':unittest.main()
