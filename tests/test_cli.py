import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from korean_job_search import cli


class CliTests(unittest.TestCase):
    def run_main(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_subprocess_help(self):
        result = subprocess.run([sys.executable, "-m", "korean_job_search", "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("answers-check", result.stdout)
        self.assertIn("discover", result.stdout)

    def test_doctor_is_offline_and_does_not_claim_model_tests(self):
        code, out, err = self.run_main(["doctor"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertFalse(data["model_sessions_tested"])
        self.assertFalse(data["network_used"])

    def test_setup_preserves_existing_non_venv_directory(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td).resolve() / "workspace"
            directory = workspace / "tools" / "scrapling"
            directory.mkdir(parents=True)
            sentinel = directory / "user-file.txt"
            sentinel.write_text("keep", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "korean_job_search", "--workspace", str(workspace), "setup"],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertFalse((directory / "pyvenv.cfg").exists())

    def test_atomic_write_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as td:
            td = str(Path(td).resolve())
            target = Path(td)/"real.json"; target.write_text("old", encoding="utf-8")
            link = Path(td)/"link.json"
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"Symlink creation unavailable: {exc}")
            with self.assertRaises(ValueError): cli.atomic_json(link, {"new": True})
            self.assertEqual(target.read_text(), "old")

    def test_bad_profile_does_not_silently_proceed(self):
        with tempfile.TemporaryDirectory() as td:
            td = str(Path(td).resolve())
            p = Path(td)/"profile.json"; p.write_text('{"keywords": "AI"}')
            with self.assertRaises(ValueError): cli.load_profile(p)

    def test_unknown_source_exit_nonzero(self):
        with patch("korean_job_search.registry.all_sources", return_value=[]):
            code,out,err = self.run_main(["collect", "--source", "does-not-exist"])
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(err))

    def test_init_and_manual_jd_workflow(self):
        with tempfile.TemporaryDirectory() as td:
            td = str(Path(td).resolve())
            ws=Path(td)/"private"
            code,out,err=self.run_main(["--workspace",str(ws),"init"])
            self.assertEqual(code,0,err)
            self.assertTrue((ws/"profile.json").exists())
            jd=Path(td)/"jd.txt"; jd.write_text("TEST FIXTURE 공고\nAI 서비스 기획 담당. 실제 채용공고 아님.",encoding="utf-8")
            code,out,err=self.run_main(["--workspace",str(ws),"ingest","--file",str(jd),"--source-url","https://example.com/job/fixture","--company","테스트 기업"])
            self.assertEqual(code,0,err)
            payload=json.loads((ws/"jobs.json").read_text())
            self.assertEqual(len(payload["jobs"]),1)
            self.assertEqual(payload["jobs"][0]["evidence"]["kind"],"manual_paste")

    def test_answers_limit_real_cli(self):
        with tempfile.TemporaryDirectory() as td:
            td = str(Path(td).resolve())
            p=Path(td)/"answer.txt"; p.write_text("가나다",encoding="utf-8")
            code,out,err=self.run_main(["answers-check",str(p),"--limit","2"])
            self.assertEqual(code,1,err)
            self.assertFalse(json.loads(out)["ok"])

    def test_track_owns_one_lock_and_manual_unknown_company(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td).resolve()/"private"
            self.assertEqual(self.run_main(["--workspace",str(ws),"init"])[0],0)
            jd = ws/"jd.txt"; jd.write_text("테스트 공고\n실제 채용공고가 아닌 오프라인 fixture",encoding="utf-8")
            code,out,err=self.run_main(["--workspace",str(ws),"ingest","--file",str(jd),"--source-url","https://example.com/job/test"])
            self.assertEqual(code,0,err)
            job=json.loads((ws/"jobs.json").read_text())["jobs"][0]
            self.assertEqual(job["company"],"Unknown employer")
            code,out,err=self.run_main(["--workspace",str(ws),"track","--job",job["id"],"--status","drafted"])
            self.assertEqual(code,0,err)
            self.assertEqual(json.loads(out)["status"],"drafted")
            self.assertFalse((ws/"applications.json.lock").exists())
            self.assertEqual(self.run_main(["--workspace",str(ws),"track","--job",job["id"],"--status","applied"])[0],2)
            self.assertEqual(self.run_main(["--workspace",str(ws),"track","--job",job["id"],"--status","applied","--confirm"])[0],0)

    def test_large_korean_answer_limit_supported(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td).resolve()/"answer.txt"; p.write_text("가나다",encoding="utf-8")
            code,out,err=self.run_main(["answers-check",str(p),"--limit","2000"])
            self.assertEqual(code,0,err)
            self.assertEqual(json.loads(out)["count"],3)

    @unittest.skipUnless(os.name == "posix", "POSIX directory permissions")
    def test_new_output_ancestors_are_private(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td).resolve()
            target=base/"workspace"/"nested"/"jobs.json"
            with cli.file_lock(target):
                cli.atomic_json(target,{"jobs":[]})
            self.assertEqual((base/"workspace").stat().st_mode & 0o777,0o700)
            self.assertEqual(target.parent.stat().st_mode & 0o777,0o700)
            self.assertEqual(target.stat().st_mode & 0o777,0o600)

    def test_lock_rejects_busy_file(self):
        with tempfile.TemporaryDirectory() as td:
            td = str(Path(td).resolve())
            path=Path(td)/"jobs.json"
            with cli.file_lock(path, timeout=0.1):
                with self.assertRaises(TimeoutError):
                    with cli.file_lock(path, timeout=0.1): pass

if __name__ == "__main__": unittest.main()
