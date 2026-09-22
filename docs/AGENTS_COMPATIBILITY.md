# 에이전트 호환성과 검증 범위

## 하나의 원본, 런타임별 얇은 어댑터

수정할 워크플로우 원본은 `.agents/skills/korean-job-search/` 하나다. `SKILL.md`, 참조 문서, 빈 양식을 함께 제공한다. 에이전트별 별도 프롬프트나 도구 호출 구현은 없다.

| 대상 | 이 저장소의 진입점 | 사용 조건 |
|---|---|---|
| Codex | `.agents/skills/korean-job-search/SKILL.md` | 저장소 안에서 실행한다. CWD부터 Git 루트까지 `.agents/skills`를 탐색한다. |
| Prime Agent | 동일한 `.agents/skills/korean-job-search/SKILL.md` | 공통 경로 탐색 또는 명시적 `--skill`을 사용한다. |
| Claude Code | `.claude/skills/korean-job-search/`의 생성 사본 | 프로젝트 스킬 경로다. `CLAUDE.md`는 `AGENTS.md`와 원본으로 연결한다. |
| Hermes | `.hermes/skills/korean-job-search/`의 생성 사본 | 해당 프로젝트에 대한 사용자의 명시적 신뢰가 필요하다. `.agents/skills`도 지원하지만 네이티브 경로가 우선이다. |
| OpenClaw | `skills/korean-job-search/`의 생성 사본 | 이 저장소가 **해당 에이전트의 구성된 workspace**여야 한다. 셸 CWD만 바꾸는 것으로 충분하지 않다. |
| 일반 AGENTS/Gemini | 루트 `AGENTS.md`, `GEMINI.md` | 공통 문서를 읽는 포인터다. Gemini 고유 스킬 자동 탐색 검증을 뜻하지 않는다. |

사본은 심볼릭 링크가 아닌 **자체 포함된 일반 파일**이다. 상대 Markdown 링크는 사본 안에서 끝나며, CLI 명령은 저장소 루트 기준이다. Windows에서 링크 생성 권한이 없어도 어댑터를 사용할 수 있다. `python3`가 없는 Windows에서는 Python 3.10+인지 확인한 `py -3` 또는 `python`으로 명령의 실행 파일만 바꾼다. 네이티브 Windows 실행 검증은 별도다.

```text
python3 scripts/sync_skills.py
python3 scripts/sync_skills.py --check
python3 -m unittest discover -s tests -p test_adapters.py -v
```

`sync_skills.py`는 Python 표준 라이브러리만 사용한다. 원본 파일과 루트 포인터를 생성하고 `--check`는 누락·내용 차이·예상 밖 파일을 검사한다. 알 수 없는 파일은 자동 삭제하지 않는다. 원본/대상 경로의 심볼릭 링크는 거부한다. 스크립트 위치로 저장소를 찾아 현재 작업 디렉터리에 의존하지 않는다. 전역 설치, 홈 설정 변경, 훅, 자동 시작, 네트워크 호출, 모델 호출은 없다.

생성 폴더와 `AGENTS.md`/`CLAUDE.md`/`GEMINI.md`를 직접 수정하지 않는다. 루트 포인터를 바꿀 때는 `scripts/sync_skills.py`의 `POINTERS`를 고친다. 오래된 참조 파일을 삭제한 경우 사본에 남은 파일을 사람이 검토해 제거한 뒤 다시 동기화한다. 실제 개인정보는 원본이나 사본이 아닌 Git에서 제외된 `workspace/`에만 둔다.

## 공식 문서로 확인한 탐색 규칙

아래는 2026-09-22에 확인한 **문서·버전 소스 계약**이다. 미래의 모든 버전에서 같다는 보장이 아니다.

### Hermes

- 공식 문서의 프로젝트 경로는 `.hermes/skills/`와 `.agents/skills/`다. 최근접 `.git` 프로젝트 루트를 사용하며 worktree의 `.git` 파일도 포함한다.
- 임의로 clone한 저장소의 스킬을 자동 신뢰하지 않는다. 사용자가 검토 후 직접 `hermes skills trust .`를 실행할 수 있다. 취소는 `hermes skills untrust .`다. **이 작업에서는 신뢰 설정을 바꾸지 않았다.**
- 신뢰는 해석된 프로젝트 루트 경로의 정확한 일치로 저장된다. 상위 폴더를 신뢰했다는 이유로 모든 하위 저장소가 신뢰되는 것은 아니다.
- `skills.project_discovery: false`이면 프로젝트 탐색이 비활성화된다. 위험 검사로 격리된 스킬은 신뢰된 프로젝트에서도 사용할 수 없다.
- 비대화형 작업은 기존 신뢰 상태만 사용하며 자동 승인하지 않는다. 같은 이름이 발견되면 프로젝트의 `.hermes/skills`가 `.agents/skills`보다 먼저다.
- 실제 발견 여부는 해당 저장소에서 `hermes skills list`로 확인하고 `korean-job-search`의 경로·가용성을 검증한다. 명시 호출은 `/korean-job-search` 또는 CLI `hermes --skills korean-job-search`다. 호출은 모델 사용을 동반할 수 있다.

