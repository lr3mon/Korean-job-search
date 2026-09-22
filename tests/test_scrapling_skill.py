"""Offline helper contracts. Optional parser tests use real Scrapling when installed."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.agents/skills/korean-job-search/scripts/scrapling_capture.py'
HAS_SCRAPLING = importlib.util.find_spec('scrapling') is not None
URL = 'https://www.jobkorea.co.kr/Search/?stext=planning'
FRAME = 'https://www.jobkorea.co.kr/Recruit/GI_Read_Comt_Ifrm?Gno=123'


def load_helper():
    module = types.ModuleType('kjs_scrapling_capture')
    module.__file__ = str(SCRIPT)
    # Do not create __pycache__ inside a byte-for-byte mirrored skill bundle.
    exec(compile(SCRIPT.read_text(), str(SCRIPT), 'exec'), module.__dict__)
    return module


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_helper()

    def test_only_public_portal_routes(self):
        for url in [URL, FRAME, 'https://www.wanted.co.kr/wdlist', 'https://www.wanted.co.kr/wd/123']:
            self.assertEqual(self.mod.public_url(url), url)
        for url in ['http://www.jobkorea.co.kr/Search/', 'https://localhost/Search/',
                    'https://127.0.0.1/Search/', 'https://www.jobkorea.co.kr/Login/',
                    'https://www.wanted.co.kr/cv/list', 'https://www.wanted.co.kr/api/apply',
                    'https://www.wanted.co.kr.evil.example/wd/123',
                    'https://name:password@www.wanted.co.kr/wd/123',
                    'https://www.wanted.co.kr/wd/123?access_token=secret']:
            with self.subTest(url=url), self.assertRaises(Exception):
                self.mod.public_url(url)

    def test_robots_failure_never_triggers_browser(self):
        for status in ['robots_denied', 'blocked', 'needs_credentials', 'error']:
            with self.subTest(status=status), mock.patch.object(self.mod, 'http_fetch', return_value={
                    'status': status, 'http_status': 403, 'diagnostics': ['Fixture policy failure']}), \
                    mock.patch.object(self.mod, 'browser_fetch') as browser:
                result = self.mod.acquire(URL, 'browser', 10, Path('.'))
                self.assertEqual(result['status'], status)
                self.assertNotIn('html', result)
                self.assertEqual(result['phase'], 'http_preflight')
                browser.assert_not_called()

    def test_http_mode_does_not_launch_browser(self):
        with mock.patch.object(self.mod, 'http_fetch', return_value={
                'status': 'ok', 'http_status': 200, 'url': URL, 'text': '<p>Fixture</p>'}), \
                mock.patch.object(self.mod, 'browser_fetch') as browser:
            result = self.mod.acquire(URL, 'http', 10, Path('.'))
        self.assertEqual(result['html'], '<p>Fixture</p>')
        self.assertEqual(result['method'], 'safe-http+scrapling-parser')
        browser.assert_not_called()

    def test_browser_failure_is_not_success(self):
        with mock.patch.object(self.mod, 'http_fetch', return_value={
                'status': 'ok', 'http_status': 200, 'url': URL, 'text': ''}), \
                mock.patch.object(self.mod, 'browser_fetch', return_value={
                    'http_status': 403, 'url': URL, 'text': 'denied'}):
            result = self.mod.acquire(URL, 'browser', 10, Path('.'))
        self.assertEqual(result['status'], 'blocked')
        self.assertNotIn('html', result)

    def test_browser_redirect_needs_exact_policy(self):
        different = URL + '&tabType=recruit'
        with mock.patch.object(self.mod, 'http_fetch', side_effect=[
                {'status': 'ok', 'http_status': 200, 'url': URL, 'text': ''},
                {'status': 'robots_denied', 'http_status': 403}]), \
                mock.patch.object(self.mod, 'browser_fetch', return_value={
                    'http_status': 200, 'url': different, 'text': '<p>Not verified</p>'}):
            result = self.mod.acquire(URL, 'browser', 10, Path('.'))
        self.assertEqual(result['status'], 'redirect_review')
        self.assertNotIn('html', result)

    def test_anonymous_browser_has_no_solver_or_profile_reuse(self):
        text = SCRIPT.read_text()
        self.assertIn('google_search=False', text)
        self.assertIn('cookies=[]', text)
        self.assertIn('user_data_dir=profile', text)
        self.assertNotIn('solve_cloudflare=True', text)
        self.assertNotIn('StealthyFetcher', text)
        self.assertNotIn('respect_robots=False', text)

    def test_paths_stay_in_private_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.assertEqual(self.mod.private_path(root, 'workspace/sources/new'), root / 'workspace/sources/new')
            for path in ['README.md', 'workspace/../outside', 'workspace']:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    self.mod.private_path(root, path)
            (root / 'workspace').mkdir()
            (root / 'outside').mkdir()
            try:
                (root / 'workspace/link').symlink_to(root / 'outside', target_is_directory=True)
            except OSError:
                self.skipTest('Symlinks unavailable')
            with self.assertRaises(ValueError):
                self.mod.private_path(root, 'workspace/link/data')

    def test_missing_dependency_is_actionable(self):
        with mock.patch.dict('sys.modules', {'scrapling': None}):
            with self.assertRaisesRegex(RuntimeError, 'isolated setup'):
                self.mod.selector_class()

    def test_saved_files_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            self.mod.save_text(path, 'original')
            with self.assertRaises(FileExistsError):
                self.mod.save_text(path, 'replacement')
            self.assertEqual(path.read_text(), 'original')


@unittest.skipUnless(HAS_SCRAPLING, 'Optional Scrapling parser environment not installed')
class ParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_helper()

    def test_jobkorea_dedupes_title_company_and_tracking_links(self):
        html = '''<a href="/Recruit/GI_Read/00123?logpath=1"><img alt="로고"></a>
        <a href="/Recruit/GI_Read/00123?logpath=2">서비스 <b>기획</b></a>
        <a href="/Recruit/GI_Read/00123">예시회사</a>
        <a href="/Recruit/GI_Read/456">다른 공고</a><a href="/Login/">로그인</a>'''
        result = self.mod.extract(html, URL, limit=1)
        self.assertEqual(result['unique_links_found'], 2)
        self.assertEqual(result['returned'], 1)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['links'][0]['posting_id'], '00123')
        self.assertIn('예시회사', result['links'][0]['labels'])
        self.assertEqual(result['status'], 'partial')

    def test_wanted_links_are_candidates_not_invented_employers(self):
        html = '<a href="/wd/321"><img alt="Product Owner">Product Owner 예시회사 서울</a>'
        result = self.mod.extract(html, 'https://www.wanted.co.kr/wdlist')
        self.assertEqual(result['links'][0]['url'], 'https://www.wanted.co.kr/wd/321')
        self.assertEqual(result['links'][0]['image_alt'], ['Product Owner'])
        self.assertNotIn('company', result['links'][0])
        self.assertEqual(result['completeness'], 'unverified')

    def test_no_links_is_not_no_jobs(self):
        result = self.mod.extract('<div id="app"></div>', URL)
        self.assertEqual(result['status'], 'parser_unsupported')
        self.assertEqual(result['returned'], 0)

    def test_parent_iframe_does_not_promote_old_essays_to_jd(self):
        html = '<iframe src="/Recruit/GI_Read_Comt_Ifrm?Gno=123"></iframe><p>과거 합격자소서</p>'
        result = self.mod.extract(html, 'https://www.jobkorea.co.kr/Recruit/GI_Read/123', kind='jd', selector='body')
        self.assertEqual(result['status'], 'needs_iframe')
        self.assertEqual(result['jd_iframe_urls'], [FRAME])
        self.assertNotIn('text', result)

    def test_jd_is_scoped_and_ignores_scripts(self):
        result = self.mod.extract('<main><section id="jd"><h2>담당업무</h2><p>서비스 기획</p>'
                                 '<script>do_evil()</script></section><aside>추천공고</aside></main>',
                                 'https://www.wanted.co.kr/wd/123', kind='jd', selector='#jd')
        self.assertIn('서비스 기획', result['text'])
        self.assertNotIn('do_evil', result['text'])
        self.assertNotIn('추천공고', result['text'])
        self.assertEqual(result['text_chars'], len(result['text']))
        self.assertEqual(result['status'], 'partial')

    def test_whole_parent_page_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'current JD container'):
            self.mod.extract('<body>Mixed content</body>', 'https://www.wanted.co.kr/wd/123', kind='jd', selector='body')

    def test_iframe_body_is_allowed_but_not_claimed_complete(self):
        result = self.mod.extract('<body><p>필요역량</p><p>검증용 문장</p></body>', FRAME, kind='jd', selector='body')
        self.assertIn('필요역량', result['text'])
        self.assertEqual(result['completeness'], 'unverified')

    def test_selector_miss_is_explicit(self):
        result = self.mod.extract('<div>Other</div>', FRAME, kind='jd', selector='#missing')
        self.assertEqual(result['status'], 'selector_empty')
        self.assertNotIn('text', result)

    def test_challenge_and_login_are_not_jds(self):
        for html in ['<title>Just a moment...</title>', '<title>403 Forbidden</title>', '<input type="password">']:
            with self.subTest(html=html):
                self.assertEqual(self.mod.extract(html, URL)['status'], 'blocked')

    def test_private_or_credential_links_are_not_exported(self):
        html = ('<a href="https://name:password@www.wanted.co.kr/wd/1">No</a>'
                '<a href="https://www.wanted.co.kr/wd/2?access_token=secret">No</a>'
                '<a href="https://127.0.0.1/wd/3">No</a>')
        self.assertEqual(self.mod.extract(html, URL)['returned'], 0)

    def test_saved_html_cli_never_fetches_and_preserves_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'korean_job_search').mkdir()
            (root / 'korean_job_search/network.py').write_text('# project-root sentinel fixture')
            (root / 'workspace').mkdir()
            (root / 'workspace/page.html').write_text('<a href="/Recruit/GI_Read/123">예시 공고</a>')
            args = ['--html', 'workspace/page.html', '--source-url', URL, '--out', 'workspace/snapshot']
            with mock.patch.object(self.mod.Path, 'cwd', return_value=root), \
                    mock.patch.object(self.mod, 'http_fetch') as network, \
                    contextlib.redirect_stdout(io.StringIO()):
                code = self.mod.main(args)
            self.assertEqual(code, 0)
            network.assert_not_called()
            snapshot = json.loads((root / 'workspace/snapshot/snapshot.json').read_text())
            self.assertEqual(snapshot['method'], 'provided-html+scrapling-parser')
            self.assertIsNone(snapshot['fetched_at'])
            self.assertIsNone(snapshot['http_status'])
            self.assertEqual(snapshot['returned'], 1)
            with mock.patch.object(self.mod.Path, 'cwd', return_value=root), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(self.mod.main(args), 2)
            self.assertEqual(json.loads((root / 'workspace/snapshot/snapshot.json').read_text()), snapshot)


if __name__ == '__main__':
    unittest.main()
