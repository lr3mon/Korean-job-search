# Korean-job-search

한국 채용공고를 찾고, 실제 경력 근거를 연결하고, 문항별 지원서를 검토하는 에이전트 공통 작업실입니다.

[English](README.en.md) · [에이전트 연결](docs/AGENTS_COMPATIBILITY.md) · [보안](SECURITY.md) · [원작/라이선스](UPSTREAM.md)

> MadsLorentzen/ai-job-search의 프로필 → 공고 평가 → 작성/검토 → 지원 기록/면접 흐름을 한국 시장에 맞게 재구성했습니다. 자동 대량 지원 봇이 아니며 제출 버튼을 누르지 않습니다.

## 무엇이 다른가요?

- 공식 채용 사이트 우선: 사람인·잡코리아·원티드는 발견 경로로, 기업 원문은 자격요건/마감의 최종 근거로 사용합니다.
- 국내 주요 기업 100개 디렉터리: 회사별 공식 채용 URL, 관계 근거, 확인 시각, 접근 한계를 기록합니다. 삼성·SK·LG·현대·한화·롯데·CJ 등 계열사와 금융·게임·플랫폼 기업을 포함합니다.
- 한국어 문항 중심: 이력서/경력기술서/경험 은행, 지원동기·역량·협업·실패·AI 활용 문항, 개인 기여와 회사 성과 분리, 행동 이후의 배움을 다룹니다.
- 실제 글자 수 검증: 공백 포함/제외, LF/CRLF, Unicode 문자·UTF-16·UTF-8/CP949 바이트를 구분합니다. 사이트가 표시하는 카운터가 최종 기준입니다.
- 에이전트 공통: Hermes, Prime Agent, Codex, Claude Code, OpenClaw와 AGENTS.md를 읽는 도구가 같은 워크플로를 참조합니다.
- 개인정보 분리: 프로필은 무시되는 `workspace/`에 저장합니다. 추적되는 AGENTS.md/스킬에 이름·연락처·이력을 쓰지 않습니다.
- 기본 실행은 Python 3.10+ 표준 라이브러리만 사용합니다. Bun·LaTeX·특정 LLM 구독을 필수로 설치하지 않습니다.

### ‘100대 기업’의 정확한 의미

이 버전의 100개는 **채용 커버리지를 위한 주요 기업 큐레이션**입니다. 매출·자산·시가총액 순위가 아닙니다. 선정 목록은 `data/research/selection.json`, 회사별 근거는 `data/research/companies-*.json`에 있습니다. 같은 그룹 채용창구를 여러 계열사가 공유할 수 있습니다.

100개 공식 채용 URL을 안다고 100개 사이트의 공고를 모두 자동으로 읽을 수 있는 것은 아닙니다. 각 출처의 접근/파싱 결과를 따로 보고하며, 차단·JS 렌더링·로그인 요구를 ‘공고 없음’으로 바꾸지 않습니다.

### 실제로 어디까지 동작하나요?

통합 실행에서 NAVER·CJ·사람인의 공고 목록 15개를 수집하고, NAVER 광고 프로덕트 기획 JD를 실제로 읽어 지원 작업 패킷과 로컬 보고서까지 생성했습니다. 잡코리아는 브라우저 확인이 필요했고, 원티드·카카오·롯데 직접 수집은 robots 정책 확인 실패로 중단했습니다. **모든 사이트의 완전 자동 수집을 주장하지 않습니다.** [실행 증거와 한계](artifacts/VERIFICATION.md)를 먼저 확인하세요.

## 빠른 시작

저장소 루트에서 실행하세요. 전역 설치 없이 사용할 수 있습니다.

```bash
python3 -m korean_job_search doctor
python3 -m korean_job_search init
python3 -m korean_job_search sources --company NAVER
python3 -m korean_job_search collect --source NAVER --source CJ제일제당 --source saramin --limit 5 --out workspace/jobs.json
```

Windows에서는 `python3` 대신 `python`을 사용하셔도 됩니다. 선택적으로 `python -m pip install -e .` 하면 같은 명령을 `kjs`로 실행할 수 있습니다.

`init` 후 `workspace/profile.json`과 경력/경험 서식을 채우세요. 최초 상태는 빈 서식이며, 데모 성과나 사용자의 경력을 임의로 생성하지 않습니다.

### 에이전트에게 맡기기

이 저장소를 작업 폴더로 열고 공통 스킬을 읽힌 뒤 다음처럼 요청하세요.

```text
korean-job-search 스킬을 사용해주세요.
workspace의 제 경력 자료를 확인하고 AI 서비스 기획/사업개발 공고를 찾아주세요.
주요 기업 공식 채용 페이지를 먼저 보고 사람인·잡코리아·원티드로 보완해주세요.
실제 JD와 마감 시각을 확인한 후보만 제안하고, 근거 없는 경험은 만들지 마세요.
문항과 글자 수를 확인한 뒤 지원서를 작성하고 독립 검토해주세요. 제출은 하지 마세요.
```

- Codex / Prime Agent: `.agents/skills/korean-job-search/`를 참조합니다.
- Claude Code: `.claude/skills/korean-job-search/` 연결을 사용합니다.
- Hermes: 프로젝트 로컬 스킬의 신뢰 절차를 먼저 확인하세요. 자동 발견을 지원하지 않는 버전에서는 AGENTS.md와 공통 스킬을 명시적으로 읽히는 경로를 사용합니다.
- OpenClaw: 이 저장소를 실행 workspace로 선택하고 `skills/korean-job-search/`를 참조합니다. 기존 gateway 전역 설정을 자동으로 덮어쓰지 않습니다.

