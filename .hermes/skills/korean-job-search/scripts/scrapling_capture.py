#!/usr/bin/env python3
"""Optional, bounded portal snapshot helper. Run from the repository root.

HTTP: SafeFetcher + Scrapling's parser. Browser: an explicit, anonymous render
AFTER successful robots-aware HTTP preflight. Saved HTML: offline parsing only.
This is not a replacement for `collect`, a full-feed crawler, or an apply bot.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 4_000_000
HOSTS = {"www.jobkorea.co.kr", "jobkorea.co.kr", "www.wanted.co.kr", "wanted.co.kr"}
INERT = ("script", "style", "noscript", "template")


def public_url(value: str) -> str:
    """Restrict this helper to observed public listing/detail route families."""
    from korean_job_search.network import _validate_url
    target = _validate_url(value)
    parts = urlsplit(target.url)
    if parts.scheme != "https" or parts.hostname not in HOSTS:
        raise ValueError("Only HTTPS JobKorea/Wanted public posting URLs are supported.")
    if parts.hostname.endswith("jobkorea.co.kr"):
        allowed = (parts.path.rstrip("/") == "/Search" or
                   re.fullmatch(r"/Recruit/GI_Read/\d+", parts.path) or
                   parts.path == "/Recruit/GI_Read_Comt_Ifrm")
    else:
        allowed = parts.path.rstrip("/") == "/wdlist" or re.fullmatch(r"/wd/\d+", parts.path)
    if not allowed:
        raise ValueError("Login, account, applicant and non-posting routes are not supported.")
    return target.url


def private_path(root: Path, value: str, *, existing=False) -> Path:
    """No workspace escape or symlink traversal, including parent directories."""
    root = root.resolve()
    path = Path(os.path.abspath(root / value))
    relative = path.relative_to(root / "workspace")
    if not relative.parts:
        raise ValueError("Use a named path inside workspace, not workspace itself.")
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise ValueError("Symlinks are not allowed in snapshot paths.")
    if existing and not path.is_file():
        raise ValueError("Input must be a regular UTF-8 HTML file inside workspace.")
    return path


def selector_class():
    try:
        from scrapling import Selector
    except ImportError as exc:
        raise RuntimeError("Scrapling is optional: use the isolated setup in references/scrapling.md.") from exc
    return Selector


def extract(html: str, url: str, *, kind="links", selector=None, limit=20) -> dict:
    root = selector_class()(html)
    titles = root.css("title")
    title = str(titles[0].get_all_text(strip=True)) if titles else ""
    if (re.search(r"^(?:access denied|just a moment|verify you are human|403|error:)", title, re.I)
            or root.css("#challenge-form, input[type='password']")):
        return {"status": "blocked", "diagnostics": ["Challenge/login page; no bypass attempted."]}
    iframes = []
    for node in root.css("iframe[src]"):
        candidate = urljoin(url, node.attrib["src"])
        try:
            candidate = public_url(candidate)
        except Exception:
            continue
        if urlsplit(candidate).path == "/Recruit/GI_Read_Comt_Ifrm" and candidate not in iframes:
            iframes.append(candidate)
    common = {"page_title": title, "jd_iframe_urls": iframes,
              "completeness": "unverified", "diagnostics": []}
    if kind == "jd":
        if not selector:
            raise ValueError("JD mode requires an explicitly inspected --selector.")
        if iframes:
            return dict(common, status="needs_iframe", diagnostics=[
                "Read the observed JD iframe separately; parent recommendations and old essays are not the JD."])
        if selector.strip().lower() in {"html", "body", "*"} and urlsplit(url).path != "/Recruit/GI_Read_Comt_Ifrm":
            raise ValueError("Select only the current JD container, not the whole parent page.")
        nodes = root.css(selector)
        if not nodes:
            return dict(common, status="selector_empty", diagnostics=["Selector matched no elements; not an empty JD."])
        text = "\n\n".join(str(n.get_all_text(separator="\n", strip=True, ignore_tags=INERT)) for n in nodes).strip()
        if not text:
            return dict(common, status="selector_empty", diagnostics=["Selected elements contained no text."])
        return dict(common, status="partial", selector=selector, text=text, text_chars=len(text),
                    diagnostics=["Text surface only: manually verify employer, role, expanded sections, images and deadline."])
    records = {}
    for node in root.css("a[href]"):
        candidate = urljoin(url, node.attrib["href"])
        parts = urlsplit(candidate)
        if parts.scheme != "https" or parts.hostname not in HOSTS or parts.username or parts.password:
            continue
        pattern = r"/Recruit/GI_Read/(\d+)" if parts.hostname.endswith("jobkorea.co.kr") else r"/wd/(\d+)"
        match = re.fullmatch(pattern, parts.path)
        if not match:
            continue
        # Validate query keys too; never turn a credential-bearing URL into an output link.
        try:
            candidate = public_url(candidate)
        except Exception:
            continue
        key = (parts.hostname.removeprefix("www."), match.group(1))
        record = records.setdefault(key, {"posting_id": match.group(1), "url": candidate,
                                          "labels": [], "image_alt": []})
        text = str(node.get_all_text(separator=" ", strip=True, ignore_tags=INERT)).strip()
        if text and text not in record["labels"]:
            record["labels"].append(text)
        for image in node.css("img[alt]"):
            alt = image.attrib["alt"].strip()
            if alt and alt not in record["image_alt"]:
                record["image_alt"].append(alt)
    rows = [r for r in records.values() if r["labels"] or r["image_alt"]]
    return dict(common, status="partial" if rows else "parser_unsupported", links=rows[:limit],
                unique_links_found=len(rows), returned=len(rows[:limit]), truncated=len(rows) > limit,
                diagnostics=["Single-page link candidates, not a complete feed or verified JDs. Ads and organic results are not separated."])


def http_fetch(url: str, timeout: float) -> dict:
    from korean_job_search.network import SafeFetcher
    return SafeFetcher(timeout=timeout, max_bytes=MAX_BYTES).fetch(url, respect_robots=True)


def browser_fetch(url: str, timeout: float, directory: Path) -> dict:
    from scrapling.fetchers import DynamicFetcher
    # No existing profile, CDP, credentials, cookies, proxy or CAPTCHA solver.
    with tempfile.TemporaryDirectory(prefix="browser-", dir=directory) as profile:
        response = DynamicFetcher.fetch(url, headless=True, google_search=False,
                                        cookies=[], user_data_dir=profile,
                                        timeout=timeout * 1000, wait=1500,
                                        network_idle=False, retries=1)
        return {"http_status": response.status, "url": str(response.url),
                "text": str(response.html_content)}


def acquire(url: str, engine: str, timeout: float, directory: Path) -> dict:
    url = public_url(url)
    result = http_fetch(url, timeout)
    if result.get("status") != "ok":
        # In particular, an unverifiable robots policy does NOT trigger a browser.
        return {"status": result.get("status", "error"), "source_url": url,
                "http_status": result.get("http_status"), "phase": "http_preflight",
                "robots_state": "not_verified_allowed", "method": "safe-http-preflight",
                "fetched_at": result.get("fetched_at"), "diagnostics": result.get("diagnostics", [])}
    final_url = public_url(result.get("url") or url)
    if engine == "browser":
        result = browser_fetch(final_url, timeout, directory)
        if result["http_status"] != 200:
            return {"status": "blocked", "source_url": url, "http_status": result["http_status"],
                    "robots_state": "http_preflight_passed", "method": "scrapling-browser",
                    "diagnostics": ["Browser did not return HTTP 200; no retry/escalation."]}
        # A browser redirect does not inherit a different path's preflight.
        if public_url(result["url"]) != final_url:
            return {"status": "redirect_review", "source_url": url, "http_status": 200,
                    "method": "scrapling-browser", "diagnostics": [
                        "Browser changed the URL after preflight; inspect and check the exact target before reuse."]}
    return {"status": "ok", "source_url": final_url, "http_status": result["http_status"],
            "html": result["text"], "robots_state": "http_preflight_passed",
            "method": "scrapling-browser" if engine == "browser" else "safe-http+scrapling-parser",
            "fetched_at": datetime.now(timezone.utc).isoformat()}


def save_text(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)
    path.chmod(0o600)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="Observed public JobKorea/Wanted URL")
    source.add_argument("--html", help="Already captured UTF-8 HTML inside workspace; no network")
    parser.add_argument("--source-url", help="Required with --html; preserve actual source URL")
    parser.add_argument("--engine", choices=("http", "browser"), default="http")
    parser.add_argument("--kind", choices=("links", "jd"), default="links")
    parser.add_argument("--selector", help="Inspected current-JD container; required for --kind jd")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--out", required=True, help="NEW directory under workspace; never overwritten")
    args = parser.parse_args(argv)
    root = Path.cwd().resolve()
    if not (root / "korean_job_search/network.py").is_file():
        parser.error("Run from the Korean-job-search repository root.")
    sys.path.insert(0, str(root))
    directory = None
    try:
        if not 1 <= args.limit <= 50 or not 0 < args.timeout <= 60:
            raise ValueError("limit must be 1..50 and timeout must be >0 and <=60 seconds.")
        if args.kind == "jd" and not args.selector:
            raise ValueError("JD mode requires --selector.")
        if args.html and (not args.source_url or args.engine != "http"):
            raise ValueError("Saved HTML requires --source-url and does not accept --engine browser.")
        if args.url and args.source_url:
            raise ValueError("--source-url is only for --html.")
        url = public_url(args.url or args.source_url)
        input_path = private_path(root, args.html, existing=True) if args.html else None
        if input_path and input_path.stat().st_size > MAX_BYTES:
            raise ValueError("Saved HTML exceeds the 4 MB bound.")
        directory = private_path(root, args.out)
        if directory.exists():
            raise ValueError("Output directory already exists; choose a new snapshot name.")
        selector_class()  # Fail on a missing optional package before creating files or fetching.
        missing = []
        parent = directory.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for parent in reversed(missing):
            parent.mkdir(mode=0o700)
        directory.mkdir(mode=0o700)
        if input_path:
            result = {"status": "ok", "html": input_path.read_text(encoding="utf-8-sig"),
                      "source_url": url, "method": "provided-html+scrapling-parser",
                      "fetched_at": None, "http_status": None, "robots_state": "not_checked_offline"}
        else:
            result = acquire(url, args.engine, args.timeout, directory)
        html = result.pop("html", None)
        if html is not None:
            if len(html.encode("utf-8")) > MAX_BYTES:
                raise ValueError("Captured HTML exceeds the 4 MB bound.")
            save_text(directory / "source.html", html)
            result.update(extract(html, result["source_url"], kind=args.kind,
                                  selector=args.selector, limit=args.limit))
            if "text" in result:
                text = result.pop("text")
                save_text(directory / "jd.txt", text + "\n")
                result["text_file"] = "jd.txt"
        result["processed_at"] = datetime.now(timezone.utc).isoformat()
        result["untrusted_source"] = True
        save_text(directory / "snapshot.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"status": result["status"], "method": result["method"],
                          "returned": result.get("returned"), "text_chars": result.get("text_chars"),
                          "snapshot": str(directory.relative_to(root) / "snapshot.json")}, ensure_ascii=False))
        return 0 if result["status"] == "partial" else 2
    except Exception as exc:
        # Do not leak source query strings or browser/profile exceptions.
        print(json.dumps({"status": "error", "error_type": type(exc).__name__,
                          "message": str(exc) if isinstance(exc, (ValueError, RuntimeError)) else
                          "Snapshot failed; inspect local dependencies/path/browser readiness. No retry performed."}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
