# 실제 검증 기록 / Verification

## 범위와 환경

2026-09-22 macOS 로컬 실행입니다. 오프라인 회귀 테스트, 실제 공개 사이트 조회, 설치형 wheel 실행을 구분합니다. 별도 에이전트 모델로 지원 업무를 수행하는 유료 평가 세션·지원서 실제 제출·전역 에이전트 설정 변경·GitHub 공개 업로드는 하지 않았습니다.

## 디렉터리와 출처

- 선정 목록과 회사별 이름/ID를 기계 대조한 100개 기업, 공식 채용 URL 71개(그룹 공용창구 중복 제거).
- 출처 근거 258개. 기업 기록 검증 상태 96 verified, 4 partial.
- 부분 검증 4건: 롯데칠성음료, CJ대한통운, 신세계인터내셔날, 우아한형제들. 홈페이지 인증서/리다이렉트/접근 제한 또는 본문 확인 한계를 각 record에 보존했습니다. 이것은 현재 채용공고가 없다는 뜻이 아닙니다.
- 초기 SK엔무브 후보는 공식 홈페이지에서 SK온 CIC 사업부로 확인되어 별도 법인 SKC로 교체했습니다. 정확히 100개를 유지하되 그룹/사업부 중복을 독립 기업처럼 세지 않았습니다.
- 이 목록은 채용 커버리지용 큐레이션이며 매출·자산·시가총액 100대 순위가 아닙니다.

`python3 -m korean_job_search sources --all-companies --verify --workers 4 --timeout 12 --out artifacts/source-health.json`

100개 대상에 대해 URL 71개를 실제 조회한 결과: ok 69개 대상, robots_denied 30개 대상, blocked 1개 대상(넥슨코리아 HTTP 403). 공용 URL 결과를 기업별로 연결한 수치입니다. URL 접근 성공은 해당 기업의 개별 공고를 파싱했다는 뜻이 아닙니다. robots.txt의 401/403/HTML 응답 등 정책을 확인할 수 없는 경우에도 fail-closed로 robots_denied를 반환하며 실제 Disallow 지시와 진단 메시지로 구분합니다.

## 통합 CLI 라이브 실행

실행 증거: `integration-live.json`. 원문/요청 추적의 더 넓은 수집기 검증: `live-collectors.json`.

| 출처 | 통합 실행 결과 | 의미 |
|---|---|---|
| NAVER | partial, 5개 | 공고 목록 일부; 전체 사이트/전체 JD 아님 |
| CJ 그룹 | partial, 5개 | 공고별 실제 고용 회사 보존 |
| 사람인 | partial, 5개 | 공개 공고 카드; 전체 JD 아님 |
| 잡코리아 | needs_browser, 0개 | HTTP 200 HTML에서 지원되는 공고 구조를 확인하지 못함 |
| 원티드 | robots_denied, 0개 | robots.txt HTTP 403, 정책 검증 실패로 직접 수집 중단 |
| 카카오 | robots_denied, 0개 | robots.txt HTTP 401, 정책 검증 실패로 직접 수집 중단 |
| 롯데 | robots_denied, 0개 | robots.txt가 plain-text 정책을 반환하지 않아 중단 |

15개 고유 목록 레코드를 저장했습니다. NAVER의 ‘광고 프로덕트 기획 경력 채용’ 공고를 URL ingest로 다시 읽어 원문 텍스트 9,869자를 확보했고, 같은 공고 ID에 병합되어 15개를 유지했습니다. source URL, 확인 시각, live_html 근거를 보존했습니다.

빈 개인 프로필로 실제 `ingest → rank → prepare → track drafted → report`를 실행했습니다. 지원 패킷의 job.json/profile.json/brief.md/answers.md/review.md 생성, 로컬 지원 기록 저장, HTML 보고서의 공고 article 15개·공식/원문 링크 15개·script 0개를 확인했습니다. 이는 도구 작동 검증이지 실제 사용자에게 맞춘 이력서 완성이나 합격가능성 검증이 아닙니다. 사용자 경력/연락처를 넣지 않았고 어떤 지원서도 제출하지 않았습니다.

## 오프라인/패키징

- `python3 -m unittest discover -s tests -q`: 237 tests, OK (Python 3.13.2 / 3.11.12 각각 실행).
- `python3 scripts/sync_skills.py --check`: canonical bundle/entrypoints 일치.
- `python3 scripts/build_catalog.py --check`: 100개, 근거 258개, 배포 파일 일치.
- Python 3.13.2에서 wheel build와 소스 트리 밖 별도 경로 설치 후 `sources --all-companies` 100개 및 `init` 빈 프로필/서식 생성을 실행했습니다. 별도 설치의 모델 호출/전역 설정 수정은 없습니다. 최종 wheel을 소스 트리 밖 별도 경로에 다시 설치하고 그 경로의 패키지가 로드되는 것을 확인한 뒤 전체 테스트 237개도 통과했습니다.
- 지원 기록 CLI의 이중 잠금, 2,000자 제한 인자, 수동 JD의 회사 미확인 상태, 새 개인 출력 경로의 0700 디렉터리/0600 파일 권한을 회귀 검증했습니다.
- Python 3.10/Windows 실제 실행과 원격 GitHub Actions는 수행하지 않았습니다. 설정 파일 존재를 CI 통과로 보고하지 않습니다.

## 에이전트 호환성

공통 Agent Skill과 Hermes/Claude Code/OpenClaw 복제본, Codex/Prime의 `.agents` 경로, AGENTS/CLAUDE/GEMINI 안내 파일을 포함합니다. 공식 문서와 설치된 버전/도움말, 복제본 일치 및 명령 인자 테스트를 확인했습니다. 모든 에이전트에서 유료 모델 세션을 실행하거나 실제 native discovery를 검증한 것은 아닙니다. Hermes는 프로젝트 신뢰, OpenClaw는 올바른 workspace 선택이 필요합니다. 구체적인 버전/호출 차이는 `../docs/AGENTS_COMPATIBILITY.md`를 확인하세요.

## 남은 경계

로그인·CAPTCHA·차단을 우회하지 않습니다. 원티드/잡코리아 등 직접 수집이 안 되는 경로는 에이전트의 실제 검색/허용된 공개 브라우저 또는 사용자가 제공한 원문으로 보완합니다. 수집 수를 채우려고 가짜 공고를 생성하지 않습니다. 지원서 작성에는 사용자 경력 근거와 현재 지원 양식/문항 확인이 추가로 필요합니다. 독립 통합 리뷰의 판정은 별도 review artifact로 보존합니다.
