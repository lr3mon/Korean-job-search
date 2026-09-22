"""채용공고 모델의 정규화·검증·마감 경계를 오프라인으로 확인한다."""

import copy
import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfoNotFoundError

from korean_job_search import models
from korean_job_search.models import (
    canonical_url,
    deadline_state,
    deduplicate_jobs,
    make_job,
    now_iso,
)


UTC = timezone.utc
KST = timezone(timedelta(hours=9), "Asia/Seoul")
COLLECTED = "2026-09-22T09:10:11.123456+09:00"


def job(**fields):
    """현재 시각에 의존하지 않는 공고를 만든다."""
    values = {
        "source_id": "공식-채용",
        "title": "파이썬 개발자 🐍",
        "company": "한글 회사",
        "url": "https://example.com/Jobs/42",
        "description": "서울에서 함께 일할 개발자를 찾습니다.\n지원해 주세요.",
        "collected_at": COLLECTED,
    }
    values.update(fields)
    return make_job(**values)


class CanonicalURLTests(unittest.TestCase):
    def test_normalizes_scheme_host_default_port_and_fragment(self):
        self.assertEqual(
            canonical_url("HTTPS://EXAMPLE.COM:443/Jobs/AbC#apply"),
            "https://example.com/Jobs/AbC",
        )
        self.assertEqual(canonical_url("http://EXAMPLE.COM:80"), "http://example.com/")

    def test_removes_known_trackers_but_keeps_job_identifiers(self):
        self.assertEqual(
            canonical_url(
                "https://example.com/jobs?utm_source=x&jobId=AbC&gclid=y&"
                "source=official&fbclid=z&ref=42&UTM_CAMPAIGN=test&msclkid=3"
            ),
            "https://example.com/jobs?jobId=AbC&source=official&ref=42",
        )

    def test_removes_encoded_tracker_keys_and_new_ad_identifiers(self):
        self.assertEqual(
            canonical_url(
                "https://example.com/?%75tm_medium=email&gbraid=x&wbraid=x&"
                "dclid=x&mc_cid=x&mc_eid=x&_ga=x&_gl=x&srsltid=x&"
                "gad_source=1&gad_campaignid=2&ttclid=x&twclid=x&yclid=x&igshid=x&id=7"
            ),
            "https://example.com/?id=7",
        )

    def test_preserves_query_order_duplicates_empty_values_and_encoding(self):
        value = "https://example.com/Jobs/?b=2&a=2&a=1&empty=&flag&q=%ED%95%9C+%EA%B8%80&x=%2f"
        self.assertEqual(canonical_url(value), value)

    def test_preserves_unicode_and_meaningful_path_case_and_slashes(self):
        value = "https://예시.한국/채용/개발자/?직무=파이썬"
        self.assertEqual(canonical_url(value), value)
        self.assertNotEqual(canonical_url("https://e.test/Job"), canonical_url("https://e.test/job"))
        self.assertNotEqual(canonical_url("https://e.test/job/"), canonical_url("https://e.test/job"))
        self.assertEqual(canonical_url("https://e.test/a%2Fb/../c;v=1"), "https://e.test/a%2Fb/../c;v=1")

    def test_preserves_nondefault_ports_and_ipv6(self):
        self.assertEqual(canonical_url("https://EXAMPLE.com:8443/jobs"), "https://example.com:8443/jobs")
        self.assertEqual(canonical_url("http://[2001:db8::1]:80/jobs"), "http://[2001:db8::1]/jobs")

    def test_rejects_missing_or_non_http_absolute_urls(self):
        for value in (None, 42, "", "/jobs/42", "//example.com/jobs", "example.com", "ftp://e.test/a", "file:///a", "javascript:alert(1)", "https:///jobs", "https://"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_url(value)

    def test_rejects_credentials_even_when_username_is_empty(self):
        for value in ("https://user:secret@e.test/", "https://user@e.test/", "https://@e.test/", "https://:secret@e.test/"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_url(value)

    def test_rejects_whitespace_control_and_invisible_format_characters(self):
        for character in (" ", "\t", "\n", "\r", "\x00", "\x7f", "\u00a0", "\u200b", "\u202e", "\ud800"):
            for value in (character + "https://e.test/", "https://e.test/jobs?x=" + character, "https://e.test/#" + character):
                with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                    canonical_url(value)

    def test_rejects_malformed_host_port_and_percent_encoding(self):
        for value in (
            "https://e.test:/", "https://e.test:65536/", "https://e.test:-1/",
            "https://e.test:abc/", "https://[invalid]/", "https://[::1/jobs",
            "https://-bad.test/", "https://bad_.test/", "https://a..test/",
            "https://e.test%2f.evil/", "https://e.test/50%", "https://e.test/%xy",
            "https://e.test/%0Aheader", "https://e.test/?x=%00",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_url(value)
        self.assertEqual(canonical_url("https://e.test/jobs/hello%20world"), "https://e.test/jobs/hello%20world")

    def test_rejects_ambiguous_bracketed_authorities_instead_of_repairing_them(self):
        for value in (
            "https://[::1]evil.test/jobs", "https://[::1]garbage:443/jobs",
            "https://[::1].evil.test/jobs", "https://example.test[::1]/jobs",
            "https://[::1]:443:444/jobs", "https://e.test\\evil/jobs",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_url(value)

    def test_canonicalization_is_idempotent(self):
        for value in ("HTTPS://E.TEST:443?utm_source=x&id=1#top", "https://예시.한국/채용?x=1", "http://[::1]:8080/"):
            with self.subTest(value=value):
                normalized = canonical_url(value)
                self.assertEqual(canonical_url(normalized), normalized)


class MakeJobTests(unittest.TestCase):
    def test_required_fields_and_evidence_defaults(self):
        result = job(url="https://EXAMPLE.com/Jobs/42?utm_source=email")
        required = {
            "id", "source_id", "company", "title", "url", "description", "location",
            "employment_type", "experience", "deadline", "deadline_raw", "collected_at",
            "evidence", "warnings",
        }
        self.assertTrue(required.issubset(result))
        self.assertEqual(result["url"], "https://example.com/Jobs/42")
        self.assertEqual(result["collected_at"], COLLECTED)
        self.assertEqual(result["evidence"], {
            "kind": "source",
            "source_url": "https://EXAMPLE.com/Jobs/42?utm_source=email",
            "fetched_at": COLLECTED,
        })
        self.assertIsNone(result["deadline"])
        self.assertEqual(result["deadline_raw"], "")
        self.assertEqual(result["warnings"], [])
        for key in ("location", "employment_type", "experience"):
            self.assertEqual(result[key], "")

    def test_default_collection_time_is_aware(self):
        result = make_job("site", "개발자", "회사", "https://e.test/job")
        self.assertIsNotNone(datetime.fromisoformat(result["collected_at"]).utcoffset())
        self.assertEqual(result["evidence"]["fetched_at"], result["collected_at"])

    def test_id_is_full_sha256_of_canonical_url(self):
        result = job()
        expected = hashlib.sha256(result["url"].encode("utf-8")).hexdigest()
        self.assertEqual(result["id"], expected)

    def test_tracking_source_and_description_do_not_change_identity(self):
        first = job(url="HTTPS://EXAMPLE.com:443/Jobs/42?utm_source=one#top")
        second = job(source_id="another", title="다른 제목", description="", url="https://example.com/Jobs/42?fbclid=two")
        self.assertEqual(first["id"], second["id"])

    def test_identifying_query_path_and_unicode_change_identity(self):
        values = [
            "https://e.test/jobs?id=1", "https://e.test/jobs?id=2",
            "https://e.test/Jobs?id=1", "https://e.test/채용/개발자",
            "https://e.test/채용/디자이너",
        ]
        identifiers = {job(url=value)["id"] for value in values}
        self.assertEqual(len(identifiers), len(values))

    def test_preserves_unicode_text_and_optional_values(self):
        result = job(location="서울·재택", employment_type="정규직", experience="경력 무관")
        self.assertEqual(result["title"], "파이썬 개발자 🐍")
        self.assertIn("\n", result["description"])
        self.assertEqual(result["location"], "서울·재택")
        self.assertEqual(result["employment_type"], "정규직")
        self.assertEqual(result["experience"], "경력 무관")

    def test_rejects_blank_or_nonstring_required_text(self):
        for field in ("source_id", "title", "company"):
            for value in (None, "", " \n", 1, []):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    job(**{field: value})

    def test_rejects_nonstring_optional_text_and_invalid_url(self):
        for field in ("description", "location", "employment_type", "experience"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                job(**{field: []})
        with self.assertRaises(ValueError):
            job(url="/not-an-absolute-url")

    def test_does_not_allow_supplied_id_to_override_identity(self):
        with self.assertRaises(ValueError):
            job(id="invented")

    def test_requires_aware_collection_timestamp(self):
        for value in ("2026-09-22", "2026-09-22T12:30:00", datetime(2026, 9, 22), "yesterday", "2026-09-22T12:00:00+09:99", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                job(collected_at=value)
        result = job(collected_at=datetime(2026, 9, 22, 12, 30, tzinfo=KST))
        self.assertEqual(result["collected_at"], "2026-09-22T12:30:00+09:00")

    def test_accepts_aware_iso_timestamps_without_losing_fraction_digits(self):
        result = job(collected_at="2026-09-22T00:10:11.123456789Z")
        self.assertEqual(result["collected_at"], "2026-09-22T00:10:11.123456789+00:00")

    def test_validates_evidence_and_preserves_evidence_metadata(self):
        evidence = {"kind": "detail", "source_url": "https://example.com/Detail?id=42&utm_source=proof", "fetched_at": "2026-09-22T00:00:00Z", "selector": "main"}
        result = job(evidence=evidence)
        self.assertEqual(result["evidence"]["source_url"], evidence["source_url"])
        self.assertEqual(result["evidence"]["fetched_at"], "2026-09-22T00:00:00+00:00")
        self.assertEqual(result["evidence"]["selector"], "main")
        self.assertEqual(result["evidence"]["kind"], "detail")

    def test_rejects_invalid_evidence_fields(self):
        for value in (None, [], "source", {"kind": ""}, {"source_url": "file:///tmp/page"}, {"fetched_at": "2026-09-22T12:00:00"}, {"fetched_at": None}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                job(evidence=value)

    def test_warnings_must_be_a_collection_of_nonblank_strings(self):
        for value in (None, "문자열", [42], [""]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                job(warnings=value)
        self.assertEqual(job(warnings=["확인 필요"])["warnings"], ["확인 필요"])

    def test_inputs_and_nested_extensions_are_not_mutated_or_aliased(self):
        fields = {"warnings": ["확인 필요"], "evidence": {"kind": "detail", "metadata": {"pages": [1]}}, "metadata": {"tags": ["한국어"]}}
        before = copy.deepcopy(fields)
        result = job(**fields)
        self.assertEqual(fields, before)
        result["warnings"].append("새 경고")
        result["evidence"]["metadata"]["pages"].append(2)
        result["metadata"]["tags"].append("영어")
        self.assertEqual(fields, before)

    def test_errors_are_user_facing_korean(self):
        with self.assertRaisesRegex(ValueError, "[가-힣]"):
            canonical_url("ftp://e.test/")
        with self.assertRaisesRegex(ValueError, "[가-힣]"):
            job(title="")


class DeadlineParsingTests(unittest.TestCase):
    def test_rebuilding_unknown_deadline_does_not_duplicate_generated_warning(self):
        first = job(deadline_raw="마감일 미정")
        fields = {key: value for key, value in first.items() if key != "id"}
        second = make_job(**fields)
        self.assertEqual(second, first)

    def test_iso_date_only_stays_date_only(self):
        result = job(deadline="2026-09-30")
        self.assertEqual(result["deadline"], "2026-09-30")
        self.assertEqual(result["deadline_raw"], "2026-09-30")

    def test_full_korean_and_dotted_dates(self):
        for raw, expected in (
            ("2026년 9월 30일", "2026-09-30"),
            ("2026.09.30", "2026-09-30"),
            ("2026.9.3", "2026-09-03"),
            ("2024년 2월 29일", "2024-02-29"),
            (" 2026년 9월 30일 ", "2026-09-30"),
        ):
            with self.subTest(raw=raw):
                result = job(deadline_raw=raw)
                self.assertEqual(result["deadline"], expected)
                self.assertEqual(result["deadline_raw"], raw)

    def test_naive_full_timestamps_assume_seoul_and_keep_precision(self):
        for raw, expected in (
            ("2026-09-30T18:30", "2026-09-30T18:30+09:00"),
            ("2026-09-30 18:30:01.1200", "2026-09-30T18:30:01.1200+09:00"),
            ("2026년 9월 30일 18:30", "2026-09-30T18:30+09:00"),
            ("2026.09.30 18:30:01", "2026-09-30T18:30:01+09:00"),
            ("2026년 9월 30일 18시 30분", "2026-09-30T18:30+09:00"),
            ("2026년 9월 30일 18시 30분 1초", "2026-09-30T18:30:01+09:00"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(job(deadline=raw)["deadline"], expected)

    def test_explicit_timestamp_offset_and_submicrosecond_digits_are_kept(self):
        for raw, expected in (
            ("2026-09-30T18:30:01.123456789+09:00", "2026-09-30T18:30:01.123456789+09:00"),
            ("2026-09-30T09:30:01.000000001Z", "2026-09-30T09:30:01.000000001+00:00"),
            ("2026-09-30T09:30:00-04:30", "2026-09-30T09:30:00-04:30"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(job(deadline=raw)["deadline"], expected)

    def test_explicit_raw_text_is_never_replaced(self):
        result = job(deadline="2026-09-30", deadline_raw="원문: 2026년 9월 30일까지")
        self.assertEqual(result["deadline"], "2026-09-30")
        self.assertEqual(result["deadline_raw"], "원문: 2026년 9월 30일까지")

    def test_absent_deadline_is_unknown_not_open(self):
        for fields in ({}, {"deadline": None}, {"deadline": ""}, {"deadline_raw": ""}):
            with self.subTest(fields=fields):
                result = job(**fields)
                self.assertIsNone(result["deadline"])
                self.assertEqual(deadline_state(result, now=COLLECTED), "unknown")
                self.assertEqual(result["warnings"], [])

    def test_incomplete_relative_and_malformed_dates_are_not_invented(self):
        values = (
            "9월 30일", "09.30", "09-30", "내일", "오늘 18시", "D-3", "3일 남음", "마감 임박",
            "2026", "2026-09", "2026-9-30", "2026-02-29", "2026-13-01", "2026-04-31",
            "2026-09-30T", "2026-09-30T24:00:00", "2026-09-30T12:60:00", "2026-09-30T12:00:60",
            "2026-09-30T12:00:00+09:99", "2026-09-30T12:00:00+24:00", "2026-09-30T12:00:00.",
            "2026-09-30garbage", "2026.09.30 18:30 extra", "2026년 9월 30일 25시 00분",
            "2026-09-30/2026-10-01", "2026-09-30+09:00", "２０２６-０９-３０",
        )
        for raw in values:
            with self.subTest(raw=raw):
                result = job(deadline=raw)
                self.assertIsNone(result["deadline"])
                self.assertEqual(result["deadline_raw"], raw)
                self.assertTrue(result["warnings"])
                self.assertEqual(deadline_state(result, now=COLLECTED), "unknown")

    def test_explicit_rolling_terms_only(self):
        for raw in ("채용시 마감", "채용 시 마감", "상시", "상시채용", "상시 채용", "상시 모집"):
            with self.subTest(raw=raw):
                result = job(deadline=raw)
                self.assertIsNone(result["deadline"])
                self.assertEqual(result["deadline_raw"], raw)
                self.assertEqual(result["warnings"], [])
                self.assertEqual(deadline_state(result, now=COLLECTED), "rolling")
        for raw in ("상시 아님", "상시근무", "채용시 마감 여부 미정", "마감일 미정", "협의"):
            with self.subTest(raw=raw):
                self.assertEqual(deadline_state(job(deadline=raw), now=COLLECTED), "unknown")

    def test_invalid_deadline_field_types_are_rejected(self):
        for fields in ({"deadline": 20260930}, {"deadline": []}, {"deadline_raw": None}, {"deadline_raw": []}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                job(**fields)

    def test_invalid_deadline_warning_preserves_existing_warnings(self):
        result = job(deadline="9월 30일", warnings=["원문 확인 필요"])
        self.assertEqual(result["warnings"][0], "원문 확인 필요")
        self.assertGreater(len(result["warnings"]), 1)

    def test_null_deadline_can_be_parsed_from_raw_when_building_job(self):
        self.assertEqual(job(deadline=None, deadline_raw="2026년 9월 30일")["deadline"], "2026-09-30")


class DeadlineStateTests(unittest.TestCase):
    def test_date_only_includes_entire_seoul_calendar_day(self):
        value = job(deadline="2026-09-30")
        for now in ("2026-09-29T23:59:59+09:00", "2026-09-30T00:00:00+09:00", "2026-09-30T23:59:59.999999999+09:00", "2026-09-30T14:59:59.999999Z"):
            with self.subTest(now=now):
                self.assertEqual(deadline_state(value, now=now), "open")
        self.assertEqual(deadline_state(value, now="2026-09-30T15:00:00Z"), "expired")

    def test_date_only_uses_seoul_instead_of_caller_timezone(self):
        value = job(deadline="2026-09-30")
        self.assertEqual(deadline_state(value, now="2026-09-30T10:59:59-04:00"), "open")
        self.assertEqual(deadline_state(value, now="2026-09-30T11:00:00-04:00"), "expired")

    def test_exact_timestamp_is_open_at_equality_and_expires_afterward(self):
        value = job(deadline="2026-09-30T18:30:01.123456+09:00")
        self.assertEqual(deadline_state(value, now="2026-09-30T09:30:01.123455Z"), "open")
        self.assertEqual(deadline_state(value, now="2026-09-30T09:30:01.123456Z"), "open")
        self.assertEqual(deadline_state(value, now="2026-09-30T09:30:01.123457Z"), "expired")

    def test_submicrosecond_comparisons_do_not_round_away_precision(self):
        value = job(deadline="2026-09-30T18:30:01.123456789+09:00")
        self.assertEqual(deadline_state(value, now="2026-09-30T09:30:01.123456788Z"), "open")
        self.assertEqual(deadline_state(value, now="2026-09-30T09:30:01.123456789Z"), "open")
        self.assertEqual(deadline_state(value, now="2026-09-30T09:30:01.123456790Z"), "expired")
        self.assertEqual(deadline_state(value, now=datetime(2026, 9, 30, 9, 30, 1, 123457, UTC)), "expired")

    def test_fractional_trailing_zeros_and_long_precision_remain_exact(self):
        value = job(deadline="2026-09-30T18:30:01.1234567890+09:00")
        self.assertEqual(deadline_state(value, now="2026-09-30T09:30:01.123456789Z"), "open")
        fraction = "1" * 5000
        value = job(deadline="2026-09-30T18:30:01." + fraction + "+09:00")
        self.assertEqual(value["deadline"], "2026-09-30T18:30:01." + fraction + "+09:00")
        self.assertEqual(deadline_state(value, now="2026-09-30T09:30:01." + fraction + "Z"), "open")
        self.assertEqual(deadline_state(value, now="2026-09-30T09:30:01." + fraction[:-1] + "2Z"), "expired")

    def test_fractional_timezone_offsets_compare_without_rounding(self):
        value = job(deadline="2026-09-30T18:30:01.123456789+09:00:00.5")
        self.assertEqual(deadline_state(value, now="2026-09-30T09:30:00.623456789Z"), "open")
        self.assertEqual(deadline_state(value, now="2026-09-30T09:30:00.623456790Z"), "expired")

    def test_date_only_handles_calendar_extremes_without_overflow(self):
        self.assertEqual(deadline_state({"deadline": "9999-12-31"}, now="9999-12-31T23:59:59Z"), "expired")
        self.assertEqual(deadline_state({"deadline": "9999-12-31"}, now="9999-12-31T14:59:59.9999999999Z"), "open")
        self.assertEqual(deadline_state({"deadline": "9999-12-31"}, now="9999-12-31T15:00:00Z"), "expired")
        self.assertEqual(deadline_state({"deadline": "0001-01-01"}, now="0001-01-01T00:00:00+23:59"), "open")

    def test_date_only_includes_repeated_last_hour_of_historical_seoul_day(self):
        self.assertEqual(deadline_state({"deadline": "1948-09-12"}, now="1948-09-12T14:30:00Z"), "open")
        self.assertEqual(deadline_state({"deadline": "1948-09-12"}, now="1948-09-12T15:00:00Z"), "expired")

    def test_accepts_aware_datetime_now(self):
        self.assertEqual(deadline_state(job(deadline="2026-09-30"), now=datetime(2026, 9, 30, 23, 59, tzinfo=KST)), "open")

    def test_rejects_naive_or_invalid_now_even_for_unknown_deadlines(self):
        for now in (datetime(2026, 9, 30), "2026-09-30T12:00:00", "2026-09-30", "내일", "2026-09-30T12:00:00+09:99", 42):
            with self.subTest(now=now), self.assertRaises(ValueError):
                deadline_state(job(), now=now)

    def test_unknown_raw_date_is_not_a_substitute_for_missing_normalized_deadline(self):
        self.assertEqual(deadline_state({"deadline": None, "deadline_raw": "2026-09-30"}, now=COLLECTED), "unknown")
        self.assertEqual(deadline_state({"deadline": "bad", "deadline_raw": ""}, now=COLLECTED), "unknown")

    def test_valid_deadline_takes_precedence_over_rolling_raw_text(self):
        value = job(deadline="2026-09-01", deadline_raw="상시")
        self.assertEqual(deadline_state(value, now=COLLECTED), "expired")

    def test_does_not_mutate_job(self):
        value = job(deadline="2026-09-30")
        before = copy.deepcopy(value)
        deadline_state(value, now=COLLECTED)
        self.assertEqual(value, before)

    def test_rejects_nonmapping_job(self):
        with self.assertRaisesRegex(ValueError, "[가-힣]"):
            deadline_state([], now=COLLECTED)

    def test_zoneinfo_missing_data_has_stdlib_seoul_fallback(self):
        with mock.patch.object(models, "ZoneInfo", side_effect=ZoneInfoNotFoundError("Asia/Seoul")):
            fallback = models._seoul_timezone()
        self.assertEqual(fallback.utcoffset(None), timedelta(hours=9))
        self.assertEqual(fallback.tzname(None), "Asia/Seoul")
        with mock.patch.object(models, "SEOUL", fallback):
            self.assertEqual(job(deadline="2026-09-30T18:00")["deadline"], "2026-09-30T18:00+09:00")
            self.assertEqual(deadline_state(job(deadline="2026-09-30"), now="2026-09-30T15:00:00Z"), "expired")


class DeduplicationTests(unittest.TestCase):
    def test_empty_iterable_and_generator(self):
        self.assertEqual(deduplicate_jobs([]), [])
        values = [job(), job(url="https://example.com/Jobs/43")]
        self.assertEqual(len(deduplicate_jobs(value for value in values)), 2)

    def test_keeps_first_seen_record_and_order(self):
        first = job(title="첫 공고", description="첫 설명", warnings=["첫 경고"])
        other = job(url="https://example.com/Jobs/43", title="다른 공고")
        duplicate = job(source_id="두번째-출처", title="나중 제목", description="나중 설명", url="https://EXAMPLE.com:443/Jobs/42?utm_source=x", warnings=["나중 경고", "첫 경고"])
        result = deduplicate_jobs([first, other, duplicate])
        self.assertEqual([value["id"] for value in result], [first["id"], other["id"]])
        self.assertEqual(result[0]["title"], "첫 공고")
        self.assertEqual(result[0]["description"], "첫 설명")
        self.assertEqual(result[0]["source_id"], first["source_id"])
        self.assertEqual(result[0]["evidence"], first["evidence"])
        self.assertEqual(result[0]["warnings"], ["첫 경고", "나중 경고"])

    def test_merges_all_source_ids_and_evidence_provenance(self):
        first = job()
        second = job(source_id="다른-출처", evidence={"kind": "detail", "source_url": "https://mirror.test/jobs/42", "fetched_at": "2026-09-22T01:00:00Z"})
        third = job(source_id="다른-출처", evidence={"kind": "detail", "source_url": "https://mirror.test/jobs/42", "fetched_at": "2026-09-23T01:00:00Z"})
        merged = deduplicate_jobs([first, second, third, second])[0]
        self.assertEqual(merged["source_ids"], ["공식-채용", "다른-출처"])
        self.assertEqual(len(merged["provenance"]), 3)
        self.assertEqual([entry["evidence"] for entry in merged["provenance"]], [first["evidence"], second["evidence"], third["evidence"]])
        self.assertEqual(merged["provenance"][0]["collected_at"], COLLECTED)

    def test_never_merges_distinct_urls_just_for_matching_title_and_company(self):
        values = [job(url="https://e.test/jobs?id=1"), job(url="https://e.test/jobs?id=2"), job(url="https://another.test/jobs?id=1")]
        self.assertEqual(len(deduplicate_jobs(values)), 3)

    def test_normalizes_urls_in_existing_records_and_recomputes_id(self):
        value = job()
        value["url"] = "HTTPS://EXAMPLE.COM:443/Jobs/42?utm_source=mail#top"
        value["id"] = "not-authoritative"
        result = deduplicate_jobs([value])[0]
        self.assertEqual(result["url"], "https://example.com/Jobs/42")
        self.assertEqual(result["id"], job()["id"])

    def test_results_do_not_mutate_or_alias_any_input(self):
        first = job(metadata={"tags": ["첫"]}, evidence={"metadata": {"page": [1]}})
        second = job(source_id="둘", warnings=["경고"], evidence={"metadata": {"page": [2]}})
        values = [first, second]
        before = copy.deepcopy(values)
        result = deduplicate_jobs(values)
        self.assertEqual(values, before)
        result[0]["metadata"]["tags"].append("새 값")
        result[0]["warnings"].append("새 경고")
        result[0]["provenance"][1]["evidence"]["metadata"]["page"].append(3)
        self.assertEqual(values, before)

    def test_repeated_dedup_is_deterministic_and_preserves_provenance(self):
        values = [job(warnings=["첫 경고"]), job(source_id="둘", warnings=["둘째 경고"])]
        merged = deduplicate_jobs(values)
        self.assertEqual(deduplicate_jobs(values), merged)
        self.assertEqual(deduplicate_jobs(merged), merged)
        self.assertEqual(deduplicate_jobs(merged + values), merged)

    def test_preserves_new_collection_provenance_for_the_same_source(self):
        merged = deduplicate_jobs([job(), job(collected_at="2026-09-23T09:10:11+09:00")])[0]
        self.assertEqual(merged["source_ids"], ["공식-채용"])
        self.assertEqual(len(merged["provenance"]), 2)

    def test_invalid_records_and_warning_shapes_are_rejected(self):
        for values in (None, "jobs", {}, [None], [{"url": "ftp://e.test/", "source_id": "x"}], [{"url": "https://e.test/", "source_id": ""}], [dict(job(), warnings="bad")]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                deduplicate_jobs(values)


class ClockTests(unittest.TestCase):
    def test_now_iso_is_aware_utc_and_between_real_clock_samples(self):
        before = datetime.now(UTC)
        result = datetime.fromisoformat(now_iso())
        after = datetime.now(UTC)
        self.assertEqual(result.utcoffset(), timedelta(0))
        self.assertLessEqual(before, result)
        self.assertLessEqual(result, after)


if __name__ == "__main__":
    unittest.main()
