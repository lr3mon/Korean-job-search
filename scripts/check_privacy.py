#!/usr/bin/env python3
"""Check tracked path boundaries and obvious credentials without printing secrets."""
from __future__ import annotations
import fnmatch
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

FORBIDDEN = ("workspace/*", "private/*", ".env", ".env.*", "*.pem", "*.key", "**/auth.json", "**/cookies.json")
SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{32,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


def forbidden_path(value):
    path = PurePosixPath(value).as_posix()
    if path == ".env.example":
        return False
    return any(fnmatch.fnmatch(path, pattern) for pattern in FORBIDDEN)


def check(root):
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
    paths = [p for p in result.stdout.decode("utf-8").split("\0") if p]
    findings = []
    for name in paths:
        if forbidden_path(name):
            findings.append({"file": name, "reason": "private path tracked"})
        target = root / name
        if not target.is_file() or target.is_symlink():
            continue
        if target.stat().st_size > 2_000_000:
            findings.append({"file": name, "reason": "large file requires manual privacy review"})
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        if any(p.search(text) for p in SECRET_PATTERNS):
            findings.append({"file": name, "reason": "credential-like text detected (value hidden)"})
    return {"tracked_files_checked": len(paths), "ok": not findings, "findings": findings,
            "limitation": "Heuristic only; manually review staged diff for personal data. Untracked files are not checked."}


def main():
    try:
        result = check(Path(__file__).resolve().parents[1])
    except (OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1

if __name__ == "__main__":
    sys.exit(main())
