# AI Lawsuit Monitor (CourtListener/RECAP & News Extractor)

AI 모델 학습을 위한 데이터 무단 사용 및 관련 저작권 소송을 추적하고 분석하는 자동화 도구입니다. 최근 3일 내의 소송 정보를 **CourtListener(RECAP Archive)**와 **뉴스(RSS)**에서 수집하여 GitHub Issue와 Slack으로 통합 리포트를 제공합니다.

## ✨ 핵심 기능

### 1. 🔍 다각도 소송 추적
- **CourtListener(RECAP) 정밀 탐색**: "PACER Document" 중 Complaint, Petition 등 소장 위주로 우선 수집합니다.
- **뉴스 기반 보강**: RSS 뉴스를 통해 최신 소송 소식을 수집하고, 관련 도켓(Docket) 정보를 역추적하여 상세 정보를 확장합니다.
- **지능형 쿼리**: `queries.py`에 정의된 정밀한 키워드 조합(AI training, LLM, copyright, DMCA 등)을 사용하여 관련성 높은 항목만 필터링합니다.

### 2. ⚖️ 정밀 데이터 분석
- **AI 학습 위험도 점수(0~100)**: 소장의 내용을 분석하여 무단 수집, 학습 직접 언급, 상업적 이용 여부 등을 점수화하고 시각화(🟢, 🟡, ⚠️, 🔥)합니다.
- **주요 섹션 추출**: 소장의 초반 텍스트에서 '소송 이유', 'AI 학습 관련 핵심 주장', '법적 근거'를 자동으로 추출하여 요약합니다.
- **통계 제공**: 연관 소송들의 **Nature of Suit (NOS)** 통계를 요약하여 전반적인 법적 트렌드를 보여줍니다.

### 3. 🤖 스마트 리포팅 & 중복 제거
- **일자별 통합 이슈**: 매일 하나의 GitHub Issue를 생성하고, 매시간 실행 결과를 댓글로 누적합니다.
- **중복 제거 시스템 (Dedup Summary)**: 당일 첫 실행 결과를 기준으로 새로운 정보(New)와 중복 정보(Dup)를 구분하여 리포트 가독성을 높입니다.
- **Slack 알람**: 중복 제거 요약, 수집 현황, 최신 RECAP 문서 링크를 포함한 요약을 실시간으로 발송합니다.
- **자동 관리**: 이전 날짜의 열린 이슈를 자동으로 Close 처리하고 링크를 연결합니다.
- **통합 정리 리포트**: 이슈 종료(Close) 직전, 당일에 수집된 모든 리포트 내용을 취합하여 **"당일 소송건들 통합 정리 자료"**를 댓글로 최종 발행합니다.

## 🛠️ 설정 가이드

### 1. GitHub Secrets (필수)
Repository → Settings → Secrets and variables → Actions → New repository secret

| Name | Description |
|---|---|
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |
| `GITHUB_OWNER` | Repository 소유자 (예: `leemgs`) |
| `GITHUB_REPO` | Repository 이름 (예: `ai-law-suit-tracker-v02`) |
| `GITHUB_TOKEN` | GitHub API 토큰 (`secrets.GITHUB_TOKEN` 사용 가능) |

### 2. GitHub Variables (필수/선택)
Repository → Settings → Secrets and variables → Actions → Variables 탭

| Name | Value (Default) | Description |
|---|---|---|
| `COURTLISTENER_TOKEN` | (선택 권장) | CourtListener API v4 인증 토큰 |
| `LOOKBACK_DAYS` | `3` | 며칠 전까지의 정보를 수집할지 설정 |
| `ISSUE_TITLE_BASE` | `AI 소송 모니터링` | 생성될 이슈의 기본 제목 |
| `ISSUE_LABEL` | `ai-lawsuit-monitor` | 이슈에 부여할 라벨 이름 |
| `SHOW_DOCKET_CANDIDATES`| `0` | 1 설정 시 매칭이 불확실한 도켓 후보군 표시 |
| `COLLAPSE_LONG_CELLS` | `0` | 1 설정 시 도켓 업데이트 등 긴 셀을 접음 |
| `COLLAPSE_ARTICLE_URLS` | `0` | 1 설정 시 기사 URL 목록을 섹션으로 접음 |
| `DEBUG` | `0` | 1 설정 시 상세 실행 로그(디버그 메세지) 출력 |

## 🚀 실행 및 로컬 환경

### GitHub Actions
- **매 시간 정각(UTC)** 자동 실행됩니다.
- `Actions` -> `lawsuit-monitor` -> `Run workflow`를 통해 수동 실행도 가능합니다.

### 로컬 실행
1. 저장소 클론 및 패키지 설치: `pip install -r requirements.txt`
2. `.env` 파일 생성 ( `.env.example` 참고 ):
   ```env
   GITHUB_OWNER=your_id
   GITHUB_REPO=your_repo
   GITHUB_TOKEN=your_pat
   SLACK_WEBHOOK_URL=your_url
   COURTLISTENER_TOKEN=your_cl_token
   DEBUG=1
   ```
3. 실행: `python -m src.run`

## 📊 위험도 평가 기준 (Evaluation Matrix)

| 항목 | 조건 (주요 키워드) | 점수 |
|---|---|---|
| 무단 데이터 수집 명시 | scrape, crawl, ingest, harvest, mining, bulk, robots.txt, unauthorized 등 | +30 |
| 모델 학습 직접 언급 | Nature=820, train, model, llm, generative ai, gpt, transformer, diffusion, inference 등 | +30 |
| 저작권 소송/쟁점 | copyright, infringement, dmca, fair use, exclusive 등 | +15 |
| 상업적 사용 | commercial, profit, monetiz, revenue, subscription, enterprise 등 | +15 |
| 집단소송 | class action, putative class, representative 등 | +10 |

- **80~100 🔥**: 무단 수집 + 학습 + 상업적 사용 (고위험 리스크)
- **60~79 ⚠️**: 모델 학습 직접 언급 및 관련 쟁점 수반
- **40~59 🟡**: 학습 데이터 관련 법적 쟁점 존재
- **0~39 🟢**: 간접 연관 또는 일반적인 주변 이슈

## 📝 참고 사항
- **RECAP 데이터**: PACER에 등록된 문서 중 "공개(RECAP)"된 문서만 접근 가능합니다. 문서가 없는 경우 힌트 정보만 제공됩니다.
- **KST 기준**: 이슈 생성 및 타임스탬프는 한국 표준시(Asia/Seoul)를 기준으로 작동합니다.
- **GitHub Permissions**: Workflow 실행 시 `issues: write` 권한이 필요합니다.

