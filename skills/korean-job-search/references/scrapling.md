# Scrapling으로 공개 공고 읽기

기본 수집기에서 끝내지 말고 **응답 확인 → 파서 보완 → 필요한 경우 브라우저 렌더링 → 선택한 JD 읽기 → 검증 후 가져오기** 순서로 진행한다. Scrapling은 검색·읽기 보조 도구이며 자동 지원 도구가 아니다.

## 언제 적용하는가

- `needs_browser`인데 HTML에 공고 링크가 이미 있으면, 브라우저 대신 Scrapling의 선택자 파서부터 쓴다.
- 메뉴·빈 앱 셸만 내려오고 허용된 페이지의 실제 본문이 JavaScript로 만들어질 때 일반 브라우저 모드를 쓴다.
- 잡코리아 상세요강이 iframe에 있으면 실제 DOM에 나타난 `src`를 별도로 읽는다.
- 호스트 브라우저에서 허용된 범위로 확보한 HTML은 오프라인으로 다시 파싱할 수 있다.
- 원티드의 `/robots.txt` 403과 공개 `/wdlist`, `/wd/<id>` 접근 성공은 서로 다른 관측이다. robots 실패를 명시적인 `Disallow: /`로 설명하지 않는다. 반대로 읽을 수 있다는 이유로 자동 수집이 허용됐다고 판단하지도 않는다.

범위를 한 페이지·선택한 공고로 제한한다. 프록시 회전, CAPTCHA 해결, 로그인·기존 사용자 세션 가져오기, 채용 지원 API, 무제한 스크롤·순회는 이 절차에 없다. 사이트의 이용·재배포 조건을 확인하고 개인정보와 공고 원문은 `workspace/` 밖에 저장하지 않는다.

## 두 가지 실행 경로

1. **Scrapling MCP가 실제 연결된 호스트:** 현재 노출된 도구 스키마를 확인해 아래 MCP 절차에 대응한다. 특정 에이전트에 MCP가 자동 설치돼 있다고 가정하지 않는다.
2. **터미널이 있는 호스트:** 번들 [캡처 스크립트](../scripts/scrapling_capture.py)를 실행한다. Python 3.10+에서 Scrapling을 선택적으로 설치한다. Codex·Prime Agent·Claude Code·Hermes·OpenClaw 모두 같은 코드를 사용할 수 있다.

기본 `python3 -m korean_job_search collect`의 동작과 의존성은 바꾸지 않는다. 스킬이 필요할 때 이 보조 도구를 명시적으로 선택한다. 대형 브라우저 패키지나 모델 호출을 모든 실행에 강제하지 않는다.

## 선택적 격리 설치

이미 사용 가능한 Scrapling 환경이 있으면 그 Python 실행 파일을 사용한다. 없으면 사용자의 프로젝트 작업 범위 안에서 아래처럼 **새 환경**을 준비한다. 기존 환경을 덮어쓰지 않는다.

```bash
python3 -m venv workspace/tools/scrapling
workspace/tools/scrapling/bin/python -m pip install -e '.[scrapling]'
```

이 extra는 검증 대상으로 고정한 `scrapling[fetchers]==0.4.11`을 설치한다. 일반 HTTP + HTML 파싱에는 브라우저 설치가 필요 없다. 브라우저 모드가 필요하고 실행 파일이 없다면 아래 공식 설치 명령으로 브라우저 의존성을 준비한다. 다운로드와 디스크 사용이 발생하며 전역 에이전트 설정은 건드리지 않는다.

```bash
workspace/tools/scrapling/bin/scrapling install
```

Windows에서는 환경 실행 파일이 `workspace/tools/scrapling/Scripts/python.exe`와 `Scripts/scrapling.exe`다. 아래 Bash 변수 문법은 셸에 맞게 바꾸거나 실제 경로를 직접 쓴다. Windows 런타임 실측을 주장하지 않는다.

```bash
PY=workspace/tools/scrapling/bin/python
CAPTURE=.agents/skills/korean-job-search/scripts/scrapling_capture.py
"$PY" "$CAPTURE" --help
```

