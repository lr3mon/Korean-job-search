"""Offline, synthetic and explicitly provenance-labelled public-page fixtures."""
import json
from pathlib import Path
import unittest

from korean_job_search.collectors import _deadline, collect_source, ingest_url, parse_job_postings

BASE = "https://jobs.example.com/openings"
NAVER = "https://recruit.navercorp.com/rcrt/list.do"
CJ = "https://recruit.cj.net/recruit/ko/recruit/recruit/list.fo"
FIXTURES = Path(__file__).parent / "fixtures"


def ld_page(value):
    return '<!-- TEST FIXTURE --><script type="application/ld+json">' + json.dumps(value).replace("<", "\\u003c") + '</script>'


def posting(**changes):
    data = {"@type": "JobPosting", "title": "Python Engineer", "url": "/jobs/123", "hiringOrganization": {"name": "Fixture Corp"}, "description": "<p>Build public APIs.</p>", "validThrough": "2026-10-01T17:00:00+09:00"}
    return data | changes


class FakeFetcher:
    evidence_kind = "test_fixture"

    def __init__(self, pages):
        self.pages, self.requests = pages, []

    def fetch(self, url, respect_robots=True):
        assert respect_robots is True
        self.requests.append(url)
        entry = self.pages(url) if callable(self.pages) else self.pages[url]
        if isinstance(entry, dict):
            return {"url": url, "http_status": 403, "text": "", "content": b"", "content_type": "", "diagnostics": []} | entry
        assert isinstance(entry, str)
        return {"status": "ok", "url": url, "http_status": 200, "text": entry, "content": entry.encode(), "content_type": "text/html", "diagnostics": []}


class StructuredTests(unittest.TestCase):
    def test_graph_nested_itemlist_and_schema_type(self):
        page = ld_page({"@graph": [{"@type": "Organization", "name": "not a job"}, {"@type": "ItemList", "itemListElement": [{"item": posting(**{"@type": ["https://schema.org/JobPosting"]})}]}]})
        jobs = parse_job_postings(page, BASE, "fixture")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["url"], "https://jobs.example.com/jobs/123")
        self.assertEqual(jobs[0]["description"], "Build public APIs.")
        self.assertEqual(jobs[0]["company"], "Fixture Corp")
        self.assertEqual(jobs[0]["deadline"], "2026-10-01T17:00:00+09:00")

    def test_locations_employment_and_date_only(self):
        page = ld_page(posting(jobLocation=[{"address": {"addressLocality": "서울", "addressRegion": "강남구"}}], employmentType=["FULL_TIME", "CONTRACTOR"], experienceRequirements={"monthsOfExperience": 24}, validThrough="2026-10-01"))
        job = parse_job_postings(page, BASE, "fixture")[0]
        self.assertIn("서울", job["location"])
        self.assertIn("FULL_TIME", job["employment_type"])
        self.assertEqual(job["deadline"], "2026-10-01")
        self.assertIn("24", job["experience"])

    def test_malformed_ld_does_not_abort_other_blocks(self):
        html = '<script type="application/ld+json">not JSON</script>' + ld_page(posting())
        self.assertEqual(len(parse_job_postings(html, BASE, "fixture")), 1)

    def test_unsafe_record_urls_rejected(self):
        bad = ["javascript:alert(1)", "http://127.0.0.1/", "http://localhost/", "https://user:password@example.com/", "https://example.com:9999/"]
        for url in bad:
            with self.subTest(url=url):
                self.assertEqual(parse_job_postings(ld_page(posting(url=url)), BASE, "fixture"), [])

    def test_missing_title_and_multiple_missing_urls_skipped(self):
        self.assertEqual(parse_job_postings(ld_page(posting(title="")), BASE, "fixture"), [])
        self.assertEqual(parse_job_postings(ld_page([posting(url=None), posting(url=None, title="Other")]), BASE, "fixture"), [])

    def test_script_not_in_description_and_no_invented_fields(self):
        job = parse_job_postings(ld_page(posting(description='<p>Role</p><script>secret()</script>', validThrough="마감시")), BASE, "fixture")[0]
        self.assertEqual(job["description"], "Role")
        self.assertIsNone(job["deadline"])
        self.assertEqual(job["deadline_raw"], "마감시")
        self.assertEqual(job["location"], "")

    def test_generic_naive_datetime_does_not_invent_timezone(self):
        job = parse_job_postings(ld_page(posting(validThrough="2026-10-01T17:00:00")), BASE, "fixture")[0]
        self.assertIsNone(job["deadline"])
        self.assertEqual(job["deadline_raw"], "2026-10-01T17:00:00")

    def test_stable_ids_and_duplicate_rows(self):
        html = ld_page([posting(), posting()])
        jobs = parse_job_postings(html, BASE, "fixture")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], parse_job_postings(ld_page(posting()), BASE, "other")[0]["id"])

    def test_oversized_and_deep_inputs_are_bounded(self):
        self.assertEqual(parse_job_postings("a" * 4_000_001, BASE, "fixture"), [])
        self.assertEqual(parse_job_postings("<div>" * 300 + "<p>x</p>" + "</div>" * 300, BASE, "fixture"), [])

    def test_abbreviated_range_never_becomes_opening_date(self):
        for raw in ("2026-09-01 ~ 09-30", "2026.09.01 ~ 09.30", "2026-09-01 – 09-30"):
            self.assertIsNone(_deadline(raw, kst=True))
        self.assertEqual(_deadline("2026.09.01 ~ 2026.09.30 (17:00)", kst=True), "2026-09-30T17:00:00+09:00")

    def test_malformed_html_declaration_is_safe(self):
        self.assertEqual(parse_job_postings("<![bogus]>", BASE, "fixture"), [])
        r = ingest_url(BASE, FakeFetcher({BASE: "<![bogus]>"}))
        # HTMLParser patch releases either reject this declaration or treat it
        # as a bogus comment. Both must remain non-success, with no fake jobs.
        self.assertIn(r["status"], {"error", "unsupported"})
        self.assertEqual(r["jobs"], [])
        self.assertTrue(r["diagnostics"])


