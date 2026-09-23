"""Bounded public recruiting acquisition; no login, application or browser actions.

Only observed first-party formats are interpreted: Schema.org JobPosting,
NAVER Careers HTML/public list JSON, CJ's public search JSON/detail HTML and
Saramin's public search cards. Wanted, JobKorea, Kakao and Lotte are routed and
attempted, but access restrictions and unsupported shells remain explicit.

Listing cards have an empty description, partial status and provenance warnings.
Use ingest_url on a detail URL to obtain supported JD text; images are not OCR'd.
HTML is inert data. The parser never executes scripts or follows arbitrary links.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .network import SafeFetcher, _Failure, _validate_url

_MAX_PAGES = 3
_MAX_LIMIT = 100
_MAX_PARSE_CHARS = 4_000_000
_MAX_NODES = 80_000
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_INERT = frozenset({"script", "style", "button", "noscript", "template"})
_STATUSES = frozenset({"ok", "partial", "empty", "blocked", "robots_denied", "needs_browser", "needs_credentials", "unsupported", "error"})
_HOST_ADAPTER = {
    "recruit.navercorp.com": "naver", "recruit.cj.net": "cj",
    "careers.kakao.com": "kakao", "recruit.lotte.co.kr": "lotte",
    "www.saramin.co.kr": "saramin", "saramin.co.kr": "saramin",
    "www.jobkorea.co.kr": "jobkorea", "jobkorea.co.kr": "jobkorea",
    "www.wanted.co.kr": "wanted", "wanted.co.kr": "wanted",
}
_CJ_KEYS = ("pageVal", "pageIndex", "orderDesc", "sch_title", "arrGubun", "arrRecBu", "arrRecJob", "arrRecArea", "schArea")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _clean(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


@dataclass
class _Node:
    tag: str = ""
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def walk(self):
        stack: list[_Node] = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(c for c in reversed(node.children) if isinstance(c, _Node))

    def text(self):
        stack, parts = [self], []
        while stack:
            child = stack.pop()
            if isinstance(child, str):
                parts.append(child)
            elif child.tag not in _INERT:
                stack.extend(reversed(child.children))
        return _clean(" ".join(parts))

    def with_class(self, name):
        return [n for n in self.walk() if name in n.attrs.get("class", "").split()]

    def first(self, name):
        return next(iter(self.with_class(name)), _Node())


class _Document(HTMLParser):
    def __init__(self, text):
        if not isinstance(text, str) or len(text) > _MAX_PARSE_CHARS:
            raise ValueError("HTML size limit exceeded")
        super().__init__(convert_charrefs=True)
        self.root, self.count = _Node(), 0
        self.stack = [self.root]
        try:
            self.feed(text)
            self.close()
        except AssertionError:
            # HTMLParser uses assertions for malformed marked declarations.
            # Remote input must not escape the normal acquisition error path.
            raise ValueError("Malformed HTML declaration") from None

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > _MAX_NODES or len(self.stack) > 256:
            raise ValueError("HTML structure limit exceeded")
        node = _Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _text(value):
    if not isinstance(value, str):
        return ""
    try:
        return _Document(value).root.text()
    except (ValueError, RecursionError):
        return ""


def _flatten(value):
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return "; ".join(filter(None, (_flatten(x) for x in value)))
    if isinstance(value, dict):
        keys = ("name", "address", "addressLocality", "addressRegion", "addressCountry", "streetAddress", "monthsOfExperience")
        return ", ".join(filter(None, (_flatten(value[k]) for k in keys if k in value)))
    return ""


def _safe_link(base, link):
    if not isinstance(link, str) or not link or link.startswith("#"):
        return ""
    try:
        # Validate BEFORE urljoin, which silently strips some control characters.
        if any(c.isspace() or c == "\\" or ord(c) < 32 for c in link):
            return ""
        return _validate_url(urljoin(base, link)).url
    except (_Failure, ValueError):
        return ""


def _deadline(raw, kst=False):
    """Never invent a year or midnight. Regional adapters may attest KST."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw or re.search(r"상시|채용시|채용 시|9999|2999|until", raw, re.I):
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T[^ ]+", raw):
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return value.isoformat() if value.tzinfo else None
        except ValueError:
            return None
    # Only examine the closing side of a range. An abbreviated closing date
    # must not cause the fully qualified opening date to become the deadline.
    raw = re.split(r"\s*(?:~|～|–|—|\s-\s|\bto\b)\s*", raw, flags=re.I)[-1]
    # The final explicit calendar date is the end of a listing date range.
    matches = list(re.finditer(r"(?<!\d)(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})(?:\s*\(?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\)?)?", raw))
    if not matches:
        return None
    match = matches[-1]
    try:
        y, m, d = (int(match[i]) for i in (1, 2, 3))
        value = date(y, m, d).isoformat()
        if match[4]:
            if not kst:
                return None
            dt = datetime(y, m, d, int(match[4]), int(match[5]), int(match[6] or 0))
            return dt.isoformat() + "+09:00"
        return value
    except ValueError:
        return None


