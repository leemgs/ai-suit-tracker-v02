# AI Lawsuit Monitor (CourtListener RECAP Complaint Extractor)

최근 3일 내 "AI 모델 학습을 위한 무단/불법 데이터 사용" 관련 소송/업데이트를
- CourtListener(=RECAP Archive)에서 **도켓 + RECAP 문서(특히 Complaint)**를 우선 수집하고,
- 뉴스(RSS)로 보강하여

GitHub Issue에 댓글로 누적하고 Slack으로 요약을 발송합니다.

## 핵심 기능(추가됨: B - Complaint 정밀 추출)
- CourtListener 검색 결과에서 **도켓(docket) 식별**
- 도켓에 연결된 **RECAP 문서 목록 조회**
- 문서 유형이 **Complaint / Amended Complaint / Petition** 등인 항목을 우선 선택
- 가능하면 PDF를 내려받아 **초반 텍스트 일부를 추출**해
  - `소송이유`(자동 요약용 스니펫)
  - `히스토리`(최근 제출 문서 목록 일부)
  를 더 정확하게 구성

> 주의: RECAP은 "공개된 문서만" 존재합니다. 어떤 사건은 RECAP 문서가 없을 수 있으며,
> 그 경우 CourtListener 단계는 힌트만 남기고 뉴스(RSS)로 폴백합니다.

## 뉴스 제목만 있는 경우 도켓/사건 역조회
- RSS에 **소송번호(도켓번호)**가 있으면 가장 정확하게 도켓을 확장합니다.
- 소송번호가 없더라도, "Bartz et al. v. Anthropic"처럼 기사 제목에서 **"A v. B" 형태의 사건명**이 추정되면
  이를 이용해 CourtListener에서 도켓을 역조회합니다.
- Issue의 뉴스 표에서는 "소송제목/기사제목" 컬럼으로 **(추정 사건명 / 기사 원제목)**을 함께 표시합니다.

## GitHub Secrets 설정
Repository → Settings → Secrets and variables → Actions → New repository secret

### 필수
- `SLACK_WEBHOOK_URL`: Slack Incoming Webhook URL

### 선택(권장)
- `COURTLISTENER_TOKEN`: CourtListener API 토큰 (v4 API 인증)

> 참고: GitHub Issue 댓글 업로드는 workflow에 `permissions: issues: write`가 설정되어 있어
> 기본 제공 `GITHUB_TOKEN`(`${{ github.token }}`)으로 동작합니다. 별도 PAT는 필요하지 않습니다.


## 최근 범위(일수) 설정
- 기본값: 3일
- 변경: 환경변수 `LOOKBACK_DAYS`를 2 등으로 설정
  - 예: `LOOKBACK_DAYS=2`
- 로컬 실행 시 `.env` 또는 `.env.example`을 참고해 관리할 수 있습니다.

## 도켓 후보(Top3) 표시 옵션
- 소송번호/사건명 매칭이 애매할 때, CourtListener의 **도켓 후보 Top3**를 리포트 표에 함께 표시할 수 있습니다.
- 환경변수: `SHOW_DOCKET_CANDIDATES=1`
  - 기본값은 0(표시 안 함)

### GitHub Actions Variables로 켜기(추천)
1) GitHub 레포로 이동
2) 상단 메뉴에서 **Settings**
3) 왼쪽 사이드바에서 **Secrets and variables** → **Actions**
4) 상단 탭에서 **Variables** 선택 (Secrets 탭이 아니라 Variables 탭)
5) **New repository variable** 클릭
6) 아래처럼 추가
   - **Name**: `SHOW_DOCKET_CANDIDATES`
   - **Value**: `1`

- 끄려면
  - Value를 `0`으로 바꾸거나,
  - 변수를 삭제(미설정)하면 됩니다.

> 참고: 이 프로젝트 workflow는 `${{ vars.SHOW_DOCKET_CANDIDATES }}`를 읽습니다.
> Variables에만 값을 넣으면 되고, Secrets에 넣을 필요는 없습니다.

### 로컬 실행 시 (.env)
레포 루트의 `.env.example`를 복사해 `.env`를 만든 뒤 아래 값을 추가하세요.

```
SHOW_DOCKET_CANDIDATES=1
```


## (옵션) 긴 섹션 접기(<details>/<summary>)
이슈가 길어질 때 가독성을 위해 아래 항목을 “접기” 형태로 렌더링할 수 있습니다.

### A) 표 안의 긴 셀 접기(도켓 후보 Top3 / 최근 도켓 업데이트 3건)
- 환경변수: `COLLAPSE_LONG_CELLS=1`
  - `1`이면, 표의 **도켓 후보(Top3)** 및 **최근 도켓 업데이트(3)** 컬럼이 `<details>/<summary>`로 접혀서 표시됩니다.
  - `0` 또는 미설정이면, 기존처럼 그대로 펼쳐서 표시합니다.

#### GitHub Actions Variables로 켜기(추천)
Repository → Settings → Secrets and variables → Actions → Variables → New repository variable