현재 폴더는 항상 저장소 루트다. 사본의 스크립트를 쓰는 경우 `CAPTURE`만 현재 번들 안의 실제 파일로 바꾼다.

## 1. HTTP에 이미 있는 목록부터

```bash
"$PY" "$CAPTURE" --url 'https://www.jobkorea.co.kr/Search/?stext=서비스%20기획' --engine http --limit 20 --out workspace/sources/jobkorea-list-http
```

실제 키워드와 범위를 사용한다. 출력 폴더는 새 이름이어야 하며 재실행 때 덮어쓰지 않는다. 결과:

- `source.html`: 이번에 획득한 공개 HTML.
- `snapshot.json`: 방식, 출처, 시각, HTTP/robots 확인 상태, 후보 링크와 진단.
- `links`: URL·표시된 문구·이미지 대체 텍스트. 회사명·직무·적합성을 임의 확정하지 않는다.
- `unique_links_found` / `returned` / `truncated`: 중복 제거 후 발견 수, 실제 반환 수, 상한 적용 여부. 사이트 전체 공고 수가 아니다.

공고 ID로 중복을 제거하므로 제목·회사·로고 링크나 추적 파라미터가 다른 같은 공고를 여러 개로 세지 않는다. 광고·일반 공고는 아직 분리하지 않으므로 후속 에이전트가 관련성과 실제 채용 회사를 검토한다. 이 JSON은 `jobs.json` 스키마가 아니다. 바로 `rank`에 넣지 말고 선택한 상세 원문을 확보한다.

## 2. 허용된 동적 페이지는 브라우저로

```bash
"$PY" "$CAPTURE" --url 'https://www.jobkorea.co.kr/Search/?stext=서비스%20기획&tabType=recruit' --engine browser --limit 20 --out workspace/sources/jobkorea-list-browser
```

먼저 기존 `SafeFetcher`로 정확한 URL의 robots·HTTP 검사를 한다. 통과한 경우만 새로운 익명 Scrapling `DynamicFetcher`를 한 번 실행한다. 로그인 프로필·쿠키·프록시·CAPTCHA 해결 기능은 사용하지 않는다. 브라우저 네트워크는 기본 수집기의 IP 고정 전송과 같지 않으므로 이 스크립트의 허용된 공개 포털 경로에서만 사용한다.

robots 정책 확인 실패, HTTP 차단, 로그인 요구가 있으면 자동 브라우저 전환하지 않는다. `redirect_review`는 렌더링 중 바뀐 정확한 대상 URL의 확인이 필요하다는 뜻이다. 오류에서 무한 재시도하지 말고 진단을 읽는다.

## 3. 실제 JD만 추출

목록에서 선택한 상세 공고를 읽고 DOM을 확인한다. 잡코리아의 `iframe[title="상세 모집 요강"]`가 가리키는 `src`를 `jd_iframe_urls`로 기록한다. 경로·번호를 추측하지 않는다. 부모 페이지의 추천공고나 과거 합격자소서를 현재 JD·문항으로 사용하지 않는다.

```bash
"$PY" "$CAPTURE" --url "$OBSERVED_IFRAME_URL" --kind jd --selector body --out workspace/sources/selected-job-jd
```

`OBSERVED_IFRAME_URL`은 실제 페이지에서 확인한 전체 HTTPS URL이다. `body`는 잡코리아 JD 전용 iframe에서만 사용한다. 일반 상세 페이지는 실제로 확인한 현재 JD 컨테이너 선택자를 명시한다. 원티드의 `상세 정보 더 보기`가 닫혀 있거나 이미지 공고가 있으면 누락을 기록한다. 선택자가 맞지 않으면 `selector_empty`, 부모 JD iframe이 남아 있으면 `needs_iframe`으로 멈춘다.

성공 시 `jd.txt`를 실제로 읽고 회사·직무·필수/우대·마감·빠진 이미지/첨부를 검토한다. `partial`은 텍스트 확보 상태일 뿐 현재 전체 JD·지원 자격·지원서 문항 검증 완료가 아니다.

