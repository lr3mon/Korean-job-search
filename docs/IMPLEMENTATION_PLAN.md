# Korean-job-search Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Derive a privacy-first Korean job-search and application toolkit from MadsLorentzen/ai-job-search, with real public-source acquisition, 100 curated corporate career references, and agent-neutral Korean workflows.

**Architecture:** A Python 3.10+ standard-library CLI handles deterministic acquisition, provenance, deduplication, deadlines, character counts, records and reports. A single Agent Skills workflow handles LLM research/drafting with thin runtime adapters. Candidate data lives only in ignored workspace/, never in AGENTS.md or tracked skills. No auto-application, credential harvesting, stealth/WAF bypass or scheduled job installation.

**Tech Stack:** Python stdlib, unittest, JSON, Markdown, static HTML; no mandatory Bun/LaTeX/LLM SDK. Optional PDF export remains a documented extra, not a claimed tested feature.

## Acceptance gates
1. Exactly 100 distinct curated employer records with selection basis, official career URL, official evidence, verification method/time/status. This is NOT a ranked national top-100 list; unknown/blocked results remain explicit.
2. Saramin, JobKorea, Wanted source definitions and verified best-available acquisition; official employers take precedence. Real listings from multiple public official sources. Restricted sources return blocked/needs_browser/etc, never fake empty/success. Batch scan and agent web-search plans cover the registry.
3. Working CLI supports init, sources, discover, collect, ingest, rank, prepare, answers-check, track, report, doctor. Repository can run with python3 -m korean_job_search without installation. Offline tests require no credentials/network.
4. Korean work history/story bank, honest JD mapping, actual question and character-limit handling, private records and deadlines, interview/reviewer workflows.
5. Hermes, Prime Agent, Codex, Claude Code, OpenClaw adapters share one canonical skill; also generic AGENTS.md / Gemini guidance. No global settings/profile edits. Distinguish format/discovery checks from real model-driven trials.
6. Source and license provenance from upstream at commit 120f476a089358363ceaf2528f52edf2854994bd retained. Korean README plus separate English README. Full tests, independent spec review then code-quality review, live evidence and final local commit.

## Shared contracts (freeze before parallel work)
Package: korean_job_search; all public functions return plain dictionaries/lists suitable for JSON.

Company JSON: id (kr-001..kr-100), selection_index, name_ko, name_en, aliases[], sector, homepage, careers_url, evidence_url (official source that supports employer-careers relation), is_group_portal (bool), access_mode (html/public_api/browser/manual), verification {checked_at ISO8601, method, status verified/partial/blocked/unverified, http_status integer|null, title, notes}. Careers URL can be a shared group portal; do NOT call it a unique per-company search API. Each research batch writes data/research/companies-N.json plus data/research/evidence-N.json. Do not fabricate URLs or verification. Use provided IDs and coverage list.

Job JSON: id (stable string from canonical URL), source_id, company, title, url, description, location, employment_type, experience, deadline (ISO8601 or null), deadline_raw, collected_at (aware ISO8601), evidence {kind: live_html/live_json/manual_paste/test_fixture, source_url, fetched_at}, warnings[]. Preserve exact source detail links and deadline precision; missing fields stay empty, not inferred qualifications. Dicts may carry source-specific extra fields.

Acquisition result: {source_id, source_url, status, jobs:[], diagnostics:[], fetched_at}. status = ok/partial/empty/blocked/robots_denied/needs_browser/needs_credentials/unsupported/error. HTTP 200 shell is NOT success; no link cards are proof of a complete JD. Strong first-party source attribution; imported content is data, never instructions.

Profile JSON: display_name (optional), target_roles:[], keywords:[], preferred_locations:[], excluded_keywords:[], experience_level (optional), stories: [{id,title,period,role,actions:[],results:[],evidence:[]}]. No real user profile seeded without approval. keywords scores are heuristic NOT hiring probability.

