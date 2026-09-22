"""네트워크와 실제 개인정보 없이 검증하는 작업 흐름 계약."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from korean_job_search.models import make_job
from korean_job_search.workflow import (
    count_text, init_workspace, load_jobs, prepare_application, rank_jobs,
    render_report, track_application, validate_answer,
)


NOW = "2026-09-22T03:00:00+00:00"


def job(number=1, **fields):
    values = {"collected_at": NOW, "deadline": "2026-10-01", **fields}
    return make_job("fixture", values.pop("title", "백엔드 개발자"),
                    values.pop("company", "테스트 회사"),
                    f"https://example.invalid/jobs/{number}",
                    values.pop("description", "Python API 개발 및 협업"), **values)


def profile(**fields):
    return {"display_name": "", "target_roles": ["백엔드"],
            "keywords": ["Python", "SQL"], "preferred_locations": ["서울"],
            "excluded_keywords": ["영업"], "experience_level": "신입",
            "stories": [], **fields}


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_json(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class WorkspaceTests(TemporaryTest):
    def test_init_empty_private_templates(self):
        result = init_workspace(self.root / "개인 작업")
        workspace = Path(result["workspace"])
        self.assertEqual(set(result["created"]), {
            "profile.json", "jobs.json", "applications.json", "story-bank.md", "answers.md", ".gitignore"})
        value = json.loads((workspace / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(value, {"display_name": "", "target_roles": [], "keywords": [],
            "preferred_locations": [], "excluded_keywords": [], "experience_level": "", "stories": []})
        self.assertEqual(load_jobs(workspace / "jobs.json"), [])
        self.assertEqual(json.loads((workspace / "applications.json").read_text()), [])
        self.assertIn("개인 기여", (workspace / "story-bank.md").read_text(encoding="utf-8"))
        self.assertIn("질문 전문", (workspace / "answers.md").read_text(encoding="utf-8"))
        self.assertIn("*", (workspace / ".gitignore").read_text())
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(workspace.stat().st_mode), 0o700)
            for path in workspace.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_init_is_idempotent_never_overwrites(self):
        workspace = self.root / "work"
        init_workspace(workspace)
        (workspace / "profile.json").write_text('{"기존": true}', encoding="utf-8")
        snapshot = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in workspace.iterdir()}
        result = init_workspace(workspace)
        self.assertEqual(result["created"], [])
        self.assertEqual({p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in workspace.iterdir()}, snapshot)

    def test_init_file_conflict_fails(self):
        target = self.root / "file"
        target.write_text("보존", encoding="utf-8")
        with self.assertRaises((ValueError, FileExistsError, NotADirectoryError)):
            init_workspace(target)
        self.assertEqual(target.read_text(encoding="utf-8"), "보존")

    @unittest.skipUnless(hasattr(os, "symlink"), "심볼릭 링크 미지원")
    def test_init_rejects_symlink_destination_and_files(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("심볼릭 링크 생성 권한 없음")
        with self.assertRaises(ValueError):
            init_workspace(link)
        outside.joinpath("profile.json").symlink_to(self.root / "absent.json")
        with self.assertRaises(ValueError):
            init_workspace(outside)
        self.assertFalse((self.root / "absent.json").exists())

    def test_load_accepts_list_and_jobs_envelope(self):
        record = job()
        for value in ([record], {"jobs": [record], "version": 1}):
            self.write_json(self.root / "jobs.json", value)
            loaded = load_jobs(self.root / "jobs.json")
            self.assertEqual(loaded[0]["id"], record["id"])
            self.assertEqual(loaded[0]["description"], record["description"])

    def test_load_accepts_utf8_bom(self):
        path = self.root / "jobs.json"
        path.write_text(json.dumps([job()], ensure_ascii=False), encoding="utf-8-sig")
        self.assertEqual(len(load_jobs(path)), 1)

    def test_load_rejects_wrong_shape_and_identity(self):
        for value in ({}, {"jobs": {}}, [1], [{"title": "정보 부족"}], [dict(job(), id="forged")]):
            with self.subTest(value=value):
                self.write_json(self.root / "jobs.json", value)
                with self.assertRaises(ValueError):
                    load_jobs(self.root / "jobs.json")

    def test_load_rejects_malformed_json(self):
        path = self.root / "jobs.json"
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_jobs(path)


class RankingTests(unittest.TestCase):
    def test_ranking_returns_explicit_heuristic_and_evidence_gaps(self):
        records = [job(2, title="고객 지원", description="문서 작성"),
                   job(1, location="서울", experience="신입")]
        before = deepcopy(records)
        ranked = rank_jobs(records, profile(), now=NOW)
        self.assertEqual(ranked[0]["id"], records[1]["id"])
        self.assertEqual(ranked[0]["rank"], 1)
        self.assertEqual(ranked[0]["score_kind"], "lexical_priority")
        self.assertIn("합격 확률", ranked[0]["score_explanation"])
        self.assertTrue(any("Python" in item for item in ranked[0]["matched"]))
        self.assertTrue(any("SQL" in item for item in ranked[0]["gaps"]))
        self.assertTrue(any("경험 근거" in item for item in ranked[0]["gaps"]))
        self.assertEqual(records, before)

    def test_expired_and_excluded_jobs_sort_last(self):
        expired = job(1, deadline="2026-09-22T11:59:59+09:00", location="서울", experience="신입")
        blocked = job(2, description="Python 영업", location="서울")
        active = job(3, title="개발", description="")
        ranked = rank_jobs([expired, blocked, active], profile(), now=NOW)
        self.assertEqual(ranked[0]["id"], active["id"])
        items = {row["id"]: row for row in ranked}
        self.assertTrue(items[expired["id"]]["expired"])
        self.assertFalse(items[expired["id"]]["eligible"])
        self.assertTrue(items[blocked["id"]]["dealbreakers"])
        self.assertFalse(items[blocked["id"]]["eligible"])

    def test_unknown_deadline_not_claimed_open(self):
        ranked = rank_jobs([job(deadline=None)], profile(), now=NOW)
        self.assertEqual(ranked[0]["deadline_state"], "unknown")
        self.assertTrue(any("마감" in value for value in ranked[0]["gaps"]))

    def test_ties_have_deterministic_input_order(self):
        records = [job(2), job(1)]
        self.assertEqual([r["id"] for r in rank_jobs(records, {}, now=NOW)], [r["id"] for r in records])

    def test_ascii_word_boundaries_case_and_korean_unicode(self):
        ranked = rank_jobs([job(description="JavaScript python 서비스기획")],
                           profile(keywords=["Java", "PYTHON", "기획"], target_roles=[]), now=NOW)
        matches = "\n".join(ranked[0]["matched"])
        self.assertNotIn("Java", matches)
        self.assertIn("PYTHON", matches)
        self.assertIn("기획", matches)

    def test_invalid_profile_rejected_without_coercion(self):
        for bad in (None, [], {"keywords": "Python"}, {"stories": ["상상"]},
                    {"excluded_keywords": [2]}, {"experience_level": 1}):
            with self.subTest(profile=bad), self.assertRaises(ValueError):
                rank_jobs([job()], bad, now=NOW)

    def test_naive_now_rejected(self):
        with self.assertRaises(ValueError):
            rank_jobs([job()], {}, now=datetime(2026, 9, 22))


class TextCountingTests(unittest.TestCase):
    def test_unicode_modes(self):
        text = "가😀A"
        self.assertEqual(count_text(text), 3)
        self.assertEqual(count_text(text, mode="utf16"), 4)
        self.assertEqual(count_text(text, mode="utf8-bytes"), 8)
        self.assertEqual(count_text("가A", mode="cp949-bytes"), 3)

    def test_no_unicode_normalization(self):
        self.assertEqual(count_text("é"), 1)
        self.assertEqual(count_text("e\u0301"), 2)
        self.assertEqual(count_text("한"), 3)

    def test_newline_policies(self):
        text = "가\r\n나\r다\n"
        self.assertEqual(count_text(text, newlines="lf"), 6)
        self.assertEqual(count_text(text, newlines="crlf"), 9)
        self.assertEqual(count_text(text, newlines="preserve"), 7)
        self.assertEqual(count_text(text, newlines="none"), 3)
        self.assertEqual(count_text("가\r\n", mode="utf16", newlines="crlf"), 3)

    def test_excluding_spaces_removes_all_unicode_whitespace(self):
        self.assertEqual(count_text(" 가\t나\r\n다\u00a0라\u3000 ", include_spaces=False), 4)
        self.assertEqual(count_text("가\u200b나", include_spaces=False), 3)

    def test_invalid_counter_options_fail(self):
        for kwargs in ({"mode": "bytes"}, {"newlines": "native"}, {"include_spaces": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                count_text("가", **kwargs)
        with self.assertRaises(ValueError):
            count_text(None)

    def test_cp949_unrepresentable_reports_error_without_crash(self):
        with self.assertRaises(UnicodeEncodeError):
            count_text("😀", mode="cp949-bytes")
        result = validate_answer("한글😀", 100, mode="cp949-bytes")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["count"])
        self.assertIsNone(result["fill"])
        self.assertTrue(result["errors"])
        self.assertIn("CP949", " ".join(result["errors"]))

    def test_validate_exact_limit_overflow_and_empty(self):
        result = validate_answer("가나다", 3)
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["fill"], 1)
        self.assertTrue(result["ok"])
        self.assertFalse(validate_answer("가나다라", 3)["ok"])
        self.assertFalse(validate_answer(" \n", 3)["ok"])

    def test_min_fill_warns_but_does_not_pad_or_fail(self):
        result = validate_answer("사실", 10)
        self.assertEqual(result["fill"], 0.2)
        self.assertTrue(result["ok"])
        self.assertTrue(result["warnings"])
        self.assertEqual(validate_answer("사실", 10, min_fill=0)["warnings"], [])

    def test_validate_draft_markers_warns(self):
        result = validate_answer("[확인 필요] TBD", 100)
        self.assertTrue(any("미완성" in item for item in result["warnings"]))

    def test_validate_invalid_limits_and_fill(self):
        for limit in (0, -1, True, 1.5, "10"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                validate_answer("가", limit)
        for fill in (-0.1, 1.1, float("nan"), True, "0.8"):
            with self.subTest(fill=fill), self.assertRaises(ValueError):
                validate_answer("가", 10, min_fill=fill)


class PacketTests(TemporaryTest):
    def test_packet_contains_jd_sources_profile_and_review_not_invented_resume(self):
        applicant = profile(stories=[{"id": "s1", "title": "테스트 경험", "period": "",
            "role": "검증 담당", "actions": ["원문 대조"], "results": [], "evidence": ["local:notes.md"]}])
        record = job(description="정확한 JD 원문\n협업 능력 필요", questions=[{"question": "본인의 기여를 기술하세요.", "limit": 500}])
        before = deepcopy((record, applicant))
        result = prepare_application(record, applicant, self.root / "지원 준비")
        self.assertEqual(result["job_id"], record["id"])
        self.assertEqual(set(result["files"]), {"job.json", "profile.json", "brief.md", "answers.md", "review.md"})
        contents = "\n".join(Path(p).read_text(encoding="utf-8") for p in result["files"].values())
        for expected in ("정확한 JD 원문", record["url"], record["evidence"]["fetched_at"],
                         "원문 대조", "local:notes.md", "본인의 기여를 기술하세요.", "500", "사람", "개인 기여", "팀·회사", "Reflection"):
            self.assertIn(expected, contents)
        self.assertIn("최종 지원서가 아닙니다", contents)
        self.assertEqual((record, applicant), before)
        if os.name == "posix":
            for path in result["files"].values():
                self.assertEqual(stat.S_IMODE(Path(path).stat().st_mode), 0o600)

    def test_packet_missing_actual_question_marks_unknown(self):
        result = prepare_application(job(), {}, self.root / "packet")
        text = Path(result["files"]["answers.md"]).read_text(encoding="utf-8")
        self.assertIn("실제 질문 미확인", text)
        self.assertIn("글자 수", text)

    def test_packet_never_overwrites(self):
        target = self.root / "packet"
        prepare_application(job(), {}, target)
        before = {p.name: p.read_bytes() for p in target.iterdir()}
        with self.assertRaises(FileExistsError):
            prepare_application(job(2), {}, target)
        self.assertEqual({p.name: p.read_bytes() for p in target.iterdir()}, before)

    def test_packet_rejects_symlink_parent(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("심볼릭 링크 생성 권한 없음")
        with self.assertRaises(ValueError):
            prepare_application(job(), {}, link / "packet")
        self.assertEqual(list(outside.iterdir()), [])

    def test_packet_invalid_profile_has_no_side_effect(self):
        target = self.root / "packet"
        with self.assertRaises(ValueError):
            prepare_application(job(), {"stories": [1]}, target)
        self.assertFalse(target.exists())

    def test_packet_fences_untrusted_jd_and_preserves_formula_text(self):
        text = "```\n<script>alert(1)</script>\n=HYPERLINK(\"x\")"
        record = job(description=text, company="../위험", title="=SUM(1,2)")
        result = prepare_application(record, {}, self.root / "packet")
        self.assertEqual(json.loads(Path(result["files"]["job.json"]).read_text(encoding="utf-8"))["description"], text)
        brief = Path(result["files"]["brief.md"]).read_text(encoding="utf-8")
        self.assertIn("````text\n" + text + "\n````", brief)
        self.assertFalse((self.root / "위험").exists())


class TrackingTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.workspace = self.root / "workspace"
        init_workspace(self.workspace)
        self.record = job()
        self.write_json(self.workspace / "jobs.json", [self.record])

    def test_draft_to_applied_requires_confirmation_then_keeps_history(self):
        drafted = track_application(self.workspace, self.record["id"], "drafted", notes="문항 확인")
        self.assertEqual(drafted["status"], "drafted")
        before = (self.workspace / "applications.json").read_bytes()
        with self.assertRaises(ValueError):
            track_application(self.workspace, self.record["id"], "applied")
        self.assertEqual((self.workspace / "applications.json").read_bytes(), before)
        applied = track_application(self.workspace, self.record["id"], "applied", notes="사람이 제출을 확인", confirm=True)
        stored = json.loads((self.workspace / "applications.json").read_text(encoding="utf-8"))
        self.assertEqual(stored, [applied])
        self.assertEqual([event["status"] for event in applied["history"]], ["drafted", "applied"])
        self.assertTrue(applied["history"][-1]["confirmed"])
        self.assertIsNotNone(datetime.fromisoformat(applied["updated_at"]).utcoffset())

    def test_all_explicit_statuses_supported(self):
        for status in ("drafted", "applied", "interview", "offer", "rejected", "withdrawn", "hired"):
            result = track_application(self.workspace, self.record["id"], status, confirm=status == "applied")
            self.assertEqual(result["status"], status)

    def test_unknown_job_invalid_status_and_boolean_confirmation_rejected(self):
        before = (self.workspace / "applications.json").read_bytes()
        for kwargs in ({"job_id": "missing", "status": "drafted"},
                       {"job_id": self.record["id"], "status": "submitted"},
                       {"job_id": self.record["id"], "status": "applied", "confirm": "yes"},
                       {"job_id": self.record["id"], "status": "drafted", "notes": None}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                track_application(self.workspace, **kwargs)
        self.assertEqual((self.workspace / "applications.json").read_bytes(), before)

    def test_tracking_preserves_application_envelope_metadata(self):
        path = self.workspace / "applications.json"
        envelope = {"version": 1, "notes": "지원 기록에 대한 사용자 메모", "applications": []}
        self.write_json(path, envelope)
        result = track_application(self.workspace, self.record["id"], "drafted")
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stored, dict(envelope, applications=[result]))

    def test_corrupt_application_file_not_replaced(self):
        path = self.workspace / "applications.json"
        path.write_text("broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            track_application(self.workspace, self.record["id"], "drafted")
        self.assertEqual(path.read_text(), "broken")
        self.assertFalse((self.workspace / "applications.json.lock").exists())

    def test_lock_prevents_lost_concurrent_updates(self):
        lock = self.workspace / "applications.json.lock"
        lock.write_text("다른 작업", encoding="utf-8")
        with self.assertRaises((ValueError, FileExistsError)):
            track_application(self.workspace, self.record["id"], "drafted")
        self.assertTrue(lock.exists())
        self.assertEqual(json.loads((self.workspace / "applications.json").read_text()), [])

    def test_formula_notes_are_only_literal_json_text(self):
        notes = '=HYPERLINK("https://example.invalid", "표시")'
        value = track_application(self.workspace, self.record["id"], "drafted", notes=notes)
        self.assertEqual(value["notes"], notes)
        self.assertEqual(list(self.workspace.glob("*.csv")), [])


class ReportTests(TemporaryTest):
    def test_report_escapes_all_untrusted_text_and_is_standalone(self):
        record = job(title='<img src=x onerror="alert(1)">', company="회사 & 동료",
                     description="<script>alert(2)</script>", location='"서울"')
        notes = "<script>alert(3)</script> =HYPERLINK(1)"
        result = render_report([record], [{"job_id": record["id"], "status": "drafted", "notes": notes}], self.root / "결과.html")
        text = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn('<html lang="ko">', text)
        self.assertIn('charset="utf-8"', text)
        self.assertNotIn("<script", text)
        self.assertNotIn("<img", text)
        self.assertIn("&lt;script&gt;alert(3)&lt;/script&gt;", text)
        self.assertIn("회사 &amp; 동료", text)
        self.assertIn("합격 확률", text)
        self.assertNotIn("<link", text)
        self.assertIn("Content-Security-Policy", text)
        self.assertEqual(result["job_count"], 1)
        self.assertEqual(result["application_count"], 1)

    def test_report_has_safe_links_and_no_overwrite(self):
        path = self.root / "report.html"
        record = job()
        render_report([record], [], path)
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            render_report([], [], path)
        self.assertEqual(path.read_bytes(), before)
        self.assertIn(b'rel="noopener noreferrer"', before)

    def test_report_empty_input_is_valid(self):
        result = render_report([], [], self.root / "empty.html")
        self.assertEqual(result["job_count"], 0)
        self.assertIn("공고가 없습니다", Path(result["path"]).read_text(encoding="utf-8"))

    def test_report_rejects_unsafe_url_and_bad_application(self):
        for records, applications in (([dict(job(), url="javascript:alert(1)")], []),
                                      ([job()], [{"job_id": job()["id"], "status": "bogus"}])):
            with self.assertRaises(ValueError):
                render_report(records, applications, self.root / "unsafe.html")
        self.assertFalse((self.root / "unsafe.html").exists())


if __name__ == "__main__":
    unittest.main()
