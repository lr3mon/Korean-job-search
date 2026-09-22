"""외부 의존성 없이 채용공고의 식별자·근거·마감 상태를 다룬다.

URL이 같은 공고만 동일한 공고로 취급한다. 마감일에 연도나 날짜가
빠졌다면 추정하지 않으며, 마감 정보가 없다는 이유로 모집 중이라 판단하지 않는다.
"""

from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import ipaddress
import re
from typing import Any, NamedTuple
import unicodedata
from urllib.parse import unquote_plus, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


__all__ = ["now_iso", "canonical_url", "make_job", "deduplicate_jobs", "deadline_state"]

_TRACKERS = frozenset({
    "gclid", "fbclid", "dclid", "msclkid", "ttclid", "twclid", "igshid", "yclid",
    "_ga", "_gl", "mc_cid", "mc_eid", "gbraid", "wbraid", "gad_source",
    "gad_campaignid", "srsltid",
})
_ROLLING = frozenset({"채용시마감", "상시", "상시채용", "상시모집"})
_OFFSET = r"(?:Z|[+-][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?)"
_ISO = re.compile(
    r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"(?:[T ](?P<time>[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?"
    + _OFFSET + r"?))?"
)
_KOREAN = re.compile(
    r"(?P<year>[0-9]{4})년\s*(?P<month>[0-9]{1,2})월\s*"
    r"(?P<day>[0-9]{1,2})일(?:\s+(?P<time>.+))?"
)
_DOTTED = re.compile(
    r"(?P<year>[0-9]{4})\.(?P<month>[0-9]{1,2})\."
    r"(?P<day>[0-9]{1,2})(?:\s+(?P<time>.+))?"
)
_CLOCK = re.compile(
    r"(?P<hour>[0-9]{1,2}):(?P<minute>[0-9]{2})"
    r"(?::(?P<second>[0-9]{2})(?:\.(?P<fraction>[0-9]+))?)?"
    r"(?P<offset>" + _OFFSET + r")?"
)
_KOREAN_CLOCK = re.compile(
    r"(?P<hour>[0-9]{1,2})시\s*(?P<minute>[0-9]{1,2})분"
    r"(?:\s*(?P<second>[0-9]{1,2})(?:\.(?P<fraction>[0-9]+))?초)?"
)
_MISSING = object()
_EPOCH = datetime(1970, 1, 1)


def _seoul_timezone():
    """시간대 데이터가 없는 운영체제에서도 한국 표준시를 제공한다."""
    try:
        return ZoneInfo("Asia/Seoul")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=9), "Asia/Seoul")


SEOUL = _seoul_timezone()


class _ParsedDate(NamedTuple):
    text: str
    day: date
    moment: datetime | None
    fraction: str = ""


def now_iso() -> str:
    """현재 시각을 UTC 시간대가 명시된 ISO 8601 문자열로 반환한다."""
    return datetime.now(timezone.utc).isoformat()


def canonical_url(url: str) -> str:
    """검증된 절대 HTTP(S) URL에서 조각 식별자와 알려진 추적 인자를 제거한다.

    스킴·호스트는 소문자로, 기본 포트는 생략하고 빈 경로는 '/'로 정규화한다.
    경로의 대소문자·끝 슬래시·이스케이프와 남은 쿼리의 순서·중복·값은 유지한다.
    'ref', 'source'처럼 공고를 식별할 수 있는 인자는 임의로 삭제하지 않는다.
    공백·제어문자·사용자 인증정보·잘못된 호스트/포트/이스케이프는 ValueError다.
    상대 URL에 호스트를 덧붙이거나 원본에 없는 경로를 추정하지 않는다.
    """
    if not isinstance(url, str) or not url:
        raise ValueError("URL은 비어 있지 않은 문자열이어야 합니다.")
    if any(char.isspace() or unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in url):
        raise ValueError("URL에 공백이나 제어문자를 사용할 수 없습니다.")
    if "\\" in url:
        raise ValueError("URL에 역슬래시를 사용할 수 없습니다.")
    if re.search(r"%(?![0-9a-fA-F]{2})", url) or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", url, re.IGNORECASE):
        raise ValueError("URL의 퍼센트 이스케이프가 잘못되었거나 제어문자를 포함합니다.")
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("절대 HTTP 또는 HTTPS URL이 필요합니다.")
        if "@" in parts.netloc:
            raise ValueError("URL에 사용자 인증정보를 포함할 수 없습니다.")
        host = parts.hostname
        port = parts.port
        if not host or parts.netloc.endswith(":"):
            raise ValueError("URL의 호스트 또는 포트가 잘못되었습니다.")
        if ":" in host:
            if re.fullmatch(r"\[[^\[\]]+\](?::[0-9]+)?", parts.netloc) is None:
                raise ValueError("대괄호로 둘러싼 호스트 뒤에 잘못된 문자가 있습니다.")
            if "%" in host:
                raise ValueError("범위 식별자가 포함된 호스트는 지원하지 않습니다.")
            host = "[" + ipaddress.IPv6Address(host).compressed + "]"
        else:
            if "[" in parts.netloc or "]" in parts.netloc:
                raise ValueError("URL의 호스트 대괄호가 잘못되었습니다.")
            ascii_host = host.encode("idna").decode("ascii")
            labels = ascii_host[:-1].split(".") if ascii_host.endswith(".") else ascii_host.split(".")
            if len(ascii_host.rstrip(".")) > 253 or any(
                not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                for label in labels
            ):
                raise ValueError("URL의 호스트 이름이 잘못되었습니다.")
        authority = host.lower()
        if port is not None and (parts.scheme, port) not in {("http", 80), ("https", 443)}:
            authority += ":" + str(port)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("올바른 HTTP(S) URL이 필요합니다. 호스트·포트·인증정보를 확인하세요.") from exc

    kept = []
    for component in parts.query.split("&"):
        key = unquote_plus(component.partition("=")[0]).casefold()
        if not key.startswith("utm_") and key not in _TRACKERS:
            kept.append(component)
    return urlunsplit((parts.scheme, authority, parts.path or "/", "&".join(kept), ""))