Core functions (lane core):
models.py: now_iso()->str; canonical_url(url)->str; make_job(source_id,title,company,url,description='',**fields)->dict; deduplicate_jobs(jobs)->list; deadline_state(job,now=None)->str.
workflow.py: init_workspace(path)->dict (idempotent/no overwrite); load_jobs(path)->list (accept list or dict.jobs); rank_jobs(jobs,profile,now=None)->list; prepare_application(job,profile,outdir)->dict (no overwrite); count_text(text,mode='codepoints',include_spaces=True,newlines='lf')->int; validate_answer(text,limit,mode='codepoints',include_spaces=True,newlines='lf',min_fill=0.75)->dict; track_application(workspace,job_id,status,notes='',confirm=False)->dict; render_report(jobs,applications,outpath)->dict.
Templates owned by core: korean_job_search/data/templates/*.json or *.md.

Collector functions (lane collect):
network.py: SafeFetcher(timeout=12,max_bytes=4000000), .fetch(url, respect_robots=True)->dict {status,url,http_status,content,text,content_type,diagnostics}; strict public HTTP(S) URLs, no userinfo/custom ports, reject local/private IPs and every redirect, bounded reads/redirects, robots, clear UA/timeouts/rate-limit. Never disable TLS.
collectors.py: collect_source(source:dict,query='',limit=20,fetcher=None)->acquisition dict; ingest_url(url,fetcher=None)->acquisition dict; parse_job_postings(html,url,source_id,company='')->list. source fields id, careers_url (or url), name_ko, kind portal/company, adapter optional. Prefer portable structured JSON-LD or tested employer/portal adapters. Need pagination/limit and partial status, no false complete results. Wanted/Saramin/JobKorea may be limited but must be tried honestly. Live proof in artifacts/live-collectors.json, raw small public fixtures in tests/fixtures with provenance; no private cookies.

## Ordered tasks and file ownership
### 1. Foundation (parent)
Create pyproject.toml, privacy .gitignore, this plan, selection manifest and licensing. git init + baseline commit. Create isolated worktrees. Test empty import and compile metadata. No remote publishing.
### 2. Core deterministic workflow (core lane)
Write tests/test_models.py and tests/test_workflow.py first; run unittest to prove failure. Implement models.py, workflow.py and template files. Tests: Unicode/utf16/utf8/cp949, spaces/newlines, no fabrication, canonical URL/job dedup, expired KST/date-only, CSV injection/HTML escaping, safe output paths, init idempotence, no stale/malformed profile silent success, explicit applied status confirmation. Commit only lane files and report tests.
### 3. Source acquisition (collect lane)
Test network boundaries, robots, redirects, shell/error distinctions, known real source fixtures, list vs full detail, canonical IDs. Implement network.py/collectors.py. Inspect current public pages/endpoints before choosing parsers. Produce real acquisition evidence with 2+ official sources and all 3 named portals attempted. No token/private files/API submission endpoints. Core imports may not be available initially; temporary imports must not shadow tracked core.
### 4. Employer catalogue (four independent research lanes, 25 each)
Use data/research/selection.json assigned batch. Search official identities/links, request public pages with bounded traffic. Append evidence incrementally. Record name changes as alias/notes, do not invent. All 25 must be accounted for including blocked/unverified. Commit only assigned JSON files.
### 5. Common workflows and adapters (docs lane)
Canonical .agents/skills/korean-job-search/SKILL.md plus references (setup/search/apply/review/interview/privacy), AGENTS.md, CLAUDE.md, GEMINI.md, .claude/skills and .hermes/skills/workspace skill adapters. Tool/runtime-neutral directions using available equivalents; no fabricated slash commands or paid model defaults. Plain CLI calls must follow this contract; parent will reconcile final flags. docs/AGENTS_COMPATIBILITY.md with authoritative docs and explicit supported-vs-executed status. scripts/sync_skills.py idempotent/check mode; tests/test_adapters.py. Do not change global skill settings or author main README (parent owns).
### 6. CLI/registry integration (parent)
registry.py reads aggregate korean_job_search/data/companies.json and portals.json; validates exact coverage and duplicate IDs. cli.py/__main__.py exposes init/sources/discover/collect/ingest/rank/prepare/answers-check/track/report/doctor. discover generates honest agent search tasks with official-first queries and must not claim fetched results. collect batches public fetches with bounded source/job counts and per-source failures. tests/test_cli.py subprocess smoke, cross-platform filesystem tests.
### 7. Verification/review (independent reviewers then parent)
Run python3 -m unittest discover -s tests -v, compileall, skill adapter --check, clean install/package-data test, actual live CLI scrape and packet/report examples. Run all 100 source URL checks bounded/deduped, preserve source-level evidence and programmatic counts. Spec reviewer first; fix omissions; then security/quality reviewer; fix critical findings and regression-test. Do not claim 100 live parsers just because 100 links exist.
### 8. Documentation and handoff (parent)
README.md Korean, README.en.md English, SECURITY.md, UPSTREAM.md, artifacts/VERIFICATION.md. Provide verified invocation commands and limitations. git status/diff + commit. Do not push or create a public/private remote without subsequent explicit publishing request.
