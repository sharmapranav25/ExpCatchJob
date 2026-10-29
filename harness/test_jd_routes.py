import unittest
from unittest.mock import patch,Mock
import jd
class Routes(unittest.TestCase):
 def response(self,page,url='https://example.com/job'):
  r=Mock();r.url=url;r.read.return_value=page.encode();r.__enter__=Mock(return_value=r);r.__exit__=Mock(return_value=False);return r
 def fetch(self,page,url='https://example.com/job',ats=None):
  opener=Mock();opener.open.return_value=self.response(page,url)
  with patch.object(jd,'_public_url'),patch.object(jd,'_ats_jd',side_effect=ats or ['']),patch.object(jd.urllib.request,'build_opener',return_value=opener):return jd.fetch_jd_result('https://example.com/job')
 def test_redirect_to_ats(self):
  text,source=self.fetch('', 'https://job-boards.greenhouse.io/test/jobs/1',['','Full fresh ATS description'])
  self.assertEqual(text,'Full fresh ATS description');self.assertEqual(source['method'],'redirect-ats')
 def test_jsonld(self):
  text,source=self.fetch('<script type="application/ld+json">{"@type":"JobPosting","title":"Engineer","description":"'+ 'Qualifications Python development. '*8 +'"}</script>')
  self.assertTrue(text.startswith('Engineer\n'));self.assertEqual(source['method'],'jobposting-jsonld')
 def test_multiple_postings_rejected(self):
  self.assertFalse(self.fetch('<script type="application/ld+json">[{"@type":"JobPosting"},{"@type":"JobPosting"}]</script>')[0])
 def test_shell_is_not_jd(self):self.assertFalse(self.fetch('<html>Sign in to continue</html>')[0])
 def test_http_failure_visible(self):
  with patch.object(jd,'_ats_jd',side_effect=TimeoutError('timed out')):
   self.assertIn('TimeoutError',jd.fetch_jd_result('https://example.com/job')[1]['error'])
 def test_private_redirect_rejected(self):
  with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',80))]):
   with self.assertRaises(ValueError):jd._public_url('http://localhost/job')
if __name__=='__main__':unittest.main()