def _job(source_id, title, company, url, page_url, *, description="", kind="live_html", completeness=None, warnings=None, **fields):
    title, company = _clean(title), _clean(company)
    safe_url = _safe_link(page_url, url)
    if not title or not safe_url:
        return None
    from .models import make_job
    completeness = completeness or ("full_text" if description else "listing_card")
    notes = list(warnings or [])
    if not company:
        notes.append("Hiring employer was not identified; the source/group name was not substituted.")
    if completeness == "listing_card":
        notes.append("Listing metadata only; full job description was not fetched.")
    if completeness == "partial_text":
        notes.append("Embedded images/attachments were not read or OCR'd; JD text may be incomplete.")
    fetched_at = _now()
    # The shared model requires a non-empty employer string. Use an explicit
    # unknown marker, never the registry/group name or an inferred affiliate.
    record = make_job(source_id, title, company or "Unknown employer", safe_url, description=description,
                      evidence={"kind": kind, "source_url": page_url, "fetched_at": fetched_at, "completeness": completeness},
                      collected_at=fetched_at, warnings=notes, **fields)
    # A deliberate unknown result from a source-specific parser must not be
    # replaced by the shared model's more permissive raw-date interpretation.
    if "deadline" in fields and fields["deadline"] is None:
        record["deadline"] = None
    return record


def _dedupe(jobs):
    jobs = [j for j in jobs if j]
    if not jobs:
        return []
    from .models import deduplicate_jobs
    return deduplicate_jobs(jobs)


def _walk_json(value):
    stack, count = [(value, 0)], 0
    while stack:
        obj, depth = stack.pop()
        count += 1
        if count > _MAX_NODES or depth > 128:
            raise ValueError("JSON structure limit exceeded")
        if isinstance(obj, dict):
            yield obj
            stack.extend((x, depth + 1) for x in obj.values() if isinstance(x, (dict, list)))
        elif isinstance(obj, list):
            stack.extend((x, depth + 1) for x in reversed(obj) if isinstance(x, (dict, list)))


def _schema_type(obj, name):
    types = obj.get("@type", [])
    types = [types] if isinstance(types, str) else types
    return isinstance(types, list) and any(isinstance(x, str) and x.rstrip("/").rsplit("/", 1)[-1] == name for x in types)


