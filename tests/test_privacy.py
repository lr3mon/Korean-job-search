import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("check_privacy", Path(__file__).resolve().parents[1] / "scripts/check_privacy.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

class PrivacyTests(unittest.TestCase):
    def test_private_paths(self):
        for path in ["workspace/profile.json", "private/cv.pdf", ".env", ".env.production", "key.pem", "hidden/auth.json"]:
            self.assertTrue(module.forbidden_path(path), path)
    def test_public_templates(self):
        for path in ["korean_job_search/data/templates/profile.json", ".env.example", "README.md", "tests/test_privacy.py"]:
            self.assertFalse(module.forbidden_path(path), path)
    def test_no_real_profile_embedded_in_instructions(self):
        root = Path(__file__).resolve().parents[1]
        for name in ["AGENTS.md", "CLAUDE.md", "GEMINI.md"]:
            path = root/name
            if path.exists():
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("/Users/stpd_fx", text)
                self.assertNotIn("lr3mon@", text)

if __name__ == "__main__": unittest.main()