```text
python3 -m korean_job_search ingest --file workspace/sources/selected-job-jd/jd.txt --source-url "https://확인한-공고-원문" --company "확인한 채용회사" --title "확인한 공고 제목"
```

URL은 실제 확인한 출처로 바꾼다. 기존 `ingest --file`의 사용자 제공 원문 표시를 유지한다. `snapshot.json`의 실제 획득 방식·시각·iframe 관계도 함께 보존하고, 입력된 공고의 회사명·제목 등 미확인 필드는 별도로 확인한다. 기존 JD를 새 카드로 덮어쓰지 않는다.

## MCP 도구로 실행할 때

- 도구 이름은 현재 호스트에 대응한다. 일반 HTTP `get`, 브라우저 `fetch`, 세션 도구가 실제 제공되는지 확인한다.
- HTTP는 TLS 확인과 제한된 timeout을 사용한다. `retries`는 최소 1로 둔다. 이 클라이언트에서 0은 “재시도만 없음”이 아니라 요청 자체를 생략할 수 있다.
- 일반 브라우저는 `headless: true`, `google_search: false`, `network_idle: false`, `timeout: 25000`, `wait: 1500`을 출발점으로 삼는다. 필요한 도구의 스키마에 실제 있는 옵션만 보낸다.
- 목록에서 `a[href*='/Recruit/GI_Read/']` 또는 `a[href^='/wd/']`를 검사한다. `extraction_type: html`, `main_content_only: false`로 읽으면 문구와 링크의 대응을 보존하기 쉽다. selector는 실제 DOM과 대조한다.
- 세션을 열었다면 작업 후 해당 세션만 닫고 목록에서 제거됐는지 확인한다. 기존 업무용 브라우저·사용자 프로필을 가져오지 않는다.
- 도구 응답의 실제 HTML 조각만 합쳐 UTF-8 파일로 저장한다. JSON 래퍼, 설명문, 만들어낸 샘플 HTML을 실수집 파일로 저장하지 않는다.

허용된 일회성 사용자 열람으로 이미 받은 HTML은 아래처럼 오프라인 파싱한다. 이 경로는 새 네트워크 요청을 하지 않고 `fetched_at`·HTTP 상태·robots 정책을 미확인으로 남긴다. 모르는 획득 시각을 현재 시각으로 만들지 않는다.

```bash
"$PY" "$CAPTURE" --html workspace/sources/browser-page.html --source-url "$OBSERVED_SOURCE_URL" --limit 20 --out workspace/sources/browser-parsed
```

MCP 호출도 정책을 우회하는 예외가 아니다. robots 확인 실패 시 상시/대량 자동 수집은 중단하고 공식 기업 JD·정식 제공 경로·사용자가 허용된 방식으로 제공한 원문을 사용한다. 공개 페이지 접근 성공과 이용 허가는 별도다.

## 완료 판정

1. 출력 JSON을 다시 읽어 실제 ID 중복·빈 문구·반환 수를 코드로 검증한다.
2. 대표 링크와 선택한 상세의 회사·직무가 일치하는지 직접 확인한다.
3. 목록, 상세 텍스트, 이미지·첨부, 로그인 뒤 현재 문항을 별개로 기록한다.
4. 성공/부분/실패와 원인을 보존한다. `parser_unsupported`는 공고 없음이 아니다.
5. 스냅샷은 `workspace/`에만 두고 공개 Git에 원문·세션·개인 자료를 올리지 않는다.
6. 기본 수집기 전체 지원, 모든 에이전트의 실제 모델 실행, 자동 제출을 완료했다고 주장하지 않는다.

보조 도구의 종료 코드 0은 부분 추출 성공이다. 나머지는 2이며 `snapshot.json` 또는 stderr 진단을 확인한다. `--help`는 0이다. 기본 CLI는 Scrapling 없이 계속 사용할 수 있다.
