"""Agent-neutral CLI. Deterministic tools only; never submits applications."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

from . import __version__, registry


def emit(value, stream=None):
    print(json.dumps(value, ensure_ascii=False, indent=2), file=stream or sys.stdout)


def _no_symlinks(path):
    target = Path(path).absolute()
    for part in (target, *target.parents):
        if part.is_symlink():
            raise ValueError(f"심볼릭 링크 출력 경로는 허용하지 않습니다: {part}")
    return target


def _private_parents(path):
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=True)


def atomic_json(path, data):
    target = _no_symlinks(path)
    _private_parents(target)
    fd, tmp = tempfile.mkstemp(prefix=".kjs-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return str(target)


@contextmanager
def file_lock(path, timeout=10):
    target = _no_symlinks(str(path) + ".lock")
    _private_parents(target)
    start = time.monotonic()
    while True:
        try:
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if time.monotonic() - start >= timeout:
                raise TimeoutError(f"다른 작업이 기록 중입니다: {target}. 종료된 작업의 잠금인지 확인하세요.")
            time.sleep(0.05)
    try:
        os.close(fd)
        yield
    finally:
        target.unlink()


def load_profile(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("profile.json은 객체여야 합니다.")
    for key in ("target_roles", "keywords", "preferred_locations", "excluded_keywords"):
        values = data.get(key, [])
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError(f"프로필 {key}는 문자열 배열이어야 합니다.")
    if not isinstance(data.get("stories", []), list):
        raise ValueError("프로필 stories는 배열이어야 합니다.")
    return data


def save_collection(results, outpath):
    from .models import deduplicate_jobs, now_iso
    from .workflow import load_jobs
    fresh = [job for result in results for job in result.get("jobs", [])]
    with file_lock(outpath):
        existing = load_jobs(outpath) if Path(outpath).exists() else []
        # Fresh observations first; the model retains coherent detail snapshots and all alternatives.
        jobs = deduplicate_jobs(fresh + existing)
        observations = [{k: v for k, v in r.items() if k != "jobs"} | {"job_count": len(r.get("jobs", []))} for r in results]
        path = atomic_json(outpath, {"schema_version": 1, "updated_at": now_iso(), "jobs": jobs,
                                     "latest_acquisition": observations,
                                     "note": "실패한 출처의 기존 공고는 보존됩니다. 최신 여부는 각 collected_at/마감 원문으로 확인하세요."})
    return {"output": path, "new_observations": len(fresh), "stored_jobs": len(jobs),
            "source_statuses": dict(Counter(r["status"] for r in results)), "sources": observations}


def _selection(args, default=False):
    sources = registry.all_sources()
    if getattr(args, "all_companies", False):
        selected = [s for s in sources if s["kind"] == "company"]
    else:
        selected = []
    for values, multiple in ((getattr(args, "source", []) or [], False), (getattr(args, "company", []) or [], True)):
        for value in values:
            selected.extend(registry.resolve_sources([x.strip() for x in value.split(",")], sources, allow_many=multiple))
    if not selected:
        if default:
            selected = registry.resolve_sources(["NAVER", "카카오", "wanted", "saramin", "jobkorea"], sources)
        else:
            selected = sources
    return list({s["id"]: s for s in selected}.values())


def _positive(value):
    n = int(value)
    if n < 1 or n > 1_000_000:
        raise argparse.ArgumentTypeError("1~1,000,000 범위의 정수를 입력하세요.")
    return n


def build_parser():
    p = argparse.ArgumentParser(prog="kjs", description="한국 공식 채용공고 탐색·근거 기반 지원서 작업 도구 (자동 제출 없음)")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--workspace", default=os.environ.get("KJS_WORKSPACE", "workspace"), help="개인 데이터 폴더 (기본: ./workspace)")
    commands = p.add_subparsers(dest="command", required=True)
    c = commands.add_parser("init", help="비공개 작업 폴더 초기화; 기존 자료 보존")
    c.add_argument("--path", help="--workspace 대신 사용할 경로")
    c = commands.add_parser("sources", help="기업/포털 출처와 검증 근거 조회")
    c.add_argument("--company", action="append")
    c.add_argument("--source", action="append")
    c.add_argument("--all-companies", action="store_true")
    c.add_argument("--json", action="store_true", help="JSON 출력 (기본 동작)")
    c.add_argument("--verify", action="store_true", help="등록 URL을 실제 조회; 공고 전체수집 검증과는 다름")
    c.add_argument("--out")
    c.add_argument("--workers", type=_positive, default=3)
    c.add_argument("--timeout", type=_positive, default=12)
    c = commands.add_parser("discover", help="공식 출처 우선 검색 계획 생성 (에이전트가 검색 도구로 실행)")
    c.add_argument("--query", required=True)
    c.add_argument("--source", action="append")
    c.add_argument("--company", action="append")
    c.add_argument("--all-companies", action="store_true")
    c.add_argument("--out")
    c = commands.add_parser("collect", help="공개 페이지 실제 수집; 출처별 실패 상태 보존")
    c.add_argument("--source", action="append", help="ID/이름, 여러 번 또는 쉼표 사용")
    c.add_argument("--company", action="append", help="기업 이름/별칭; 그룹 검색 시 여러 회사 가능")
    c.add_argument("--all-companies", action="store_true")
    c.add_argument("--query", default="")
    c.add_argument("--limit", type=_positive, default=20, help="출처별 최대 공고 수")
    c.add_argument("--max-sources", type=_positive, help="이 수를 넘으면 중단 (묵시적 누락 없음)")
    c.add_argument("--workers", type=_positive, default=3)
    c.add_argument("--timeout", type=_positive, default=12)
    c.add_argument("--out")
    c = commands.add_parser("ingest", help="실제 JD URL 또는 사용자가 복사한 원문 보관")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--url"); g.add_argument("--file")
    c.add_argument("--source-url", help="붙여넣은 원문의 출처 URL")
    c.add_argument("--company", default="")
    c.add_argument("--title")
    c.add_argument("--out")
    c = commands.add_parser("rank", help="명시된 키워드 기반 우선순위 (합격확률 아님)")
    c.add_argument("--jobs"); c.add_argument("--profile"); c.add_argument("--out")
    c = commands.add_parser("prepare", help="공고/사실근거/문항/검토용 지원 패킷 생성")
    c.add_argument("--job", required=True); c.add_argument("--jobs"); c.add_argument("--profile"); c.add_argument("--out")
    c = commands.add_parser("answers-check", help="한국어 글자/UTF-16/UTF-8/CP949 바이트 제한 검사")
    c.add_argument("file"); c.add_argument("--limit", type=_positive, required=True)
    c.add_argument("--mode", choices=["codepoints", "utf16", "utf8-bytes", "cp949-bytes"], default="codepoints")
    c.add_argument("--exclude-spaces", action="store_true")
    c.add_argument("--newlines", choices=["lf", "crlf"], default="lf")
    c.add_argument("--min-fill", type=float, default=0.75)
    c.add_argument("--json", action="store_true")
    c = commands.add_parser("track", help="지원 상태 수동 기록; --confirm도 전송/제출하지 않음")
    c.add_argument("--job"); c.add_argument("--status", choices=["drafted", "applied", "interview", "offer", "rejected", "withdrawn", "hired"]); c.add_argument("--notes", default="")
    c.add_argument("--confirm", action="store_true", help="이미 직접 제출한 사실의 기록을 확인")
    c = commands.add_parser("report", help="외부 요청 없는 로컬 HTML 보고서")
    c.add_argument("--jobs"); c.add_argument("--applications"); c.add_argument("--out")
    commands.add_parser("doctor", help="설치/파일/경로 확인; 모델 호출이나 로그인 없음")
    return p


def _verify_sources(selected, timeout, workers):
    from .network import SafeFetcher
    urls = list(dict.fromkeys(s["careers_url"] for s in selected))
    # Same group portal is requested once; each employer retains its own evidence row.
    def check(url):
        result = SafeFetcher(timeout=timeout).fetch(url)
        text = result.get("text", "")
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        status = result.get("status", "error")
        return {"requested_url": url, "final_url": result.get("url", url), "status": status,
                "http_status": result.get("http_status"), "content_characters": len(text),
                "diagnostics": result.get("diagnostics", []),
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "scope": "URL 접근성 점검; 회사별 공고 파싱/전체 수집 증명 아님"}
    with ThreadPoolExecutor(max_workers=min(workers, 8)) as pool:
        checks = dict(zip(urls, pool.map(check, urls)))
    rows = [{"id": s["id"], "name_ko": s["name_ko"], **checks[s["careers_url"]]} for s in selected]
    return {"checked_source_count": len(rows), "unique_urls_requested": len(urls),
            "status_counts": dict(Counter(r["status"] for r in rows)), "checks": rows}


def _collect(selected, query, limit, timeout, workers):
    from .collectors import collect_source
    from .network import SafeFetcher
    # Serialize same origin to avoid multiply hitting shared corporate portals.
    from urllib.parse import urlsplit
    groups = {}
    for source in selected:
        groups.setdefault(urlsplit(source["careers_url"]).netloc, []).append(source)
    def collect_group(group):
        fetcher = SafeFetcher(timeout=timeout)
        rows = []
        seen = {}
        for source in group:
            key = (source["careers_url"], source.get("adapter", "generic"))
            if key in seen:
                # Never relabel a group listing as a different employer's jobs.
                base = seen[key]
                rows.append({"source_id": source["id"], "source_url": source["careers_url"],
                             "status": "partial" if base.get("jobs") else base["status"], "jobs": [],
                             "fetched_at": base.get("fetched_at"),
                             "diagnostics": [f"동일 그룹 채용창구는 {base['source_id']}에서 조회했습니다. 각 공고의 실제 고용 법인을 확인하세요."],
                             "shared_with": base["source_id"]})
                continue
            try:
                result = collect_source(source, query=query, limit=limit, fetcher=fetcher)
            except Exception as exc:
                result = {"source_id": source["id"], "source_url": source["careers_url"], "status": "error",
                          "jobs": [], "diagnostics": [f"{type(exc).__name__}: {exc}"],
                          "fetched_at": datetime.now(timezone.utc).isoformat()}
            seen[key] = result
            rows.append(result)
        return rows
    with ThreadPoolExecutor(max_workers=min(workers, 8)) as pool:
        return [result for batch in pool.map(collect_group, groups.values()) for result in batch]


def run(args):
    ws = Path(args.workspace).expanduser()
    if args.command == "doctor":
        root = Path(__file__).parent.parent
        emit({"version": __version__, "python": sys.version.split()[0], "package_root": str(root),
              "workspace": str(ws.absolute()), "runtime_dependencies": [], "network_used": False,
              "model_sessions_tested": False,
              "agents": {name: {"executable": shutil.which(binary), "note": "실행 파일 탐지이며 모델 세션 검증 아님"}
                         for name, binary in [("hermes", "hermes"), ("prime-agent", "prime-agent"), ("codex", "codex"), ("claude-code", "claude"), ("openclaw", "openclaw")]},
              "catalogue_present": (registry.DATA_DIR / "companies.json").exists(),
              "canonical_skill": str(root / ".agents/skills/korean-job-search/SKILL.md"),
              "privacy": "workspace/private/.env는 커밋 금지. 클라우드 모델을 사용하면 읽은 내용은 해당 제공자에게 전달될 수 있습니다."})
        return 0
    if args.command == "sources":
        selected = _selection(args)
        result = _verify_sources(selected, args.timeout, args.workers) if args.verify else {
            "count": len(selected), "not_a_ranking": True, "selection_basis": registry.SELECTION_BASIS, "sources": selected}
        if args.out:
            output = atomic_json(args.out, result)
            emit({k: v for k, v in result.items() if k not in {"sources", "checks"}} | {"output": output})
        else:
            emit(result)
        return 0
    if args.command == "discover":
        selected = _selection(args)
        result = registry.build_discovery_plan(selected, args.query)
        if args.out:
            emit({"status": result["status"], "live_search_executed": False, "task_count": result["task_count"], "output": atomic_json(args.out, result)})
        else:
            emit(result)
        return 0
    if args.command == "collect":
        selected = _selection(args, default=True)
        if args.max_sources and len(selected) > args.max_sources:
            raise ValueError(f"{len(selected)}개 출처가 --max-sources {args.max_sources}를 초과합니다. 누락 없이 실행 범위를 다시 선택하세요.")
        if args.limit > 100:
            raise ValueError("공개 사이트 부하 방지를 위해 출처당 limit은 100 이하입니다.")
        results = _collect(selected, args.query, args.limit, args.timeout, args.workers)
        emit(save_collection(results, args.out or ws / "jobs.json"))
        return 0 if any(r["status"] in {"ok", "partial", "empty"} for r in results) else 2
    if args.command == "ingest":
        from .models import make_job, now_iso
        if args.url:
            from .collectors import ingest_url
            result = ingest_url(args.url)
        else:
            if not args.source_url:
                raise ValueError("--file에는 실제 원문 출처 --source-url이 필요합니다.")
            if not registry._public_shape(args.source_url):
                raise ValueError("공고 출처는 HTTP(S) URL이어야 합니다.")
            text = Path(args.file).read_text(encoding="utf-8")
            if not text.strip():
                raise ValueError("빈 공고 원문")
            if len(text) > 2_000_000:
                raise ValueError("공고 원문은 2MB 이하로 나누어 주세요.")
            title = args.title or next(s.strip() for s in text.splitlines() if s.strip())[:200]
            now = now_iso()
            job = make_job("manual", title, args.company or "Unknown employer", args.source_url, description=text,
                           evidence={"kind": "manual_paste", "source_url": args.source_url, "fetched_at": now},
                           warnings=["사용자가 제공한 원문이며 사이트의 현재 상태/마감/완전성은 별도 확인 필요"])
            result = {"source_id": "manual", "source_url": args.source_url, "status": "ok", "jobs": [job], "diagnostics": [], "fetched_at": now}
        emit(save_collection([result], args.out or ws / "jobs.json"))
        return 0 if result.get("jobs") else 2
    from . import workflow
    if args.command == "init":
        emit(workflow.init_workspace(args.path or ws)); return 0
    if args.command == "rank":
        jobs = workflow.load_jobs(args.jobs or ws / "jobs.json")
        profile = load_profile(args.profile or ws / "profile.json")
        result = workflow.rank_jobs(jobs, profile)
        output = atomic_json(args.out or ws / "ranked.json", {"jobs": result, "note": "키워드 기반 우선순위이며 합격확률/공인 평가 점수가 아닙니다."})
        emit({"count": len(result), "output": output, "top": result[:5], "method": "transparent_keyword_heuristic_not_hiring_probability"}); return 0
    if args.command == "prepare":
        jobs = workflow.load_jobs(args.jobs or ws / "jobs.json")
        matches = [j for j in jobs if j.get("id") == args.job]
        if len(matches) != 1:
            raise ValueError(f"정확히 하나의 공고 ID를 지정하세요: {args.job}")
        if any(c in args.job for c in ("/", "\\", "..")):
            raise ValueError("공고 ID는 파일 경로가 될 수 없습니다.")
        result = workflow.prepare_application(matches[0], load_profile(args.profile or ws / "profile.json"),
                                              args.out or ws / "applications" / args.job)
        emit(result); return 0
    if args.command == "answers-check":
        if not 0 <= args.min_fill <= 1:
            raise ValueError("min-fill은 0~1 범위입니다.")
        text = Path(args.file).read_text(encoding="utf-8")
        result = workflow.validate_answer(text, args.limit, mode=args.mode, include_spaces=not args.exclude_spaces,
                                          newlines=args.newlines, min_fill=args.min_fill)
        emit(result); return 0 if result.get("ok") else 1
    if args.command == "track":
        if not args.job:
            if args.status:
                raise ValueError("상태 기록에는 --job이 필요합니다.")
            path = ws / "applications.json"
            emit(json.loads(path.read_text(encoding="utf-8")) if path.exists() else []); return 0
        if not args.status:
            raise ValueError("--job과 함께 --status를 지정하세요.")
        # The workflow owns its atomic update lock; a second lock here deadlocks.
        result = workflow.track_application(ws, args.job, args.status, args.notes, confirm=args.confirm)
        emit(result); return 0
    if args.command == "report":
        jobs = workflow.load_jobs(args.jobs or ws / "jobs.json")
        app_path = Path(args.applications or ws / "applications.json")
        applications = json.loads(app_path.read_text(encoding="utf-8")) if app_path.exists() else []
        emit(workflow.render_report(jobs, applications, args.out or ws / "report.html")); return 0
    raise ValueError("알 수 없는 명령")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (OSError, ValueError, RuntimeError) as exc:
        emit({"error": str(exc), "type": type(exc).__name__}, stream=sys.stderr)
        return 2
    except KeyboardInterrupt:
        emit({"error": "사용자가 중단했습니다."}, stream=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