def _jsonld(root, page_url, source_id, company=""):
    postings = []
    for node in root.walk():
        if node.tag != "script" or node.attrs.get("type", "").lower().split(";", 1)[0] != "application/ld+json":
            continue
        script = "".join(c for c in node.children if isinstance(c, str))
        try:
            postings.extend(obj for obj in _walk_json(json.loads(script)) if _schema_type(obj, "JobPosting"))
        except (ValueError, TypeError, RecursionError):
            continue
    jobs = []
    for row in postings:
        target = row.get("url") or row.get("mainEntityOfPage") or row.get("@id")
        if isinstance(target, dict):
            target = target.get("@id") or target.get("url")
        if len(postings) == 1 and (not target or isinstance(target, str) and target.startswith("#")):
            target = page_url
        description = _text(row.get("description"))
        raw_deadline = row.get("validThrough", "")
        raw_deadline = raw_deadline if isinstance(raw_deadline, str) else ""
        warnings = []
        if raw_deadline and not _deadline(raw_deadline):
            warnings.append("Deadline could not be normalized without guessing; preserve deadline_raw.")
        raw_description = row.get("description", "")
        images = isinstance(raw_description, str) and bool(re.search(r"<(?:img|iframe|object)\b", raw_description, re.I))
        job = _job(source_id, row.get("title", ""), _flatten(row.get("hiringOrganization")) or company,
                   target, page_url, description=description, warnings=warnings,
                   completeness="partial_text" if images else None,
                   location=_flatten(row.get("jobLocation")) or _flatten(row.get("applicantLocationRequirements")),
                   employment_type=_flatten(row.get("employmentType")), experience=_flatten(row.get("experienceRequirements")),
                   deadline=_deadline(raw_deadline), deadline_raw=raw_deadline)
        if job:
            jobs.append(job)
    return jobs


def _definition_fields(node):
    result, label = {}, ""
    for child in node.walk():
        if child.tag == "dt":
            label = child.text()
        elif child.tag == "dd" and label:
            result[label] = child.text()
    return result


def _naver_dom(root, page_url, source_id, structured_jobs=()):
    jobs = []
    cards = root.with_class("card_item")
    detail = urlsplit(page_url).path == "/rcrt/view.do"
    if detail:
        cards = root.with_class("card_title_box")[:1]
    for card in cards:
        title = card.first("card_title").text()
        fields = _definition_fields(card.first("card_info"))
        if detail:
            query = dict(parse_qsl(urlsplit(page_url).query))
            ident = query.get("annoId", "")
            company = next((job["company"] for job in structured_jobs
                            if job["title"] == title and job["company"] != "Unknown employer"), "")
        else:
            click = card.first("card_link").attrs.get("onclick", "")
            found = re.fullmatch(r"\s*show\(['\"]?(\d+)['\"]?\)\s*;?\s*", click)
            ident = found[1] if found else ""
            # The live template hardcodes blind text 'NAVER' even for affiliate
            # logos. That accessibility label is not hiring-employer evidence.
            # collect_source obtains sysCompanyCdNm from the public list API.
            company = ""
        if not re.fullmatch(r"\d+", ident):
            continue
        raw = fields.get("모집 기간", "")
        description_node = root.first("detail_wrap") if detail else _Node()
        description = description_node.text()
        has_media = any(n.tag in {"img", "iframe", "object"} for n in description_node.walk())
        jobs.append(_job(source_id, title, company, "/rcrt/view.do?" + urlencode({"annoId": ident}), page_url,
                         description=description, completeness="partial_text" if has_media else None,
                         employment_type=fields.get("근로 조건", ""), experience=fields.get("모집 경력", ""),
                         deadline=_deadline(raw, kst=True), deadline_raw=raw))
    return jobs


def _naver_json(value, page_url, source_id):
    if not isinstance(value, dict) or value.get("result") != "Y" or not isinstance(value.get("list"), list):
        raise ValueError("NAVER list schema was not recognized")
    jobs = []
    for row in value["list"]:
        if not isinstance(row, dict) or not re.fullmatch(r"\d+", str(row.get("annoId", ""))):
            continue
        ident = str(row["annoId"])
        raw = _clean(row.get("endYmdTime"))
        jobs.append(_job(source_id, row.get("annoSubject", ""), row.get("sysCompanyCdNm", ""),
                         "/rcrt/view.do?" + urlencode({"annoId": ident}), page_url, kind="live_json",
                         employment_type=_clean(row.get("empTypeCdNm")), experience=_clean(row.get("entTypeCdNm")),
                         deadline=_deadline(raw, kst=True), deadline_raw=raw))
    return jobs


