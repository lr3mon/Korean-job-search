#!/usr/bin/env python3
"""Build the packaged, exactly reconciled employer directory from research batches."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from korean_job_search.registry import validate_companies


def build():
    folder = ROOT / "data/research"
    selection = json.loads((folder / "selection.json").read_text(encoding="utf-8"))
    expected = {r["id"]: r for r in selection["companies"]}
    if len(expected) != 100:
        raise ValueError("선정 목록은 정확히 100개의 고유 ID여야 합니다.")
    records, evidence = [], []
    for batch in range(1, 5):
        rows = json.loads((folder / f"companies-{batch}.json").read_text(encoding="utf-8"))
        proof = json.loads((folder / f"evidence-{batch}.json").read_text(encoding="utf-8"))
        wanted = {key for key, value in expected.items() if value["batch"] == batch}
        if len(rows) != 25 or {r["id"] for r in rows} != wanted:
            raise ValueError(f"batch {batch}: 25개 선정 대상과 일치하지 않습니다.")
        for row in rows:
            source = expected[row["id"]]
            if row["name_ko"] != source["name_ko"] or row["selection_index"] != source["selection_index"]:
                raise ValueError(f"원본 식별자/이름 불일치: {row['id']}")
        records.extend(rows)
        evidence.extend(proof)
    summary = validate_companies(records)
    supported = {r.get("company_id", r.get("id")) for r in evidence if r.get("snippet") and (r.get("source_url") or r.get("url"))}
    if set(expected) - supported:
        raise ValueError("실제 출처 근거 없는 기업: " + str(sorted(set(expected) - supported)))
    records.sort(key=lambda r: r["selection_index"])
    payload = {"schema_version": 1, "not_a_ranking": True,
               "selection_basis": selection["selection_basis"],
               "selection_revision_note": selection.get("selection_revision_note", ""),
               "coverage": summary, "evidence_record_count": len(evidence), "companies": records}
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        payload = build()
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        target = ROOT / "korean_job_search/data/companies.json"
        if args.check:
            if not target.is_file() or target.read_text(encoding="utf-8") != text:
                raise ValueError("배포 디렉터리와 조사 자료가 다릅니다. build_catalog.py를 실행하세요.")
        else:
            if target.is_symlink():
                raise ValueError("출력 심볼릭 링크 거부")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        print(json.dumps(payload["coverage"] | {"evidence_record_count": payload["evidence_record_count"], "check": args.check}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
