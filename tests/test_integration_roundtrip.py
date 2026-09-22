"""Offline observation round-trips; synthetic records are explicitly fixtures."""

from copy import deepcopy
import html
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from korean_job_search.cli import save_collection
from korean_job_search.collectors import parse_job_postings
from korean_job_search.models import deadline_state, deduplicate_jobs, make_job
from korean_job_search.workflow import load_jobs, prepare_application, rank_jobs, render_report


FIXTURES = Path(__file__).parent / "fixtures"
LIST_URL = "https://recruit.navercorp.com/rcrt/loadJobList.do?firstIndex=0"
DETAIL_AT = "2026-09-22T01:00:00+00:00"
CARD_AT = "2026-09-23T01:00:00+00:00"
NOW = "2026-09-29T02:00:00Z"
DEADLINE_WARNING = "Deadline could not be normalized without guessing; preserve deadline_raw."


def fixture_parse(content, url, source_id, at):
    with patch("korean_job_search.collectors._now", return_value=at):
        records = parse_job_postings(content, url, source_id)
    # These are offline reproductions, not live acquisitions (including history).
    for record in records:
        for observation in [record, *record.get("provenance", []), *record.get("observations", [])]:
            observation["evidence"]["kind"] = "test_fixture"
    return records


def save(records, path, status="ok"):
    return save_collection([{"source_id": "offline-fixture", "status": status, "jobs": records}], path)


def synthetic(**fields):
    values: dict = dict(source_id="offline-fixture", title="SYNTHETIC observation test",
                  company="Synthetic fixture company", url="https://example.com/jobs/fixture",
                  description="Synthetic fixture, not a real vacancy.", collected_at=DETAIL_AT,
                  evidence={"kind": "test_fixture", "completeness": "full_text"})
    values.update(fields)
    return make_job(**values)


class ObservationRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "jobs.json"
        network = patch("socket.socket.connect", side_effect=AssertionError("offline test attempted network"))
        network.start()
        self.addCleanup(network.stop)

    def test_naver_collect_ingest_refresh_prepare_preserves_acquired_jd(self):
        cards = fixture_parse((FIXTURES / "collect-naver-first-page.json").read_text(encoding="utf-8"),
                              LIST_URL, "naver", CARD_AT)
        card = next(item for item in cards if "annoId=30005289" in item["url"])
        detail = fixture_parse((FIXTURES / "collect-naver-detail.html").read_text(encoding="utf-8"),
                               card["url"], "naver", DETAIL_AT)[0]
        self.assertEqual(card["description"], "")
        self.assertGreater(len(detail["description"]), 9000)
        self.assertEqual(detail["evidence"]["completeness"], "full_text")
        inputs = deepcopy([card, detail])
        save([card], self.path)
        save([detail], self.path)
        save([], self.path, "robots_denied")
        self.assertEqual(load_jobs(self.path)[0]["description"], detail["description"])
        save([card], self.path)
        stored = load_jobs(self.path)
        self.assertEqual(len(stored), 1)
        retained = stored[0]
        self.assertEqual(retained["id"], card["id"])
        self.assertEqual(retained["description"], detail["description"])
        self.assertEqual(retained["evidence"], detail["evidence"])
        self.assertEqual(retained["collected_at"], detail["collected_at"])
        self.assertTrue(any("retained" in warning.lower() for warning in retained["warnings"]))
        self.assertEqual([card, detail], inputs)
        self.assertIn(card["evidence"], [entry["evidence"] for entry in retained["observations"]])
        self.assertIn(detail["evidence"], [entry["evidence"] for entry in retained["observations"]])
        ranked = rank_jobs(stored, {}, now=NOW)
        packet = prepare_application(ranked[0], {}, self.root / "packet")
        packet_job = json.loads(Path(packet["files"]["job.json"]).read_text(encoding="utf-8"))
        self.assertEqual(packet_job["description"], detail["description"])
        self.assertEqual(packet_job["evidence"], detail["evidence"])
        brief = Path(packet["files"]["brief.md"]).read_text(encoding="utf-8")
        self.assertIn(detail["description"], brief)
        self.assertIn(DETAIL_AT, brief)
        report = render_report(stored, [], self.root / "report.html")
        report_html = Path(report["path"]).read_text(encoding="utf-8")
        self.assertIn(html.escape(detail["description"]), report_html)
        self.assertIn("근거 수집: " + DETAIL_AT, report_html)
        self.assertNotIn("근거 수집: " + CARD_AT, report_html)
        snapshot = deepcopy(stored)
        save([card], self.path)
        self.assertEqual(load_jobs(self.path), snapshot)

    def test_conflicting_observations_are_flat_unique_and_inputs_immutable(self):
        old = synthetic(description="Synthetic old full description.", questions=["SYNTHETIC old question"],
                        deadline="2026-09-28", qualifications={"status": "unknown"})
        fresh = synthetic(title="SYNTHETIC refreshed title", description="Synthetic card summary.",
                          questions=["SYNTHETIC changed question"], deadline="2026-09-30",
                          collected_at=CARD_AT,
                          evidence={"kind": "test_fixture", "completeness": "listing_card"})
        inputs = deepcopy([fresh, old])
        merged = deduplicate_jobs([fresh, old])
        self.assertEqual(merged[0]["description"], old["description"])
        self.assertEqual(merged[0]["questions"], old["questions"])
        self.assertEqual(merged[0]["qualifications"], {"status": "unknown"})
        observations = merged[0]["observations"]
        self.assertEqual(len(observations), 2)
        for original in inputs:
            observed = next(item for item in observations if item["evidence"] == original["evidence"])
            for field in ("description", "questions", "deadline", "deadline_raw", "title", "collected_at"):
                self.assertEqual(observed[field], original[field])
        for observed in observations:
            self.assertTrue({"observations", "provenance", "source_ids"}.isdisjoint(observed))
        for _ in range(20):
            self.assertEqual(deduplicate_jobs(merged + inputs), merged)
            merged = deduplicate_jobs(merged)
        self.assertEqual([fresh, old], inputs)
        merged[0]["observations"][0]["questions"].append("changed output only")
        self.assertEqual([fresh, old], inputs)

    def test_new_shorter_detail_wins_without_erasing_previous_conflicts(self):
        old = synthetic(description="Synthetic longer previous JD. " * 10, deadline="2026-09-28",
                        questions=["SYNTHETIC previous question"])
        fresh = synthetic(description="Synthetic shorter replacement JD.", collected_at=CARD_AT,
                          deadline="2026-09-30", questions=["SYNTHETIC replacement question"])
        save([old], self.path)
        save([fresh], self.path)
        stored = load_jobs(self.path)[0]
        self.assertEqual(stored["description"], fresh["description"])
        self.assertEqual(stored["questions"], fresh["questions"])
        self.assertEqual(stored["deadline"], fresh["deadline"])
        self.assertEqual(stored["evidence"], fresh["evidence"])
        self.assertEqual(len(stored["observations"]), 2)
        self.assertIn(old["description"], [item["description"] for item in stored["observations"]])

    def test_explicit_unknown_deadline_survives_every_workflow_boundary(self):
        # Explicitly synthetic JSON-LD, never represented as an actual vacancy.
        posting = {"@type": "JobPosting", "title": "SYNTHETIC timezone fixture",
                   "hiringOrganization": {"name": "Synthetic fixture company"},
                   "description": "Synthetic offline deadline test, not a real job.",
                   "validThrough": "2026-09-29T10:00:00",
                   "url": "https://example.com/jobs/timezone-fixture"}
        document = '<script type="application/ld+json">' + json.dumps(posting) + '</script>'
        parsed = fixture_parse(document, posting["url"], "offline-fixture", DETAIL_AT)[0]
        self.assertIsNone(parsed["deadline"])
        self.assertIn(DEADLINE_WARNING, parsed["warnings"])
        before = deepcopy(parsed)
        save([parsed], self.path)
        stored = json.loads(self.path.read_text(encoding="utf-8"))["jobs"][0]
        loaded = load_jobs(self.path)[0]
        ranked = rank_jobs([loaded], {}, now=NOW)[0]
        packet = prepare_application(ranked, {}, self.root / "packet")
        packet_job = json.loads(Path(packet["files"]["job.json"]).read_text(encoding="utf-8"))
        with patch("korean_job_search.models.now_iso", return_value=NOW):
            report = render_report([ranked], [], self.root / "report.html")
        report_html = Path(report["path"]).read_text(encoding="utf-8")
        save([], self.path, "robots_denied")
        resaved = json.loads(self.path.read_text(encoding="utf-8"))["jobs"][0]
        for stage, record in (("parsed", parsed), ("stored", stored), ("loaded", loaded),
                              ("ranked", ranked), ("prepared", packet_job), ("resaved", resaved)):
            with self.subTest(stage=stage):
                self.assertIsNone(record["deadline"])
                self.assertEqual(record["deadline_raw"], posting["validThrough"])
                self.assertEqual(deadline_state(record, now=NOW), "unknown")
                self.assertIn(DEADLINE_WARNING, record["warnings"])
        self.assertEqual(ranked["deadline_state"], "unknown")
        self.assertFalse(ranked["expired"])
        self.assertIn("마감: " + posting["validThrough"] + " · 미확인", report_html)
        self.assertNotIn("마감 지남", report_html)
        self.assertIn(DEADLINE_WARNING, report_html)
        self.assertEqual(parsed, before)

    def test_absent_normalized_deadline_and_explicit_offset_still_normalize(self):
        absent = synthetic(deadline_raw="2026년 9월 30일")
        explicit = synthetic(deadline="2026-09-29T10:00:00Z")
        self.assertEqual(absent["deadline"], "2026-09-30")
        self.assertEqual(explicit["deadline"], "2026-09-29T10:00:00+00:00")
        for index, original in enumerate((absent, explicit)):
            path = self.root / f"known-{index}.json"
            save([original], path)
            self.assertEqual(load_jobs(path)[0]["deadline"], original["deadline"])

    def test_explicit_null_is_not_the_same_as_absent_normalized_deadline(self):
        raw = "2026-09-29T10:00:00"
        self.assertEqual(synthetic(deadline_raw=raw)["deadline"], raw + "+09:00")
        self.assertIsNone(synthetic(deadline=None, deadline_raw=raw)["deadline"])


if __name__ == "__main__":
    unittest.main()
