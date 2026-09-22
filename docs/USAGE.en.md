# Usage guide

[Back to the project](../README.en.md) · [한국어](USAGE.md)

Run commands from the repository root. Follow only the stages you need.

## Quick start

Python 3.10+; no mandatory third-party runtime dependencies. Run from this repository:

```bash
python3 -m korean_job_search doctor
python3 -m korean_job_search init
python3 -m korean_job_search sources --company NAVER
python3 -m korean_job_search collect --source NAVER --source CJ제일제당 --source saramin --limit 5
```

Use `python` on Windows if applicable. `python -m pip install -e .` optionally installs the `kjs` command. Fill the blank private profile and story templates in `workspace/`; no fabricated candidate data is supplied.

Open your agent in the repository and ask it to read `AGENTS.md` and `.agents/skills/korean-job-search/SKILL.md`, then find roles based on your private profile. Ask for exact source URLs, current deadlines, factual claims only, and no application submission. [Compatibility notes](AGENTS_COMPATIBILITY.md) distinguish standard-format support, discovery checks and actual model-driven trials.

## Acquisition versus discovery plans

```bash
python3 -m korean_job_search collect --source wanted --source saramin --source jobkorea --query "서비스 기획"
python3 -m korean_job_search collect --all-companies --limit 5 --workers 3
python3 -m korean_job_search discover --query "AI product planning" --all-companies --out workspace/discovery.json
python3 -m korean_job_search ingest --url "https://official-employer.example/job"
python3 -m korean_job_search ingest --file workspace/jd.txt --source-url "https://official-employer.example/job"
```

`collect` makes real public requests, subject to robots/access controls. Inspect per-source status and diagnostics. Shared group portals are fetched once, not mislabeled as separate employer feeds. Existing job observations survive failed refreshes. A card-only refresh retains an acquired detail observation and its original timestamp; newer card metadata stays in flat `observations`, with a warning. Retained detail is not necessarily current: compare timestamps/deadlines and recheck the official detail on conflict.

`discover` only produces a structured search plan, clearly labeled `live_search_executed: false`. Your agent executes the tasks using its actual search/browser tools and imports the source text. Search snippets and listing cards are not full job descriptions. Login-required forms must be verified with authorized access or user-provided text; old questions are never substituted.

## Application workflow

```bash
python3 -m korean_job_search rank --jobs workspace/jobs.json --profile workspace/profile.json
python3 -m korean_job_search prepare --job JOB_ID --out workspace/applications/my-application
python3 -m korean_job_search answers-check workspace/answer.txt --limit 1000
python3 -m korean_job_search answers-check workspace/answer.txt --limit 2000 --mode cp949-bytes --newlines crlf
python3 -m korean_job_search track --job JOB_ID --status drafted
python3 -m korean_job_search report --jobs workspace/jobs.json --out workspace/report.html
```

Ranking is a transparent keyword heuristic, not a hiring probability or assessment. `prepare` writes a grounded work packet for an actual agent to draft and review; it does not pretend to generate a finished resume itself. Experience, employer names, dates, metrics and individual responsibility must remain faithful to evidence. The target site's counter is authoritative.

`track --status applied --confirm` records a submission you already made; it sends nothing to an employer. No auto-apply, messaging, consent clicks, account creation, background scheduling or credential capture is included.

## Privacy

Keep all candidate documents in ignored `workspace/` or `private/`. Never put them in tracked agent instructions. The CLI makes no LLM calls. If your chosen agent reads private data using a cloud model, that data may be transmitted to its provider: local files do not imply offline inference. Read [SECURITY.md](../SECURITY.md) before adding connectors or publishing a copy.

## Verification

```bash
python3 -m unittest discover -s tests -v
python3 scripts/sync_skills.py --check
python3 scripts/check_privacy.py
python3 -m korean_job_search sources --all-companies --verify --out workspace/source-health.json
```

On macOS, a symlinked system temporary directory can trip the path-safety tests. Use a real directory inside the repository rather than disabling the safety check:

```bash
mkdir -p workspace/test-tmp
TMPDIR="$PWD/workspace/test-tmp" python3 -m unittest discover -s tests -v
```

Tests are offline. Live results and limitations are reported separately in `artifacts/VERIFICATION.md`. Public-source adapters may break when sites change; all-100 link coverage is not all-100 parser coverage. PDF rendering is an optional future integration, not a claimed built-in feature.