def _cj_json(value, page_url, source_id):
    if not isinstance(value, dict) or not isinstance(value.get("ds_newRecruitList"), list) or value.get("ErrorCode", 0) not in {0, "0"}:
        raise ValueError("CJ list schema was not recognized")
    jobs = []
    for row in value["ds_newRecruitList"]:
        if not isinstance(row, dict) or not re.fullmatch(r"(?:\d+|J\d+)", str(row.get("zz_jo_num", ""))):
            continue
        gubun = str(row.get("gubun", ""))
        if gubun not in {"1", "2"}:
            continue  # No guessed detail route for undocumented record types.
        route = "detail.fo" if gubun == "1" else "bestDetail.fo"
        params = {"zz_jo_num": str(row["zz_jo_num"])}
        if gubun == "2":
            params["direct"] = "N"
        target = "/recruit/ko/recruit/recruit/" + route + "?" + urlencode(params)
        raw = _clean(row.get("zz_end_dt_str"))
        hour, minute = str(row.get("zz_end_hh", "")), str(row.get("zz_end_mi", ""))
        if row.get("zz_till_hire") == "Y":
            raw = "채용시까지"
        elif re.fullmatch(r"\d{1,2}", hour) and re.fullmatch(r"\d{2}", minute):
            raw += " " + hour + ":" + minute
        jobs.append(_job(source_id, row.get("zz_title", ""), row.get("compnm", ""), target, page_url,
                         kind="live_json", location=_clean(row.get("location_cd_nm")),
                         deadline=_deadline(raw, kst=True), deadline_raw=raw,
                         warnings=["Listing deadline metadata only; compare with the written JD before applying."]))
    return jobs


def _cj_dom(root, page_url, source_id):
    if urlsplit(page_url).path.rsplit("/", 1)[-1] not in {"detail.fo", "bestDetail.fo"}:
        return []
    header = root.first("support-list")
    title = header.first("title").text()
    if not title:
        return []
    content = root.first("detail-list")
    description = content.text()
    has_media = any(n.tag in {"img", "iframe", "object"} or (n.tag == "a" and ".pdf" in n.attrs.get("href", "").lower()) for n in content.walk())
    metadata = header.first("meta-info").text()
    experience = next((n.text() for n in header.first("meta-info").walk() if n.tag == "li" and n.text() in {"신입", "경력", "인턴"}), "")
    return [_job(source_id, title, header.first("company").text(), page_url, page_url,
                 description=description, completeness="partial_text" if has_media else None,
                 deadline=_deadline(metadata, kst=True), deadline_raw=metadata, experience=experience)]


def _saramin_dom(root, page_url, source_id):
    jobs = []
    for card in root.with_class("item_recruit"):
        title_node = card.first("job_tit")
        anchor = next((n for n in title_node.walk() if n.tag == "a" and n.attrs.get("href")), None)
        if not anchor:
            continue
        original = _safe_link(page_url, anchor.attrs["href"])
        if not original or urlsplit(original).hostname not in {"www.saramin.co.kr", "saramin.co.kr"}:
            continue
        ident = dict(parse_qsl(urlsplit(original).query)).get("rec_idx", "")
        if not re.fullmatch(r"\d+", ident):
            continue
        # rec_idx is the observed stable detail identifier; discard tracking IDs.
        target = urlunsplit(("https", "www.saramin.co.kr", "/zf_user/jobs/relay/view", urlencode({"rec_idx": ident}), ""))
        raw = card.first("job_date").first("date").text()
        conditions = [n.text() for n in card.first("job_condition").children if isinstance(n, _Node) and n.tag == "span"]
        jobs.append(_job(source_id, anchor.attrs.get("title") or title_node.text(), card.first("corp_name").text(), target, page_url,
                         location=conditions[0] if conditions else "",
                         experience=next((x for x in conditions[1:] if "경력" in x or "신입" in x), ""),
                         employment_type=next((x for x in conditions[1:] if any(t in x for t in ("정규직", "계약직", "인턴", "프리랜서"))), ""),
                         deadline=_deadline(raw, kst=True), deadline_raw=raw))
    return jobs