def _job_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _text(value, name: str, *, required: bool = False) -> str:
    if not isinstance(value, str) or (required and not value.strip()):
        qualifier = "비어 있지 않은 " if required else ""
        raise ValueError(f"{name} 항목은 {qualifier}문자열이어야 합니다.")
    return value


def _warnings(value) -> list:
    if not isinstance(value, (list, tuple)):
        raise ValueError("경고 항목은 문자열 목록이어야 합니다.")
    return [_text(item, "경고", required=True) for item in value]


def _offset_timezone(value: str):
    if value == "Z":
        return timezone.utc
    fields = value[1:].split(":")
    hour, minute = int(fields[0]), int(fields[1])
    seconds, _, fraction = fields[2].partition(".") if len(fields) == 3 else ("0", "", "")
    second = int(seconds)
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError("시간대의 시·분·초 범위가 잘못되었습니다.")
    offset = timedelta(hours=hour, minutes=minute, seconds=second, microseconds=int(fraction.ljust(6, "0") or "0"))
    return timezone(offset if value[0] == "+" else -offset)


def _parsed_match(match, *, assume_seoul: bool) -> _ParsedDate | None:
    try:
        day = date(int(match["year"]), int(match["month"]), int(match["day"]))
        time_text = match["time"]
        if time_text is None:
            return _ParsedDate(day.isoformat(), day, None)
        clock = _CLOCK.fullmatch(time_text) or _KOREAN_CLOCK.fullmatch(time_text)
        if clock is None:
            return None
        parts = clock.groupdict()
        hour, minute = int(parts["hour"]), int(parts["minute"])
        second = int(parts["second"] or "0")
        fraction = parts["fraction"] or ""
        offset = parts.get("offset")
        tz = _offset_timezone(offset) if offset else (SEOUL if assume_seoul else None)
        moment = datetime(day.year, day.month, day.day, hour, minute, second, int((fraction + "000000")[:6]), tzinfo=tz)
        normalized = day.isoformat() + f"T{hour:02d}:{minute:02d}"
        if parts["second"] is not None:
            normalized += f":{second:02d}"
        if fraction:
            normalized += "." + fraction
        if offset:
            normalized += "+00:00" if offset == "Z" else offset
        elif tz is not None:
            normalized += moment.isoformat(timespec="seconds").split("T", 1)[1][8:]
        return _ParsedDate(normalized, day, moment, fraction)
    except (ValueError, OverflowError):
        return None


def _parse_deadline(value) -> _ParsedDate | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    for pattern in (_ISO, _KOREAN, _DOTTED):
        match = pattern.fullmatch(text)
        if match is not None:
            return _parsed_match(match, assume_seoul=True)
    return None


def _aware_timestamp(value, name: str) -> _ParsedDate:
    parsed = None
    if isinstance(value, datetime):
        if value.utcoffset() is not None:
            fraction = f"{value.microsecond:06d}" if value.microsecond else ""
            parsed = _ParsedDate(value.isoformat(), value.date(), value, fraction)
    elif isinstance(value, str):
        match = _ISO.fullmatch(value)
        if match is not None:
            parsed = _parsed_match(match, assume_seoul=False)
    if parsed is None or parsed.moment is None or parsed.moment.utcoffset() is None:
        raise ValueError(f"{name} 항목은 시간대가 명시된 ISO 날짜·시각 또는 datetime이어야 합니다.")
    return parsed


def _rolling(value) -> bool:
    return isinstance(value, str) and re.sub(r"\s+", "", value) in _ROLLING