class AcquisitionTests(unittest.TestCase):
    def test_shell_not_empty_or_success(self):
        f = FakeFetcher({BASE: '<div id="root"></div><script src="/app.js"></script>'})
        result = collect_source({"id": "fixture", "url": BASE}, fetcher=f)
        self.assertEqual(result["status"], "needs_browser")
        self.assertEqual(result["jobs"], [])

    def test_html_login_is_credentials_not_empty(self):
        f = FakeFetcher({BASE: '<form><input type="password"><button>Login</button></form>'})
        self.assertEqual(ingest_url(BASE, f)["status"], "needs_credentials")

    def test_network_failures_preserved(self):
        for status in ("blocked", "robots_denied", "needs_credentials", "needs_browser", "unsupported", "error"):
            f = FakeFetcher({BASE: {"status": status, "diagnostics": ["test-only failure"]}})
            r = collect_source({"id": "fixture", "url": BASE}, fetcher=f)
            self.assertEqual(r["status"], status)
            self.assertEqual(r["jobs"], [])
            self.assertIn("test-only failure", r["diagnostics"])

    def test_successful_jsonld_with_test_provenance(self):
        f = FakeFetcher({BASE: ld_page(posting())})
        r = ingest_url(BASE, f)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["jobs"][0]["evidence"]["kind"], "test_fixture")
        self.assertEqual(r["jobs"][0]["evidence"]["source_url"], BASE)

    def test_link_card_is_partial_never_full_jd(self):
        f = FakeFetcher({BASE: ld_page(posting(description=""))})
        r = ingest_url(BASE, f)
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["jobs"][0]["evidence"]["completeness"], "listing_card")
        self.assertTrue(r["jobs"][0]["warnings"])

    def test_query_miss_is_not_global_empty(self):
        f = FakeFetcher({BASE: ld_page(posting())})
        r = collect_source({"id": "fixture", "url": BASE}, query="nonexistent", fetcher=f)
        self.assertEqual(r["jobs"], [])
        self.assertTrue(any("query" in d.lower() for d in r["diagnostics"]))

    def test_limit_and_unfollowed_pagination_mark_partial(self):
        html = ld_page([posting(url="/jobs/1"), posting(url="/jobs/2")]) + '<a rel="next" href="?page=2">Next</a>'
        r = collect_source({"id": "fixture", "url": BASE}, limit=1, fetcher=FakeFetcher({BASE: html}))
        self.assertEqual(len(r["jobs"]), 1)
        self.assertEqual(r["status"], "partial")

    def test_group_source_never_used_as_hiring_company(self):
        f = FakeFetcher({BASE: ld_page(posting(hiringOrganization=None))})
        r = collect_source({"id": "kr-099", "url": BASE, "name_ko": "Group Member", "is_group_portal": True}, fetcher=f)
        self.assertEqual(r["jobs"][0]["company"], "Unknown employer")
        self.assertTrue(any("employer" in x.lower() for x in r["jobs"][0]["warnings"]))

    def test_generic_company_hint_not_unverified_attribution(self):
        f = FakeFetcher({BASE: ld_page(posting(hiringOrganization=None))})
        r = collect_source({"id": "kr-099", "url": BASE, "name_ko": "Unverified Company"}, fetcher=f)
        self.assertEqual(r["jobs"][0]["company"], "Unknown employer")

    def test_bad_limit_and_missing_url(self):
        for limit in (0, -1, True, 1001, "20"):
            r = collect_source({"id": "fixture", "url": BASE}, limit=limit, fetcher=FakeFetcher({}))
            self.assertEqual(r["status"], "error")
        self.assertEqual(collect_source({"id": "fixture"})["status"], "error")

    def test_portal_host_routing_and_query_encoding(self):
        roots = {"saramin": "https://www.saramin.co.kr", "jobkorea": "https://www.jobkorea.co.kr", "wanted": "https://www.wanted.co.kr"}
        for name, root in roots.items():
            f = FakeFetcher(lambda _: {"status": "robots_denied"})
            result = collect_source({"id": "unrelated", "url": root, "kind": "portal"}, query="Python 데이터", fetcher=f)
            self.assertEqual(result["status"], "robots_denied")
            self.assertEqual(len(f.requests), 1)
            self.assertNotIn(" ", f.requests[0])
            self.assertTrue(any(name in d.lower() for d in result["diagnostics"]))

    def test_adapter_cannot_move_arbitrary_host_to_official_api(self):
        f = FakeFetcher({BASE: '<div id="root"></div><script src="app.js"></script>'})
        r = collect_source({"id": "x", "adapter": "naver", "url": BASE}, fetcher=f)
        self.assertEqual(f.requests, [BASE])
        self.assertEqual(r["status"], "needs_browser")

    def test_rejected_credentials_do_not_survive_in_result(self):
        for url in ("https://user:secret-value@example.com/", "https://example.com/?token=secret-value"):
            f = FakeFetcher({})
            result = ingest_url(url, f)
            self.assertNotIn("secret-value", json.dumps(result))
            self.assertEqual(f.requests, [])

    def test_direct_recognized_json_zero_and_error(self):
        for url, payload in (("https://recruit.navercorp.com/rcrt/loadJobList.do", {"result": "Y", "list": []}), ("https://recruit.cj.net/recruit/ko/recruit/recruit/searchNewGonggoList.fo", {"ds_newRecruitList": [], "ErrorCode": 0})):
            result = ingest_url(url, FakeFetcher({url: json.dumps(payload)}))
            self.assertEqual(result["status"], "empty")
        url = "https://recruit.cj.net/recruit/ko/recruit/recruit/searchNewGonggoList.fo"
        result = ingest_url(url, FakeFetcher({url: json.dumps({"ds_newRecruitList": [], "ErrorCode": 1})}))
        self.assertEqual(result["status"], "error")