def parse_job_postings(html, url, source_id, company=""):
    """Parse supplied content without fetching. Caller is responsible for provenance.

    ``company`` is an explicit, trusted caller hint for generic structured data,
    not an automatic registry-group attribution. collect_source never passes it.
    Invalid/oversized input returns no records, never an invented empty-site claim.
    """
    if not isinstance(html, str) or len(html) > _MAX_PARSE_CHARS:
        return []
    try:
        adapter = _HOST_ADAPTER.get(urlsplit(url).hostname, "generic")
        if html.lstrip().startswith(("{", "[")) and adapter in {"naver", "cj"}:
            value = json.loads(html)
            return _dedupe((_naver_json if adapter == "naver" else _cj_json)(value, url, source_id))
        root = _Document(html).root
        jobs = _jsonld(root, url, source_id, company)
        # NAVER's observed detail JSON-LD has an empty description despite a
        # complete text JD in .detail_wrap. Prefer the verified detail surface
        # and supplement only missing metadata, not arbitrary page text.
        detail_jobs = []
        if adapter == "naver" and urlsplit(url).path == "/rcrt/view.do":
            detail_jobs = _naver_dom(root, url, source_id, jobs)
        elif adapter == "cj":
            detail_jobs = _cj_dom(root, url, source_id)
        detail_jobs = [job for job in detail_jobs if job]
        if detail_jobs:
            for detail in detail_jobs:
                metadata = next((j for j in jobs if j and j["title"] == detail["title"]), {})
                for key in ("location", "employment_type", "experience"):
                    if not detail.get(key) and metadata.get(key):
                        detail[key] = metadata[key]
            jobs = detail_jobs
        elif not jobs:
            if adapter == "naver":
                jobs = _naver_dom(root, url, source_id)
            elif adapter == "saramin":
                jobs = _saramin_dom(root, url, source_id)
        return _dedupe(jobs)
    except (ValueError, TypeError, RecursionError):
        return []


def _request(fetcher, url, diagnostics):
    try:
        response = fetcher.fetch(url, respect_robots=True)
    except Exception:
        # Third-party injected fetchers must not leak credentials in exceptions.
        diagnostics.append("Fetcher failed; sensitive exception details omitted.")
        return {"status": "error", "url": url, "text": "", "diagnostics": ["Fetcher failed; sensitive exception details omitted."]}
    if not isinstance(response, dict) or response.get("status") not in _STATUSES:
        diagnostics.append("Fetcher returned an invalid result.")
        return {"status": "error", "url": url, "text": "", "diagnostics": ["Fetcher returned an invalid result."]}
    safe_url = _safe_link(url, response.get("url") or url)
    diagnostics.append("GET " + (safe_url or "[invalid public URL]") + " -> " + response["status"] + " (HTTP " + str(response.get("http_status")) + ").")
    notes = response.get("diagnostics", [])
    if isinstance(notes, list):
        diagnostics.extend(n for n in notes if isinstance(n, str))
    return response


def _portal_url(adapter, url, query):
    parts = urlsplit(url)
    # Do not turn an explicit detail/filtered URL into an unrelated broad search.
    if parts.path not in {"", "/"}:
        return url
    if adapter == "saramin":
        return urljoin(url, "/zf_user/search") + "?" + urlencode({"searchword": query})
    if adapter == "jobkorea":
        return urljoin(url, "/Search/") + "?" + urlencode({"stext": query})
    if adapter == "wanted":
        return urljoin(url, "/search") + "?" + urlencode({"query": query, "tab": "position"}) if query else urljoin(url, "/wdlist")
    if adapter == "naver":
        return urljoin(url, "/rcrt/list.do")
    if adapter == "cj":
        return urljoin(url, "/recruit/ko/recruit/recruit/list.fo")
    return url