def _instant(value: _ParsedDate) -> tuple[int, str]:
    """초와 소수 문자열을 비교하여 긴 정밀도에도 반올림·정수 길이 제한을 피한다."""
    moment = value.moment
    if moment is None:
        raise ValueError("시각 비교에는 날짜와 시각이 모두 필요합니다.")
    offset = moment.utcoffset()
    if offset is None:
        raise ValueError("시각 비교에는 시간대가 필요합니다.")
    delta = moment.replace(tzinfo=None) - _EPOCH - offset
    fraction = (f"{delta.microseconds:06d}" + value.fraction[6:]).rstrip("0")
    return delta.days * 86400 + delta.seconds, fraction


def make_job(source_id: str, title: str, company: str, url: str, description: str = "", **fields) -> dict:
    """검증된 공고 사전을 만들고 입력·중첩 메타데이터는 수정하지 않는다.

    id는 정규 URL의 UTF-8 SHA-256 전체 64자리 값이며 직접 지정할 수 없다.
    필수 문자열은 source_id·title·company다. 선택 문자열 location·employment_type·
    experience는 빈 문자열을 기본값으로 사용하며, 알 수 없는 확장 필드는 깊은 복사한다.
    collected_at과 evidence.fetched_at에는 시간대가 반드시 있어야 한다.
    근거 기본값은 kind='source', source_url=원본 URL, fetched_at=수집 시각이다.

    deadline은 문자열 또는 None이고 deadline_raw는 원문 문자열이다. 완전한 ISO 날짜,
    연도를 포함한 한국어/점 구분 날짜와 선택적 시각만 해석한다. 시간대 없는 시각은
    서울 시간이며, 날짜만 있으면 날짜만 저장한다. 상대 표현·불완전한 날짜·잘못된
    날짜는 deadline=None과 한국어 경고로 남긴다. 명시적인 상시 표현에는 경고가 없다.
    날짜의 시각·소수초 정밀도와 원문은 보존하며, 잘못된 필드 형식은 ValueError다.
    """
    if "id" in fields:
        raise ValueError("공고 식별자는 URL로 계산되므로 직접 지정할 수 없습니다.")
    normalized_url = canonical_url(url)
    values = deepcopy(fields)
    record: dict[str, Any] = {
        "id": _job_id(normalized_url),
        "source_id": _text(source_id, "출처 식별자", required=True),
        "title": _text(title, "공고 제목", required=True),
        "company": _text(company, "회사명", required=True),
        "url": normalized_url,
        "description": _text(description, "공고 설명"),
    }
    for name, label in (("location", "근무지"), ("employment_type", "고용 형태"), ("experience", "경력")):
        record[name] = _text(values.pop(name, ""), label)
    collected_value = values.pop("collected_at", _MISSING)
    collected = _aware_timestamp(now_iso() if collected_value is _MISSING else collected_value, "수집 시각").text
    record["collected_at"] = collected
    evidence = values.pop("evidence", {})
    if not isinstance(evidence, Mapping):
        raise ValueError("근거 항목은 사전이어야 합니다.")
    evidence = dict(evidence)
    evidence["kind"] = _text(evidence.get("kind", "source"), "근거 종류", required=True)
    source_url = evidence.get("source_url", url)
    canonical_url(source_url)
    evidence["source_url"] = source_url
    evidence["fetched_at"] = _aware_timestamp(evidence.get("fetched_at", collected), "근거 수집 시각").text
    record["evidence"] = evidence
    record["warnings"] = _warnings(values.pop("warnings", []))

    deadline = values.pop("deadline", None)
    if deadline is not None and not isinstance(deadline, str):
        raise ValueError("마감일은 문자열 또는 None이어야 합니다.")
    raw = values.pop("deadline_raw", _MISSING)
    raw = (deadline if deadline is not None else "") if raw is _MISSING else _text(raw, "마감일 원문")
    candidate = deadline if deadline is not None and deadline.strip() else raw
    parsed = _parse_deadline(candidate)
    record["deadline"] = parsed.text if parsed is not None else None
    record["deadline_raw"] = raw
    if candidate.strip() and parsed is None and not _rolling(candidate):
        warning = f"마감일을 해석하지 못했습니다: {candidate}"
        if warning not in record["warnings"]:
            record["warnings"].append(warning)
    record.update(values)
    return record