class ObservedFixtureTests(unittest.TestCase):
    def fixture(self, name):
        return (FIXTURES / name).read_text(encoding="utf-8")

    def test_naver_live_html_excerpt_and_exact_deadline_precision(self):
        jobs = parse_job_postings(self.fixture("collect-naver-list.html"), NAVER, "kr-012")
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["company"], "Unknown employer")
        self.assertIn("annoId=30005289", jobs[0]["url"])
        self.assertEqual(jobs[0]["deadline"], "2026-09-29")
        self.assertEqual(jobs[0]["description"], "")

    def test_naver_detail_dom_wins_over_empty_jsonld(self):
        u = "https://recruit.navercorp.com/rcrt/view.do?annoId=30005289"
        r = ingest_url(u, FakeFetcher({u: self.fixture("collect-naver-detail.html")}))
        self.assertEqual(len(r["jobs"]), 1)
        job = r["jobs"][0]
        self.assertIn("Security", job["description"])
        self.assertGreater(len(job["description"]), 200)
        self.assertNotEqual(job["evidence"]["completeness"], "listing_card")
        self.assertEqual(job["deadline"], "2026-09-29T10:00:00+09:00")

    def test_cj_observed_alphanumeric_career_id(self):
        u = "https://recruit.cj.net/recruit/ko/recruit/recruit/searchNewGonggoList.fo"
        jobs = parse_job_postings(self.fixture("collect-cj-career-row.json"), u, "cj")
        self.assertEqual(len(jobs), 1)
        self.assertIn("bestDetail.fo?zz_jo_num=J20260922039598", jobs[0]["url"])
        self.assertIn("direct=N", jobs[0]["url"])

    def test_naver_pagination_counts_and_group_attribution(self):
        def response(u):
            if "firstIndex=0" in u:
                return self.fixture("collect-naver-first-page.json")
            return self.fixture("collect-naver-page.json") if "loadJobList" in u else self.fixture("collect-naver-list.html")
        f = FakeFetcher(response)
        r = collect_source({"id": "kr-012", "adapter": "naver", "careers_url": NAVER, "name_ko": "Wrong", "is_group_portal": True}, limit=4, fetcher=f)
        self.assertEqual(r["status"], "partial")
        self.assertEqual(len(r["jobs"]), 4)
        self.assertEqual(len(f.requests), 3)
        self.assertNotIn("Wrong", {j["company"] for j in r["jobs"]})
        self.assertTrue(any("45" in x for x in r["diagnostics"]))

    def test_naver_affiliate_logo_blind_text_not_employer(self):
        html = self.fixture("collect-naver-affiliate-card.html")
        jobs = parse_job_postings(html, NAVER, "naver")
        self.assertEqual(len(jobs), 1)
        self.assertIn("네이버웹툰", jobs[0]["title"])
        self.assertEqual(jobs[0]["company"], "Unknown employer")
        self.assertNotEqual(jobs[0]["company"], "NAVER")

    def test_naver_failed_next_page_retains_partial(self):
        f = FakeFetcher(lambda u: {"status": "blocked"} if "loadJobList" in u else self.fixture("collect-naver-list.html"))
        r = collect_source({"id": "x", "url": NAVER}, limit=4, fetcher=f)
        self.assertEqual(r["status"], "partial")
        self.assertEqual(len(r["jobs"]), 2)

    def test_naver_explicit_zero_count_is_empty(self):
        html = '<script>var pageSize="10"*1,page="1"*1,totalRows="0"*1;</script><div class="card_list"></div>'
        r = collect_source({"id": "x", "url": NAVER}, fetcher=FakeFetcher({NAVER: html}))
        self.assertEqual(r["status"], "empty")

    def test_cj_observed_public_endpoint_and_company_per_row(self):
        f = FakeFetcher(lambda u: self.fixture("collect-cj-page.json") if "searchNewGonggoList.fo" in u else self.fixture("collect-cj-list.html"))
        r = collect_source({"id": "kr-070", "url": CJ, "name_ko": "CJ Group", "is_group_portal": True}, limit=2, fetcher=f)
        self.assertEqual(r["status"], "partial")
        self.assertEqual(len(r["jobs"]), 2)
        self.assertEqual(r["jobs"][0]["company"], "CJ제일제당")
        self.assertEqual(r["jobs"][0]["deadline"], "2026-09-30T17:00:00+09:00")
        self.assertTrue(any("184" in d for d in r["diagnostics"]))
        self.assertIn("pageVal=1", f.requests[1])
        self.assertIn("schArea=N", f.requests[1])

    def test_cj_no_guessed_endpoint_for_unrecognized_page(self):
        f = FakeFetcher({CJ: '<p>Maintenance</p>'})
        result = collect_source({"id": "x", "url": CJ}, fetcher=f)
        self.assertEqual(len(f.requests), 1)
        self.assertNotIn(result["status"], ("empty", "ok"))

    def test_cj_detail_image_not_parsed_as_full_description(self):
        u = "https://recruit.cj.net/recruit/ko/recruit/recruit/detail.fo?zz_jo_num=8788"
        r = ingest_url(u, FakeFetcher({u: self.fixture("collect-cj-detail.html")}))
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["jobs"][0]["description"], "")
        self.assertTrue(any("image" in w.lower() for w in r["jobs"][0]["warnings"]))

    def test_saramin_listing_card_parsed_without_inventing_year(self):
        u = "https://www.saramin.co.kr/zf_user/search?searchword=python"
        r = collect_source({"id": "saramin", "url": u, "kind": "portal"}, limit=2, fetcher=FakeFetcher({u: self.fixture("collect-saramin-list.html")}))
        self.assertEqual(r["status"], "partial")
        self.assertEqual(len(r["jobs"]), 2)
        job = r["jobs"][0]
        self.assertEqual(job["company"], "(주)에즈웰플러스")
        self.assertIsNone(job["deadline"])
        self.assertIn("10/21", job["deadline_raw"])
        self.assertEqual(job["description"], "")
        self.assertNotIn("search_uuid", job["url"])


if __name__ == "__main__":
    unittest.main()