def _fallback_status(root, content_type, adapter):
    if any(n.tag == "input" and n.attrs.get("type", "").lower() == "password" for n in root.walk()):
        return "needs_credentials", "A login form was returned; no credentials were requested or submitted."
    visible = root.text().casefold()
    if any(x in visible for x in ("verify you are human", "access denied", "접근이 제한", "보안문자")):
        return "blocked", "An access/challenge page was returned; no bypass was attempted."
    if content_type and not any(t in content_type for t in ("html", "json", "text/plain")):
        return "unsupported", "Response is not a supported HTML/JSON document; binary documents require manual extraction."
    if any(n.tag in {"script", "iframe"} for n in root.walk()) or adapter in {"wanted", "jobkorea", "kakao", "lotte"}:
        return "needs_browser", "No supported public job records were found. HTTP 200 may be an application shell, not an empty job list."
    return "unsupported", "No supported JobPosting or known listing structure was found; this is not proof of zero jobs."


def _naver_total(text):
    match = re.search(r'\btotalRows\s*=\s*["\']?(\d+)', text)
    return int(match[1]) if match else None


def _more_link(root):
    return any(n.tag == "a" and ("next" in n.attrs.get("rel", "").split() or n.attrs.get("aria-label", "").lower() in {"next", "다음 페이지"}) for n in root.walk())


def _filter_jobs(jobs, query):
    words = query.casefold().split()
    return [j for j in jobs if all(word in " ".join(str(j.get(k, "")) for k in ("title", "company", "description", "location", "experience", "employment_type")).casefold() for word in words)]