근거:
- [공식 Skills / Project-local skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills#project-local-skills): “Hermes does not auto-load them from arbitrary cloned repos.”
- [확인한 upstream 리비전의 루트·신뢰 구현](https://raw.githubusercontent.com/NousResearch/hermes-agent/524041b9/agent/skill_utils.py)
- [스킬 탐색 구현](https://raw.githubusercontent.com/NousResearch/hermes-agent/main/tools/skills_tool.py)

### Prime Agent

- 공식 `v0.9.5` 문서는 프로젝트 `.prime/agent/skills/`와 CWD부터 Git 루트까지 `.agents/skills/` 탐색을 명시한다. Git 밖에서는 파일시스템 루트까지 올라간다.
- `.agents/skills`에서는 `SKILL.md`가 들어 있는 폴더를 사용한다. 임의의 최상위 Markdown 파일을 같은 방식으로 탐색한다고 가정하지 않는다.
- 명시적 파일 로드: `prime-agent --skill .agents/skills/korean-job-search/SKILL.md`.
- 대화 명령은 **`/skill:korean-job-search`**다. Claude식 `/korean-job-search`를 Prime 명령이라고 안내하지 않는다.
- `--skill`은 반복할 수 있고 `--no-skills`와 함께 있어도 명시적으로 지정한 파일을 로드한다. 변경된 메타데이터는 `/reload`로 다시 탐색한다. 프로젝트 신뢰 게이트는 Hermes의 규칙을 옮겨 적지 않는다.

근거:
- [현재 공식 skills.md](https://raw.githubusercontent.com/PrimeIntellect-ai/prime-agent/main/packages/coding-agent/docs/skills.md)
- [설치 버전과 맞춘 v0.9.5 문서](https://raw.githubusercontent.com/PrimeIntellect-ai/prime-agent/v0.9.5/packages/coding-agent/docs/skills.md): “Skills register as `/skill:name` commands”; “`--skill <path>` (repeatable, additive even with `--no-skills`)”.

### Codex

- 공식 문서에 따라 `.agents/skills/<skill-name>/SKILL.md`를 사용한다. 현재 디렉터리부터 저장소 루트까지 각 디렉터리의 `.agents/skills`를 탐색한다.
- 대화에서 `$korean-job-search`로 명시하거나 `/skills`에서 선택한다. 같은 이름이 여럿이면 병합되지 않으므로 발견된 경로를 확인한다.
- `.codex/config.toml`에 대한 프로젝트 신뢰와 스킬 탐색을 동일한 게이트로 설명하지 않는다. 확인한 `0.134.0` 로더는 비활성화된 프로젝트 설정 계층도 스킬 루트 계산에 포함한다.
- 이 저장소는 전역 `$HOME/.agents/skills` 또는 예전 `$CODEX_HOME/skills`에 설치하지 않는다.

근거:
- [공식 Codex skills](https://developers.openai.com/codex/skills): “Codex scans `.agents/skills` in every directory from your current working directory up to the repository root.”
- [0.134.0 스킬 로더](https://raw.githubusercontent.com/openai/codex/rust-v0.134.0/codex-rs/core-skills/src/loader.rs)
- [같은 버전의 untrusted 프로젝트 테스트](https://raw.githubusercontent.com/openai/codex/rust-v0.134.0/codex-rs/core-skills/src/loader_tests.rs)

### Claude Code

- 공식 프로젝트 경로 `.claude/skills/<name>/SKILL.md`에 생성 사본을 둔다. `/korean-job-search`로 호출할 수 있다. `CLAUDE.md` 포인터만으로 스킬 등록을 입증하지 않는다.
- 첫 대화형 실행에는 코드베이스 신뢰 확인이 있다. `-p`는 이 신뢰 대화상자를 건너뛰고 모델 호출을 수행할 수 있으므로 단순 탐색 검사로 실행하지 않는다.
- 현재 문서는 설치 버전보다 새 기능을 포함할 수 있다. 예를 들어 일반 skills 디렉터리를 대상으로 하는 `claude plugin validate`는 문서상 `2.1.233+` 요구사항이 있어 설치된 `2.1.81`에서 검증했다고 주장하지 않는다.
- `allowed-tools`, `context: fork`, 전용 tool 함수 등 Claude 전용 필드는 원본에 넣지 않는다.

근거:
- [공식 Claude Code skills](https://code.claude.com/docs/en/skills)
- [공식 보안 / trust verification](https://code.claude.com/docs/en/security): “Trust verification is disabled when running non-interactively with the `-p` flag.”

### OpenClaw: 설치된 2026.2.1을 기준으로 한다

- `2026.2.1`의 경로는 `<workspace>/skills`, 사용자 관리 스킬, bundled 스킬이다. 별도 설정의 추가 경로도 있지만 이 저장소가 설정을 수정하지 않는다. workspace 사본이 사용자/번들 사본보다 우선한다.
- 최신 [공식 Skills 문서](https://docs.openclaw.ai/tools/skills)는 `.agents/skills`도 설명한다. **그 최신 설명을 설치된 2026.2.1의 지원 증거로 사용하지 않는다.** 이 버전에는 `skills/korean-job-search/` 사본을 사용한다.
- 저장소를 OpenClaw 에이전트의 workspace로 지정하는 일은 사용자의 별도 선택이다. 기존 workspace를 자동으로 교체하거나 개인 설정 파일을 수정하지 않는다. 그 선택이 없으면 이 저장소의 사본은 자동 발견되지 않을 수 있다.
- 설치된 CLI의 읽기 전용 점검 명령은 `openclaw skills list --json`, `openclaw skills info korean-job-search --json`, `openclaw skills check --json`이다. 결과의 실제 경로와 준비 상태를 확인한다. 명령 help 확인만으로 이 스킬을 발견했다고 말하지 않는다.
- 호출은 `/skill korean-job-search`다. 자동 생성 명령은 기호가 `_`로 정리되어 `/korean_job_search`가 될 수 있으므로 Claude식 명령을 그대로 가정하지 않는다.
- frontmatter는 한 줄 키와 간단한 스칼라로 유지한다. 설정 활성화/실행환경 요구 조건을 확인하고 Hermes의 trust 명령을 적용하지 않는다.

버전 일치 근거:
- [2026.2.1 Skills 문서](https://raw.githubusercontent.com/openclaw/openclaw/v2026.2.1/docs/tools/skills.md)
- [2026.2.1 workspace 스킬 로더](https://raw.githubusercontent.com/openclaw/openclaw/v2026.2.1/src/agents/skills/workspace.ts)
- [2026.2.1 slash commands](https://raw.githubusercontent.com/openclaw/openclaw/v2026.2.1/docs/tools/slash-commands.md)
- [2026.2.1 workspace 의미](https://raw.githubusercontent.com/openclaw/openclaw/v2026.2.1/docs/concepts/agent-workspace.md)

## 검증 기록과 과장하지 않을 범위

다음의 검증 단계는 서로 다르다.

| 단계 | 이 작업의 확인 범위 | 말할 수 없는 것 |
|---|---|---|
| 공식 문서·소스 | 위 경로/명령/버전별 조건 확인 | 모든 과거·미래 버전 호환 |
| 로컬 CLI 확인 | `--version`, `--help` 등 메타데이터 조회 | 이 프로젝트 스킬의 자동 발견 |
| 오프라인 어댑터 검사 | `test_adapters.py`: 동일 내용·상대 링크·진입점·명령 계약·개인정보 금지·드리프트·멱등성·경로 안전성 | 에이전트의 자동 스킬 선택 정확도 |
| 프로젝트별 런타임 발견 | **미실행**. Hermes trust/OpenClaw workspace를 변경하지 않음 | 다섯 에이전트 모두 설치·활성화 완료 |
| 실제 모델 실행 | **미실행**. 유료 호출/사용자 계정 사용/자동 제출 시험을 승인받지 않음 | 다섯 모델에서 지원서 작업 성공, 품질 점수, 토큰 절감 |

오프라인 실제 실행: Python `3.13.2`에서 `python3 -m unittest discover -s tests -v` **20개 통과**. `python3 scripts/sync_skills.py --check`와 `compileall`도 통과했다. 테스트는 한글·공백이 있는 임시 경로, CRLF 진입점, 다른 CWD에서 실행, 사본 드리프트, 심볼릭 링크 거부와 알 수 없는 파일 보존을 포함한다. 네이티브 Windows나 다섯 런타임의 모델 실행 결과는 아니다.

조회한 설치 버전: Hermes `v0.21.4 (2026.9.21)` / upstream `524041b9`(로컬 패치 있음), Prime Agent `0.9.5`, Codex `0.134.0`, Claude Code `2.1.81`, OpenClaw `2026.2.1` / `ed4529e`. 해당 바이너리의 버전·help만 조회했으며 로그인·설정·모델 호출은 하지 않았다. Python CLI 통합/수집 테스트는 저장소 전체 테스트와 별도 수집 증거를 확인한다. 이 문서의 어댑터 검사는 네트워크 수집 성공을 입증하지 않는다.

실제 발견을 나중에 검사할 때는 사용자 승인 아래 저장소/격리 workspace를 지정하고, 스킬 이름뿐 아니라 **정확한 발견 경로**를 기록한다. 실제 모델 평가가 필요하면 모델·제공자·횟수·개인정보·비용 범위를 먼저 정하고 작업 출력과 도구 추적을 남긴다. 명시적 스킬 로드 한 번의 성공을 자동 선택 성공률로 발표하지 않는다.
