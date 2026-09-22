# Scrapling skill integration — execution evidence

Verified: 2026-09-22T10:23:01.092027+00:00. Scope: an optional shared-skill helper, not a replacement for the core collectors.

## Executed environment and installation

- macOS; isolated CPython 3.13.2 environment created under ignored `workspace/tools/scrapling`.
- `uv pip install --python workspace/tools/scrapling/bin/python -e '.[scrapling]'` completed from package-index dependencies; Scrapling **0.4.11** imported successfully. The already installed browser cache was usable; no agent-global config, project trust, user profile or model authentication was changed.
- Default Python CLI remains standard-library-only. Optional imports are lazy and `--help` works without Scrapling.

## Real public-page runs

| Run | Actual result | Bound |
| --- | --- | --- |
| JobKorea HTTP + Scrapling parser | HTTP 200; 27 unique posting links found, 5 returned | One search-page snapshot; ads and organic results not separated |
| JobKorea anonymous Scrapling browser | HTTP 200; 27 unique posting links found, 5 returned | One render after successful robots-aware HTTP preflight; no existing profile or Google referer |
| Observed JobKorea JD iframe | HTTP 200; 974 text characters before the output newline | Responsibilities, requirements and preferences present; text completeness remains unverified |
| Saved-HTML parse | 5 links returned, no network | `fetched_at` and HTTP status remain null; not relabeled as a live fetch |
| Existing CLI import | 1 job stored; description exactly equals the generated `jd.txt` (975 characters including its newline) | `manual_paste` provenance retained; company/title supplied only after the real source was read |
| Wanted browser request | `robots_denied`, exit 2 at `http_preflight` | Robots endpoint returned HTTP 403; the browser was NOT launched |

Source URLs:
- HTTP listing: https://www.jobkorea.co.kr/Search/?stext=%EC%84%9C%EB%B9%84%EC%8A%A4%20%EA%B8%B0%ED%9A%8D
- Browser listing: https://www.jobkorea.co.kr/Search/?stext=%EC%84%9C%EB%B9%84%EC%8A%A4%20%EA%B8%B0%ED%9A%8D&tabType=recruit
- Observed JD iframe: https://www.jobkorea.co.kr/Recruit/GI_Read_Comt_Ifrm?Gno=50010663&isHiringCenter=false&hideMapView=false
- Wanted policy probe: https://www.wanted.co.kr/wdlist

All generated HTML, text and exact manifests remain private in ignored `workspace/scrapling-verification/`. No copied portal JD or applicant data is committed here. Counts describe these snapshots, not the full sites or a durable availability guarantee. No application was submitted.

## Offline and compatibility checks

- Full suite with the optional Scrapling environment: **263 tests, OK, no skips**.
- Full suite without Scrapling installed: **263 discovered, OK with 11 optional parser tests skipped**. The core suite and optional-tool safety tests still run.
- Added `tests/test_scrapling_skill.py`: **20 tests**, including real Scrapling parsing of explicitly synthetic fixtures, link deduplication, literal IDs, JD iframe scoping, challenge rejection, private paths, no overwrite, saved-source provenance and fail-closed browser gating.
- `scripts/sync_skills.py --check` passed for the canonical bundle and generated Claude Code/Hermes/OpenClaw copies. Codex and Prime Agent use the same canonical bundle. This is file/command compatibility, **not five model-runtime executions**.
- Initial full-suite execution hit the pre-existing macOS system-temporary-directory symlink safeguard in 18 workflow tests. Re-running with a real non-symlink `TMPDIR` passed; the production safeguard was not weakened. See the existing macOS test note in the usage guide.

An optional `scrapling-parser-tests` CI job now installs the extra and runs the synthetic-fixture helper suite. The workflow was added locally; **GitHub Actions was not run or published in this task**. Live recruiting sites and browser downloads are not part of that job.

## Limits kept explicit

The helper supports allowlisted JobKorea/Wanted public listing and detail route families only, at most 50 returned links and one selected page at a time. Missing parser coverage is not an empty feed. JD mode requires an inspected selector and never treats parent-page historical essays as current questions. A 403 robots response is not an observed Disallow rule, but it still stops automatic acquisition. Browser subresource traffic is not the core HTTP fetcher's IP-pinned transport. Public readability is not consent for recurring/bulk collection or redistribution. No stealth escalation, CAPTCHA solver, proxy rotation, cookie reuse, scheduler or paid model evaluation was added.