def deadline_state(job: Mapping, now=None) -> str:
    """공고 마감을 'open', 'expired', 'unknown', 'rolling' 중 하나로 반환한다.

    날짜 마감은 한국 달력의 해당 날짜 전체를 포함한다. 시각 마감은 해당 순간까지
    open이며 그보다 늦으면 expired다. 누락·해석 불가 마감은 unknown이고, 정확한
    '채용시 마감'·'상시'·'상시 채용'·'상시 모집' 표현만 rolling으로 인정한다.
    now는 시간대가 있는 datetime 또는 ISO 문자열이어야 하며, 생략하면 현재 UTC다.
    공고의 정규화된 deadline이 없을 때 원문에서 날짜를 다시 추정하지 않는다.
    """
    if not isinstance(job, Mapping):
        raise ValueError("공고는 사전이어야 합니다.")
    current = _aware_timestamp(now_iso() if now is None else now, "기준 시각")
    parsed = _parse_deadline(job.get("deadline"))
    if parsed is None:
        return "rolling" if _rolling(job.get("deadline_raw")) or _rolling(job.get("deadline")) else "unknown"
    if parsed.moment is None:
        # 다음 날 자정을 사용해 과거 서울의 반복되는 마지막 시간도 포함한다.
        if parsed.day < date.max:
            following_day = parsed.day + timedelta(days=1)
            midnight = datetime(following_day.year, following_day.month, following_day.day, tzinfo=SEOUL)
            boundary = _instant(_ParsedDate("", following_day, midnight))[0]
        else:
            # 최대 연도에서도 달력 범위를 넘는 datetime을 생성하지 않는다.
            last_second = datetime(parsed.day.year, parsed.day.month, parsed.day.day, 23, 59, 59, tzinfo=SEOUL, fold=1)
            boundary = _instant(_ParsedDate("", parsed.day, last_second))[0] + 1
        return "expired" if _instant(current) >= (boundary, "") else "open"
    return "expired" if _instant(current) > _instant(parsed) else "open"


def _append_unique(target: list, values: Iterable) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _provenance(record: dict) -> tuple[list, list]:
    sources = [record["source_id"]]
    inherited_sources = record.get("source_ids", [])
    inherited_provenance = record.get("provenance", [])
    if not isinstance(inherited_sources, (list, tuple)) or not isinstance(inherited_provenance, (list, tuple)):
        raise ValueError("출처 목록과 근거 이력은 목록이어야 합니다.")
    _append_unique(sources, [_text(value, "출처 식별자", required=True) for value in inherited_sources])
    provenance = []
    for entry in inherited_provenance:
        if not isinstance(entry, Mapping):
            raise ValueError("근거 이력의 각 항목은 사전이어야 합니다.")
        source = _text(entry.get("source_id"), "근거 이력의 출처 식별자", required=True)
        _append_unique(sources, [source])
        _append_unique(provenance, [dict(entry)])
    current = {"source_id": record["source_id"]}
    if "evidence" in record:
        if not isinstance(record["evidence"], Mapping):
            raise ValueError("근거 항목은 사전이어야 합니다.")
        current["evidence"] = deepcopy(dict(record["evidence"]))
    if "collected_at" in record:
        current["collected_at"] = record["collected_at"]
    _append_unique(provenance, [current])
    return sources, provenance


def deduplicate_jobs(jobs: Iterable[Mapping]) -> list:
    """정규 URL만을 기준으로 중복을 합치고 최초 공고·순서를 보존한다.

    입력을 깊은 복사하고 URL과 id를 다시 계산한다. 동일 회사·제목이라도 URL이
    다르면 합치지 않는다. 첫 공고의 필드는 유지하고 warnings·source_ids·provenance는
    최초 등장 순서로 중복 없이 합친다. provenance는 source_id, evidence, collected_at을
    보존하며 후자의 두 항목은 입력에 있을 때 포함한다. 같은 출처의 새로운 수집
    근거도 보존한다. 이미 합친 결과를 다시 합쳐도 결과가 변하지 않는다.
    각 입력은 최소한 유효한 url·source_id를 가져야 하며 잘못된 입력은 ValueError다.
    """
    if isinstance(jobs, (str, bytes, Mapping)):
        raise ValueError("공고 목록 또는 공고를 순서대로 제공하는 반복자가 필요합니다.")
    try:
        iterator = iter(jobs)
    except TypeError as exc:
        raise ValueError("공고 목록 또는 반복자가 필요합니다.") from exc
    by_url = {}
    for item in iterator:
        if not isinstance(item, Mapping):
            raise ValueError("공고 목록의 각 항목은 사전이어야 합니다.")
        record: dict[str, Any] = deepcopy(dict(item))
        url = canonical_url(record.get("url", ""))
        record["source_id"] = _text(record.get("source_id"), "출처 식별자", required=True)
        record["url"] = url
        record["id"] = _job_id(url)
        warnings = []
        _append_unique(warnings, _warnings(record.get("warnings", [])))
        record["warnings"] = warnings
        record["source_ids"], record["provenance"] = _provenance(record)
        if url not in by_url:
            by_url[url] = record
        else:
            first = by_url[url]
            for field in ("warnings", "source_ids", "provenance"):
                _append_unique(first[field], record[field])
    return list(by_url.values())
