"""Offline contract checks, NOT model-driven agent evaluations."""
from __future__ import annotations

import importlib.util
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = Path(".agents/skills/korean-job-search")
COPIES = (
    Path(".claude/skills/korean-job-search"),
    Path(".hermes/skills/korean-job-search"),
    Path("skills/korean-job-search"),
)


def load_sync():
    spec = importlib.util.spec_from_file_location("kjs_sync_skills", ROOT / "scripts/sync_skills.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AdapterContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sync = load_sync()

    def test_checked_in_artifacts_have_no_drift(self):
        issues = self.sync.synchronize(ROOT, check=True)
        self.assertEqual(issues, [])

    def test_single_canonical_skill_and_self_contained_copies(self):
        source = ROOT / CANONICAL
        files = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        self.assertIn(Path("SKILL.md"), files)
        for dest in COPIES:
            with self.subTest(destination=str(dest)):
                self.assertEqual(files, {p.relative_to(ROOT / dest): p.read_bytes()
                                        for p in (ROOT / dest).rglob("*") if p.is_file()})
                self.assertFalse(any(p.is_symlink() for p in (ROOT / dest).rglob("*")))

    def test_skill_frontmatter_is_portable_and_selective(self):
        text = (ROOT / CANONICAL / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        self.assertIn("name: korean-job-search", frontmatter)
        self.assertRegex(frontmatter, r'description: "Use when [^\n]+"')
        for line in frontmatter.splitlines():
            if line:
                self.assertFalse(line.startswith(" "), "Use flat, single-line portable metadata")
        self.assertNotIn("allowed-tools:", frontmatter)
        self.assertNotIn("context: fork", frontmatter)

    def test_all_skill_markdown_links_resolve_within_bundle(self):
        for base in (CANONICAL,) + COPIES:
            bundle = (ROOT / base).resolve()
            for doc in bundle.rglob("*.md"):
                for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", doc.read_text(encoding="utf-8")):
                    if "://" in link or link.startswith("#"):
                        continue
                    target = (doc.parent / link.split("#", 1)[0]).resolve()
                    with self.subTest(file=str(doc.relative_to(ROOT)), link=link):
                        self.assertTrue(target.is_relative_to(bundle), "Bundle link escaped its copy")
                        self.assertTrue(target.is_file())

    def test_entrypoints_reference_repo_root_and_no_profile(self):
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(".agents/skills/korean-job-search/SKILL.md", agents)
        self.assertIn("저장소 루트", agents)
        self.assertIn("workspace/", agents)
        for name in ("CLAUDE.md", "GEMINI.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("AGENTS.md", text)
            self.assertIn(".agents/skills/korean-job-search/SKILL.md", text)
            self.assertIn("저장소 루트", text)

    def test_every_cli_command_and_count_mode_is_documented(self):
        text = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / CANONICAL).rglob("*.md"))
        commands = {"init", "sources", "discover", "collect", "ingest", "rank", "prepare",
                    "answers-check", "track", "report", "doctor"}
        found = set(re.findall(r"python3 -m korean_job_search ([a-z-]+)", text))
        self.assertEqual(commands, found)
        for mode in ("codepoints", "utf16", "utf8-bytes", "cp949-bytes"):
            self.assertIn(mode, text)
        for fragment in ("--source SOURCE_ID", "--source-url", "--all-companies", "--confirm",
                         "--jobs workspace/jobs.json", "--profile workspace/profile.json"):
            self.assertIn(fragment, text)
        # Browser calls are capability descriptions, not a hard-coded runtime API.
        for command in ("Task(", "WebSearch(", "WebFetch(", "Bash(", "Read("):
            self.assertNotIn(command, text)

    def test_safety_and_workflow_contracts(self):
        text = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / CANONICAL).rglob("*.md"))
        for token in ("개인 기여", "판단", "행동", "결과", "성찰", "블라인드", "KST", "UTC+09:00",
                      "현재 문항", "공백", "줄바꿈", "공식", "지원동기", "면접", "needs_browser",
                      "needs_credentials", "handoff", "합격 확률", "자동 제출", "비밀번호", "OTP",
                      "판단 → 행동 → 결과 → 성찰", "팀·회사", "최종 제출", "동의"):
            self.assertIn(token, text)
        for ref in ("setup", "search", "apply", "review", "interview", "privacy"):
            self.assertTrue((ROOT / CANONICAL / "references" / (ref + ".md")).is_file())

    def test_cli_examples_follow_the_shared_flag_contract(self):
        allowed = {
            "init": set(), "sources": set(), "doctor": set(),
            "discover": {"--query", "--company", "--all-companies"},
            "collect": {"--source", "--query", "--limit", "--out"},
            "ingest": {"--url", "--file", "--source-url", "--company", "--title"},
            "rank": {"--jobs", "--profile"},
            "prepare": {"--job", "--jobs", "--profile", "--out"},
            "answers-check": {"--limit", "--mode"},
            "track": {"--job", "--status", "--confirm"},
            "report": {"--jobs", "--out"},
        }
        required = {
            "discover": {"--query"}, "collect": {"--source", "--out"},
            "rank": {"--jobs", "--profile"},
            "prepare": {"--job", "--jobs", "--profile", "--out"},
            "answers-check": {"--limit", "--mode"},
            "track": {"--job", "--status", "--confirm"},
            "report": {"--jobs", "--out"},
        }
        for path in (ROOT / CANONICAL).rglob("*.md"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.startswith("python3 -m korean_job_search "):
                    continue
                parts = shlex.split(line)
                command, args = parts[3], parts[4:]
                flags = {arg for arg in args if arg.startswith("--")}
                with self.subTest(file=str(path.relative_to(ROOT)), command=line):
                    self.assertLessEqual(flags, allowed[command])
                    self.assertLessEqual(required.get(command, set()), flags)
                    if command == "ingest":
                        self.assertEqual(len(flags & {"--url", "--file"}), 1)
                        if "--file" in flags:
                            self.assertIn("--source-url", flags)
                    if command == "discover":
                        self.assertFalse({"--company", "--all-companies"} <= flags)

    def test_question_template_preserves_unknowns_instead_of_fake_answers(self):
        record = json.loads((ROOT / CANONICAL / "templates/question-record.json").read_text(encoding="utf-8"))
        self.assertEqual(record["questions"], [])
        self.assertIsNone(record["source_url"])
        self.assertIsNone(record["captured_at"])
        self.assertEqual(record["verification_status"], "unverified")
        self.assertIn("exact_prompt", record["question_fields"])
        self.assertIn("live_count", record["question_fields"])

    def test_no_local_personal_data_or_embedded_runtime_config(self):
        paths = list((ROOT / CANONICAL).rglob("*.md")) + [ROOT / name for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md")]
        paths += list((ROOT / CANONICAL).rglob("*.json"))
        for path in paths:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertNotRegex(text, r"/Users/|/home/|[A-Za-z]:[\\/](?:Users|home)[\\/]")
                self.assertNotRegex(text, r"(?i)[a-z0-9_.+-]+@(gmail|naver|daum|kakao)\.[a-z]+")
                self.assertNotRegex(text, r"(?<!\d)010[- ]?\d{4}[- ]?\d{4}(?!\d)")
                self.assertNotIn("default_model:", text)
                self.assertNotIn("api_key:", text)
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("workspace/", ignored)
        self.assertIn(".env", ignored)
        result = subprocess.run(["git", "ls-files", "workspace", "private", ".env"], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "")

    def test_compatibility_claims_distinguish_documentation_and_model_execution(self):
        doc = (ROOT / "docs/AGENTS_COMPATIBILITY.md").read_text(encoding="utf-8")
        for token in ("Hermes", "Prime Agent", "Codex", "Claude Code", "OpenClaw", "2026.2.1",
                      "모델 실행", "미실행", "trust", "/skill:korean-job-search", "--skill",
                      "--check", "Windows", "문서", "workspace"):
            self.assertIn(token, doc)
        for url in ("https://hermes-agent.nousresearch.com/docs/user-guide/features/skills",
                    "https://developers.openai.com/codex/skills",
                    "https://code.claude.com/docs/en/skills",
                    "https://docs.openclaw.ai/tools/skills",
                    "https://raw.githubusercontent.com/PrimeIntellect-ai/prime-agent/main/packages/coding-agent/docs/skills.md"):
            self.assertIn(url, doc)


class SynchronizerTests(unittest.TestCase):
    def setUp(self):
        self.sync = load_sync()
        self.temp = tempfile.TemporaryDirectory(prefix="kjs-adapters-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "한글 project"
        self.root.mkdir()
        shutil.copytree(ROOT / CANONICAL, self.root / CANONICAL)

    def test_missing_generation_and_idempotence(self):
        self.assertTrue(self.sync.synchronize(self.root, check=True))
        self.assertEqual(self.sync.synchronize(self.root), [])
        files = {p.relative_to(self.root): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.sync.synchronize(self.root), [])
        self.assertEqual(files, {p.relative_to(self.root): (p.read_bytes(), p.stat().st_mtime_ns)
                                 for p in self.root.rglob("*") if p.is_file()})

    def test_content_drift_and_missing_reference(self):
        self.sync.synchronize(self.root)
        target = self.root / COPIES[0] / "SKILL.md"
        target.write_text("drift", encoding="utf-8")
        (self.root / COPIES[1] / "references/search.md").unlink()
        issues = self.sync.synchronize(self.root, check=True)
        self.assertTrue(any("stale" in item and str(COPIES[0]).replace("\\", "/") in item for item in issues))
        self.assertTrue(any("missing" in item and "search.md" in item for item in issues))
        self.assertEqual(target.read_text(encoding="utf-8"), "drift", "--check must not write")
        self.assertEqual(self.sync.synchronize(self.root), [])
        self.assertEqual(self.sync.synchronize(self.root, check=True), [])

    def test_pointer_drift_is_detected_and_repaired(self):
        self.sync.synchronize(self.root)
        pointer = self.root / "CLAUDE.md"
        pointer.write_text("incorrect path", encoding="utf-8")
        self.assertTrue(any("CLAUDE.md" in issue for issue in self.sync.synchronize(self.root, check=True)))
        self.assertEqual(self.sync.synchronize(self.root), [])

    def test_windows_crlf_entrypoints_are_not_spurious_drift(self):
        self.sync.synchronize(self.root)
        for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
            path = self.root / name
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(self.sync.synchronize(self.root, check=True), [])

    def test_check_cli_reports_drift_without_repair(self):
        shutil.copytree(ROOT / "scripts", self.root / "scripts")
        result = subprocess.run([sys.executable, str(self.root / "scripts/sync_skills.py"), "--check"],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing:", result.stdout)
        self.assertFalse((self.root / COPIES[0]).exists())

    def test_extra_file_is_not_deleted_or_silently_accepted(self):
        self.sync.synchronize(self.root)
        extra = self.root / COPIES[2] / "user-notes.md"
        extra.write_text("private notes must not be deleted", encoding="utf-8")
        for check in (True, False):
            self.assertTrue(any("unexpected" in issue for issue in self.sync.synchronize(self.root, check=check)))
            self.assertEqual(extra.read_text(encoding="utf-8"), "private notes must not be deleted")

    def test_symlink_target_is_refused_without_external_write(self):
        outside = self.root / "outside"
        outside.mkdir()
        destination = self.root / COPIES[0]
        destination.parent.mkdir(parents=True)
        try:
            destination.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks requires OS support/privilege")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.sync.synchronize(self.root)
        self.assertEqual(list(outside.iterdir()), [])

    def test_canonical_symlink_is_refused(self):
        source = self.root / CANONICAL
        try:
            (source / "linked.md").symlink_to(source / "SKILL.md")
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks requires OS support/privilege")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.sync.synchronize(self.root)

    def test_script_works_outside_repository_working_directory(self):
        shutil.copytree(ROOT / "scripts", self.root / "scripts")
        result = subprocess.run([sys.executable, str(self.root / "scripts/sync_skills.py")],
                                cwd=self.root.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, str(self.root / "scripts/sync_skills.py"), "--check"],
                                cwd=self.root.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