설정/호출 방식과 버전별 차이는 [호환성 문서](docs/AGENTS_COMPATIBILITY.md)에 있습니다. 형식 호환, 파일 발견, 실제 모델로 수행한 결과는 서로 다른 검증입니다. 이 레포가 모든 런타임의 인증이나 검색 API를 제공하는 것은 아닙니다.

## 검색은 두 경로로 동작합니다

### 1. 공개 페이지 직접 수집

```bash
python3 -m korean_job_search collect --source wanted --source saramin --source jobkorea --query "서비스 기획" --limit 10
python3 -m korean_job_search collect --company LG --limit 10
python3 -m korean_job_search collect --all-companies --workers 3 --limit 5
```

출처별 `status`, `diagnostics`, `job_count`를 확인하세요. `ok/partial/empty`와 `blocked/robots_denied/needs_browser/needs_credentials/unsupported/error`를 구분합니다. 공개 조회가 거부되면 우회하지 않습니다. 일부 출처만 성공해도 성공/실패 내역을 모두 보존합니다. 동일 그룹 창구는 중복 요청하지 않으며, 공고별 실제 고용 법인을 확인해야 합니다.

`jobs.json`은 기존 기록을 보존하며 새 관측을 병합합니다. 목록 카드만 다시 읽으면 이미 확보한 JD와 원래 확인 시각을 보존하고, 최신 카드의 변경 내용은 `observations`에 따로 남깁니다. 보존된 JD가 현재도 유효하다는 뜻은 아닙니다. `warnings`, 각 관측의 시각·마감 원문을 비교하고 충돌하면 공식 상세를 다시 확인하세요. 실패한 사이트의 이전 기록도 남으며, 목록 카드만 확보했으면 전체 JD를 읽은 것으로 취급하지 않습니다.

### 2. 에이전트의 검색/브라우저를 통한 보완

```bash
python3 -m korean_job_search discover --query "AI 서비스 기획 신입 경력" --all-companies --out workspace/discovery.json
```

`discover`는 **검색 계획만** 만듭니다. 결과를 꾸며서 반환하지 않습니다. 에이전트는 각 task를 실제 웹 검색/공개 브라우저로 수행하고, 찾은 원문을 다음처럼 저장합니다.

```bash
python3 -m korean_job_search ingest --url "공식_채용공고_URL"
python3 -m korean_job_search ingest --file workspace/jd.txt --source-url "원문_URL" --company "회사명"
```

로그인 뒤의 문항은 사용자가 직접 제공하거나 권한 있는 세션에서 확인해야 합니다. 과거 채용 문항을 현재 문항으로 대체하지 않습니다.

## 지원서 작성 흐름

```bash
python3 -m korean_job_search rank --jobs workspace/jobs.json --profile workspace/profile.json
python3 -m korean_job_search prepare --job "공고_ID" --jobs workspace/jobs.json --profile workspace/profile.json --out workspace/applications/지원건
python3 -m korean_job_search answers-check workspace/answer.txt --limit 1000
python3 -m korean_job_search answers-check workspace/answer.txt --limit 2000 --mode cp949-bytes --newlines crlf
python3 -m korean_job_search report --jobs workspace/jobs.json --out workspace/report.html
```

`rank`는 투명한 키워드 기반 정렬 보조 도구이며 합격 확률이나 심리/조직 적합성 진단이 아닙니다. 실제 경력, 필수 자격, 회사 조사와 함께 판단하세요.

`prepare`는 원문·프로필 근거·작성/검토 지침을 묶은 작업 패킷을 생성합니다. LLM을 흉내 내어 완성 이력서를 출력하지 않습니다. 에이전트가 실제 문항을 읽고 작성한 다음, 다른 검토자가 사실/개인 기여/질문 대응/중복/글자 수를 확인하는 구조입니다.

지원 관리도 로컬 기록입니다.

```bash
python3 -m korean_job_search track --job "공고_ID" --status drafted
python3 -m korean_job_search track --job "공고_ID" --status applied --confirm
python3 -m korean_job_search track
```

`--confirm`은 이미 직접 제출한 사실의 기록을 확인할 뿐, 채용 사이트에 무엇도 전송하지 않습니다.

## 검증과 유지보수

```bash
python3 -m unittest discover -s tests -v
python3 scripts/sync_skills.py --check
python3 scripts/check_privacy.py
python3 -m korean_job_search sources --all-companies --verify --workers 3 --out workspace/source-health.json
```

오프라인 테스트는 실제 채용 사이트를 호출하지 않습니다. 실수집 결과와 한계는 `artifacts/VERIFICATION.md`에 별도로 기록합니다. 사이트 구조가 바뀌면 해당 파서와 공개 fixture를 함께 갱신해야 합니다.

### 저장소 구조

```text
.agents/skills/korean-job-search/  공통 한국 채용 작업 지침
korean_job_search/                표준 라이브러리 CLI와 수집/검증 모듈
korean_job_search/data/           배포되는 기업·포털 디렉터리와 빈 서식
data/research/                   선정 기준과 회사별 출처 근거
workspace/                       개인 자료/공고/지원 기록 (Git 제외)
scripts/                         스킬 동기화·공개 전 개인정보 경계 점검
tests/                           네트워크 없는 회귀 테스트
artifacts/                       개인 정보 없는 검증 기록
```

현재 범위: 공개 채용공고 읽기, 근거 기반 작업 지침, 글자 수 검사와 로컬 기록. 포함하지 않는 것: 지원 자동 제출, 상시 실행 크론 설치, 모든 사이트의 완전 수집 보증, 인증 우회, 합격률 보증, 자동 PDF 렌더링.

MIT 라이선스. 원작 고지와 변경 범위는 [UPSTREAM.md](UPSTREAM.md)를 확인하세요.