- Name: `COLLAPSE_LONG_CELLS`
- Value: `1`

### B) "기사 주소" 섹션 접기
- 환경변수: `COLLAPSE_ARTICLE_URLS=1`
  - `1`이면, 각 항목의 기사 URL 목록이 `<details>/<summary>`로 접혀서 표시됩니다.
  - `0` 또는 미설정이면, 기존처럼 그대로 펼쳐서 표시합니다.

#### GitHub Actions Variables로 켜기(추천)
Repository → Settings → Secrets and variables → Actions → Variables → New repository variable

- Name: `COLLAPSE_ARTICLE_URLS`
- Value: `1`

### 로컬 실행 시 (.env)
```
COLLAPSE_LONG_CELLS=1
COLLAPSE_ARTICLE_URLS=1
```


---

## 문제 해결: GitHub Issue에서 표(테이블)가 텍스트로만 보이는 경우
GitHub Issues의 Markdown 테이블은 아주 쉽게 깨집니다. 대표 원인은 아래 두 가지입니다.

1) **구분선(---) 행이 유효하지 않음**
   - 각 컬럼은 `---`처럼 **최소 3개의 하이픈**이 필요합니다.
   - 예: `|---|---|---|` 형태

2) **셀 값에 줄바꿈이 포함됨(행이 여러 줄로 분리됨)**
   - 테이블은 **각 행이 한 줄**이어야 합니다.
   - 셀 안에 줄바꿈이 필요하면 `\n` 대신 `<br>`를 사용해야 합니다.

이 레포의 `src/render.py`는 위 문제를 피하도록,
- 셀 내부 줄바꿈을 `<br>`로 변환하고
- 유효한 구분선 행을 자동 생성합니다.


## 커스터마이징
- `src/queries.py`에서 키워드 조정
- `data/known_cases.yml`에 사건 매핑 추가

## 실행
- GitHub Actions: 매시간 정각(UTC)
- 수동 실행: Actions → hourly-monitor → Run workflow


## 균형형 쿼리 튜닝 적용
- CourtListener Search에 `type=r`, `available_only=on`, `order_by=entry_date_filed desc`를 적용해 RECAP 문서(도켓/문서) 중심으로 최신 항목을 우선 수집합니다.


## 추가: 소송번호(도켓) 기반 확장 필드
- 접수일(Date Filed), 상태(Open/Closed), 담당 판사/치안판사, Nature of Suit(NOS), Cause(법률 조항), Parties roster, Complaint 문서번호/링크, 최근 도켓 업데이트 3건을 RECAP 도켓에서 자동 추출합니다.


## Actions 권한/로그 개선
- workflow에 `permissions: issues: write`를 추가해 PAT 없이도 이슈 댓글 업로드가 가능하도록 했습니다.
- Actions 로그에 리포트 본문 일부를 출력하고 `tee run_output.log`로 로그 파일도 남깁니다.


## 일자별 이슈 생성
- 매 실행 결과는 **당일(Asia/Seoul) 날짜가 포함된 이슈**에 누적됩니다.
- 이슈 제목 형식: `AI 불법/무단 학습데이터 소송 모니터링 (YYYY-MM-DD)`
- 필요 시 기본 제목은 `ISSUE_TITLE_BASE` 환경변수로 변경할 수 있습니다.


## 이전 날짜 이슈 자동 Close
- 매일 새 날짜 이슈를 생성한 뒤, 같은 라벨을 가진 이전 날짜의 열린 이슈들을 자동으로 Close 처리합니다.
- 제목 형식이 `기본제목 (YYYY-MM-DD)`인 이슈만 대상이며, 다른 이슈는 닫지 않습니다.


## 자동 Close 시 마무리 코멘트
- 이전 날짜 이슈를 닫기 전에 아래 코멘트를 자동으로 남깁니다.
  - `이 이슈는 다음 날짜 리포트 생성으로 자동 종료되었습니다.`


## 이슈 제목 형식 (KST)
- 이슈 제목: `AI 불법/무단 학습데이터 소송 모니터링 (YYYY-MM-DD HH:MM)`
- 시간은 **KST(Asia/Seoul)** 기준입니다.
- 새 이슈가 생성되면 이전 이슈에 `다음 리포트` 링크 코멘트를 남긴 뒤 자동 Close 합니다.


## 변경: 하루 1개 이슈 + 실행시각은 본문에 기록
- 이슈 제목은 **일자 기준 1개**로 생성됩니다: `AI 불법/무단 학습데이터 소송 모니터링 (YYYY-MM-DD)`
- 매 실행(매시간) 결과는 같은 날짜 이슈에 댓글로 누적되며, 댓글 상단에 `실행 시각(KST)`가 포함됩니다.


## RECAP 보조 모드(Fallback)
- 최근 3일 내 해당 키워드로 RECAP 문서를 찾되, **Complaint가 0건이면** Motion/Order/Opinion/Judgment 등 핵심 문서를 보조로 수집합니다.
- 보조 모드로 수집된 문서는 리포트의 문서유형에 `FALLBACK:` 접두사가 붙습니다.
