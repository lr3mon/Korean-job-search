import copy
import unittest
from korean_job_search.registry import validate_companies, resolve_sources, build_discovery_plan, adapter_for_url


def company(i=1, name="검증용 기업"):
    return {"id": f"kr-{i:03}", "selection_index": i, "name_ko": name,
            "name_en": "Fixture Company", "aliases": ["fixture"], "sector": "test",
            "homepage": "https://example.com/", "careers_url": "https://example.com/jobs",
            "evidence_url": "https://example.com/about", "is_group_portal": False,
            "access_mode": "html", "verification": {"checked_at": "2026-09-22T00:00:00+00:00",
            "method": "offline_test_fixture", "status": "unverified", "http_status": None,
            "title": "TEST ONLY", "notes": "Not a real employer"}}


class RegistryTests(unittest.TestCase):
    def test_valid_and_exact_count(self):
        self.assertEqual(validate_companies([company()], expected=1)["count"], 1)
        with self.assertRaises(ValueError): validate_companies([company()], expected=100)

    def test_duplicate_ids_and_names(self):
        with self.assertRaises(ValueError): validate_companies([company(), company()], expected=2)
        with self.assertRaises(ValueError): validate_companies([company(), company(2)], expected=2)

    def test_required_evidence_and_url(self):
        row = company(); row["evidence_url"] = ""
        with self.assertRaises(ValueError): validate_companies([row], expected=1)
        row = company(); row["careers_url"] = "javascript:alert(1)"
        with self.assertRaises(ValueError): validate_companies([row], expected=1)
        row = company(); row["verification"]["checked_at"] = "yesterday"
        with self.assertRaises(ValueError): validate_companies([row], expected=1)

    def test_no_rank_claim_and_status_counts(self):
        result = validate_companies([company()], expected=1)
        self.assertTrue(result["not_a_ranking"])
        self.assertEqual(result["verification_statuses"], {"unverified": 1})

    def test_literal_names_preserved(self):
        row = company(name="삼성E&A")
        self.assertEqual(resolve_sources(["삼성E&A"], [row])[0]["name_ko"], "삼성E&A")

    def test_selector_rejects_missing_ambiguous(self):
        rows = [company(1, "기업 가"), company(2, "기업 나")]
        with self.assertRaises(ValueError): resolve_sources(["없는기업"], rows)
        with self.assertRaises(ValueError): resolve_sources(["기업"], rows)
        self.assertEqual(len(resolve_sources(["기업"], rows, allow_many=True)), 2)

    def test_discovery_is_a_plan_not_fetched_jobs(self):
        result = build_discovery_plan([company()], "AI 기획")
        self.assertEqual(result["status"], "search_plan")
        self.assertEqual(result["task_count"], 1)
        self.assertIn("site:example.com", result["tasks"][0]["query"])
        self.assertNotIn("jobs", result)
        self.assertFalse(result["live_search_executed"])

    def test_exact_adapter_domains(self):
        self.assertEqual(adapter_for_url("https://careers.kakao.com/jobs"), "kakao")
        self.assertEqual(adapter_for_url("https://careers.kakao.com.evil.example/jobs"), "generic")


if __name__ == "__main__": unittest.main()