def _acquire(source, query, limit, fetcher, ingest=False):
    source = source if isinstance(source, dict) else {}
    source_url = source.get("careers_url") or source.get("url") or ""
    source_id = str(source.get("id") or "url")
    result = {"source_id": source_id, "source_url": "", "status": "error", "jobs": [], "diagnostics": [], "fetched_at": _now()}
    diagnostics = result["diagnostics"]
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_LIMIT:
        diagnostics.append("limit must be an integer from 1 to " + str(_MAX_LIMIT) + ".")
        return result
    if not isinstance(source_url, str) or not source_url or not isinstance(query, str) or len(query) > 500:
        diagnostics.append("A public source URL and a query of at most 500 characters are required.")
        return result
    try:
        checked = _validate_url(source_url)
    except _Failure as exc:
        result["status"] = exc.status
        diagnostics.append(exc.message)
        return result
    result["source_url"] = checked.url
    adapter = _HOST_ADAPTER.get(checked.host, "generic")
    requested_adapter = source.get("adapter")
    if requested_adapter and requested_adapter not in {adapter, "generic", "auto"}:
        diagnostics.append("Adapter hint does not match a verified hostname; no unrelated first-party API will be guessed.")
    if adapter in {"wanted", "saramin", "jobkorea"}:
        diagnostics.append(adapter + ": public HTML/JSON-LD attempt. No authenticated/partner API credentials are configured or sent; availability is not assumed.")
    url = checked.url if ingest else _portal_url(adapter, checked.url, query)
    fetcher = fetcher if fetcher is not None else SafeFetcher()
    response = _request(fetcher, url, diagnostics)
    if response.get("status") != "ok":
        result["status"] = response.get("status", "error")
        return result
    final_url, html = response.get("url") or url, response.get("text", "")
    if not isinstance(html, str):
        diagnostics.append("Fetcher did not return text.")
        return result
    try:
        root = _Document(html).root
    except (ValueError, RecursionError):
        diagnostics.append("Document exceeded parser limits or was malformed.")
        return result
    # Routing after a redirect is determined by its verified final host, not the
    # initial source label. No name_ko fallback can misattribute group postings.
    adapter = _HOST_ADAPTER.get(urlsplit(final_url).hostname or "", "generic")
    jobs = parse_job_postings(html, final_url, source_id)
    total, complete_empty, interrupted = None, False, False
    if adapter in {"naver", "cj"} and html.lstrip().startswith("{"):
        try:
            value = json.loads(html)
            if adapter == "naver":
                _naver_json(value, final_url, source_id)
                complete_empty = value["list"] == []
            else:
                _cj_json(value, final_url, source_id)
                complete_empty = value["ds_newRecruitList"] == []
        except (ValueError, TypeError, RecursionError):
            diagnostics.append("Public listing JSON returned an unrecognized or error schema.")
            interrupted = True
    more = _more_link(root)
    if more:
        diagnostics.append("A next-page link is present; generic pagination is not followed.")
    if not ingest and adapter == "naver" and urlsplit(final_url).path == "/rcrt/list.do":
        total = _naver_total(html)
        complete_empty = total == 0 and not jobs
        page_match = re.search(r'\bpageSize\s*=\s*["\']?(\d+)', html)
        size = int(page_match[1]) if page_match else 10
        size = size if 1 <= size <= 100 else 10
        params = dict(parse_qsl(urlsplit(final_url).query))
        for page in range(_MAX_PAGES):
            if complete_empty:
                break
            if "/rcrt/loadJobList.do" not in html:
                diagnostics.append("NAVER pagination endpoint was not present in the fetched page; no endpoint was guessed.")
                break
            params["firstIndex"] = str(page * size)
            next_url = urljoin(final_url, "/rcrt/loadJobList.do") + "?" + urlencode(params)
            next_response = _request(fetcher, next_url, diagnostics)
            if next_response.get("status") != "ok":
                interrupted = True
                break
            try:
                value = json.loads(next_response.get("text", ""))
                next_jobs = _naver_json(value, next_response.get("url") or next_url, source_id)
            except (ValueError, TypeError, RecursionError):
                diagnostics.append("NAVER pagination returned an unrecognized schema, not a verified empty page.")
                interrupted = True
                break
            if page == 0 and next_jobs:
                # Replace provisional HTML cards; the API supplies verified
                # per-posting employers and precise deadlines for this page.
                jobs = _dedupe(next_jobs)
                if (total is not None and len(jobs) >= total) or len(_filter_jobs(jobs, query)) >= limit:
                    break
                continue
            before = len(jobs)
            jobs = _dedupe(jobs + next_jobs)
            if len(jobs) == before:
                diagnostics.append("NAVER pagination made no progress; stopped without claiming completeness.")
                interrupted = True
                break
            if (total is not None and len(jobs) >= total) or len(_filter_jobs(jobs, query)) >= limit:
                break
        more = more or total is None or len(jobs) < total
    elif not ingest and adapter == "cj" and urlsplit(final_url).path.endswith("/list.fo"):
        endpoint = "/recruit/ko/recruit/recruit/searchNewGonggoList.fo"
        inputs = {n.attrs.get("name"): n.attrs.get("value", "") for n in root.walk() if n.tag == "input" and n.attrs.get("name") in _CJ_KEYS}
        if endpoint in html and all(key in inputs for key in _CJ_KEYS):
            params = {key: inputs[key] for key in _CJ_KEYS}
            params["pageIndex"] = "10"
            for page in range(1, _MAX_PAGES + 1):
                params["pageVal"] = str(page)
                next_url = urljoin(final_url, endpoint) + "?" + urlencode(params)
                next_response = _request(fetcher, next_url, diagnostics)
                if next_response.get("status") != "ok":
                    if not jobs:
                        result["status"] = next_response.get("status", "error")
                        return result
                    interrupted = True
                    break
                try:
                    value = json.loads(next_response.get("text", ""))
                    next_jobs = _cj_json(value, next_response.get("url") or next_url, source_id)
                    rows = value["ds_newRecruitList"]
                    parsed_count = sum(1 for job in next_jobs if job)
                    if parsed_count < len(rows):
                        diagnostics.append("CJ page contained " + str(len(rows) - parsed_count) + " unsupported/invalid rows; those were not invented.")
                        interrupted = True
                    counts = {int(r["tot_cnt"]) for r in rows if isinstance(r, dict) and str(r.get("tot_cnt", "")).isdigit()}
                    if len(counts) == 1:
                        total = counts.pop()
                    elif len(counts) > 1:
                        diagnostics.append("CJ returned inconsistent totals; completeness is unverified.")
                        interrupted = True
                    # The documented API's empty array is an explicit result,
                    # unlike the initial HTML shell's placeholder zero count.
                    if not rows and page == 1:
                        complete_empty = True
                except (ValueError, TypeError, RecursionError):
                    diagnostics.append("CJ pagination returned an unrecognized schema, not a verified empty page.")
                    interrupted = True
                    break
                before = len(jobs)
                jobs = _dedupe(jobs + next_jobs)
                if not rows or (total is not None and len(jobs) >= total) or len(_filter_jobs(jobs, query)) >= limit:
                    break
                if len(jobs) == before:
                    diagnostics.append("CJ pagination made no progress; stopped without claiming completeness.")
                    interrupted = True
                    break
            more = more or (total is None and not complete_empty) or (total is not None and len(jobs) < total)
        else:
            diagnostics.append("CJ public search endpoint/form was not observed; no undocumented endpoint was called.")
    jobs = _dedupe(jobs)
    scanned = len(jobs)
    if total is not None:
        diagnostics.append("Source declared " + str(total) + " records; parsed " + str(scanned) + " unique records in this bounded snapshot.")
        if scanned > total:
            diagnostics.append("Parsed count exceeds declared count; source changed or its total is inconsistent.")
            interrupted = True
    if more or interrupted:
        diagnostics.append("Coverage is partial: at most " + str(_MAX_PAGES) + " listing pages are read; more pages or failed requests may remain.")
    selected = _filter_jobs(jobs, query)
    if query:
        diagnostics.append("Query is a local substring filter over fetched fields, not an exhaustive site-wide search.")
    truncated = len(selected) > limit
    selected = selected[:limit]
    if truncated:
        diagnostics.append("Returned records were truncated to the requested limit.")
    if getattr(fetcher, "evidence_kind", "") == "test_fixture":
        for job in selected:
            job["evidence"]["kind"] = "test_fixture"
    result["jobs"] = selected
    incomplete_jd = any(j.get("evidence", {}).get("completeness") != "full_text" for j in selected)
    if selected:
        result["status"] = "partial" if more or interrupted or truncated or incomplete_jd else "ok"
        if incomplete_jd:
            diagnostics.append("One or more records are listing cards or incomplete media-based JDs; descriptions/qualifications were not invented.")
    elif complete_empty and not interrupted:
        result["status"] = "empty"
        diagnostics.append("The recognized source explicitly returned zero listings for these source filters.")
    elif query and scanned:
        result["status"] = "partial" if more or interrupted else "empty"
        diagnostics.append("No query match in the fetched snapshot; this is not a claim of no jobs on the site.")
    elif interrupted:
        result["status"] = "error"
    else:
        result["status"], message = _fallback_status(root, response.get("content_type", ""), adapter)
        diagnostics.append(message)
    return result


def collect_source(source: dict, query="", limit=20, fetcher=None):
    """Fetch a bounded public source snapshot; no apply/login/save endpoints."""
    return _acquire(source, query, limit, fetcher)


def ingest_url(url, fetcher=None):
    """Fetch exactly the supplied public page, apart from safe HTTP redirects."""
    try:
        source_id = urlsplit(url).hostname or "url"
    except (ValueError, TypeError):
        source_id = "url"
    return _acquire({"id": source_id, "url": url}, "", _MAX_LIMIT, fetcher, ingest=True)
