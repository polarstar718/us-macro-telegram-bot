# 📊 미국 증시 데일리 매크로 & 위클리(특징주·캘린더) 자동 브리핑 시스템

매일 아침 미국 정규장 마감 직후 최신 시장 지표와 AI 해설을 보내는 **데일리 매크로 브리핑(화~토 06:40)** 및 한 주간의 흐름을 짚는 **주간 특징주 브리핑**과 차주를 대비하는 **위클리 캘린더(일요일 19:30 동시 발송)**를 텔레그램으로 자동 발송하는 프리미엄 AI 금융 비서 시스템입니다.

---

## 📸 발송 서비스 안내

### 1️⃣ [데일리] 미국 증시 매크로 브리핑 (화~토 아침 06:40 KST)
- **주요 지수 & 스트립**: S&P 500, 다우, 나스닥 + 필라델피아 반도체(SOX), 러셀 2000, 비트코인
- **TOP PICKS 심층 점검**: 포트폴리오 최대 비중인 **엔비디아(NVDA)** & **AMD** 전용 심층 분석 카드
- **매크로 6종 & 빅테크 M7**: 10년물 금리, 환율, 유가, VIX 및 빅테크 종가
- **Gemini 초보자 맞춤 해설**: 오늘의 판단 1줄 요약 + '왜 이렇게 판단했나' 3대 요인 + 체크포인트

### 2️⃣ [위클리 1/2] 주간 특징주 요약 & S&P 500 히트맵 2장 앨범 (매주 일요일 저녁 19:30 KST)
- **2장 묶음(앨범) 발송**: 주간 특징주 요약표와 S&P 500 컬러맵 2장이 한 메시지(앨범)로 전송
- **사진 1. 주간 특징주 요약**:
  - No 1. 시장 (📊): 지수 전반 동향 및 SPY, QQQ, DIA, SOXX 등락
  - No 2. AI 인프라 (🤖): 엔비디아(NVDA) & AMD 핵심 점검 및 반도체/서버주 동향
  - No 3. 주간 급등/지정학 (⚔️): 에너지, 방산 등 한 주간 가장 강했던 주도 섹터
  - No 4. 안전선호/방어주 (🦺): 헬스케어, 필수소비재 등 피난처 및 특징주
  - 한국 증시 표준 컬러 (상승 빨간색 / 하락 파란색)
- **사진 2. S&P 500 주간 히트맵 (`컬러맵.jpg` 스타일)**:
  - Finviz S&P 500 1-Week Performance 맵 전면 고해상도 캡처
  - 월가 글로벌 표준 컬러: **상승 초록색(Green)**, **하락 빨간색(Red)**
  - 섹터별 시가총액 비중 트리맵으로 시장 전반의 수급 쏠림을 한눈에 조망
- **통합 상세 캡션**: 4대 테마주 핵심 요약과 히트맵 섹터별 강세/약세 가이드 제공

### 3️⃣ [위클리 2/2] 차주 위클리 캘린더 (매주 일요일 저녁 19:30 KST)
- **차주(다음 주) 프리뷰**: 다음 주 월~금 미국 매크로 일정, 지표 발표, 실적 일정 완벽 대비
- **5대 고정 컬러 범례**:
  - 🌸 **주요이벤트** (`#FFE4E6` 연분홍)
  - 🏛 **정책/통화** (`#F1F5F9` 슬레이트 그레이)
  - 📊 **지표** (`#DCFCE7` 에메랄드 민트)
  - 💼 **실적** (`#E0F2FE` 스카이블루)
  - 🎤 **발언** (`#FEF08A` 옐로우)
- **AI 중요도 별표(★~★★★)**: 시장 파급력 높은 초특급 일정 빨간 별표 강조
- **Weekly Focus & To Do**: 차주 핵심 화두 2가지 및 중기 체크포인트 5종
- **주요 기업 실적 발표 일정**: 주요 빅테크 & AMD/NVDA 실적 일정 및 장 시작 전(B)/장 마감 후(A) 표기

---

## 🛠 폴더 구조

```text
미국_증시_매크로_브리핑/
├── .github/
│   └── workflows/
│       ├── daily_briefing.yml       # 화~토 아침 06:40 KST 클라우드 자동 발송
│       └── weekly_briefing.yml      # 매주 일요일 저녁 19:30 KST (특징주 + 캘린더 2종 발송)
├── templates/
│   ├── briefing.html                # 데일리 매크로 고화질 카드 템플릿
│   ├── weekly_featured.html         # 주간 특징주 요약 3단 테이블 템플릿
│   └── weekly_calendar.html         # 차주 위클리 캘린더 템플릿 (5색 범례)
├── config.json                      # 종목/지표 및 브랜딩 커스터마이징 설정
├── main.py                          # 데일리 브리핑 파이프라인
├── weekly_featured.py               # 주간 특징주 브리핑 파이프라인 (히트맵 앨범 발송)
├── weekly_heatmap.py                # S&P 500 주간 히트맵 캡처 모듈
├── weekly_calendar.py               # 차주 위클리 캘린더 파이프라인
├── run_briefing.bat                 # 데일리 윈도우 스케줄러 실행 스크립트
├── run_weekly.bat                   # 위클리 윈도우 스케줄러 실행 스크립트 (특징주 + 캘린더 순차 실행)
├── requirements.txt                 # 파이썬 패키지 목록
└── .env                             # API 키 및 텔레그램 봇 토큰
```

---

## 🚀 1. 로컬 환경에서 테스트 실행하기

### ① 필수 패키지 설치
터미널(PowerShell)을 열고 해당 폴더로 이동한 뒤 설치합니다:

```powershell
cd "c:\Users\polar\OneDrive\바탕 화면\최광민\AI\미국_증시_매크로_브리핑"
& "C:\Users\polar\AppData\Local\Programs\Python\Python312\python.exe" -m pip install -r requirements.txt
```

### ② 환경 변수 설정 (`.env`)
`.env.example` 파일을 복사하여 `.env` 파일을 만듭니다:

```env
# Google Gemini API Key (무료 발급: https://aistudio.google.com/app/apikey)
GEMINI_API_KEY=your_gemini_api_key_here

# 텔레그램 봇 토큰 및 수신할 본인 Chat ID
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here
```
> **TIP**: API 키가 없어도 자동으로 스마트 기본 분석 템플릿으로 고화질 이미지를 즉시 생성해 보실 수 있습니다.

### ③ 실행하기
```powershell
& "C:\Users\polar\AppData\Local\Programs\Python\Python312\python.exe" main.py
```
실행이 완료되면 `output/briefing.png` 경로에 아름다운 카드뉴스 이미지가 생성됩니다!

---

## 📱 2. 텔레그램 봇 2분 설정 가이드

1. **봇 만들기**: 텔레그램 검색창에서 `@BotFather`를 검색하고 대화를 시작합니다.
   - `/newbot` 입력 후 안내에 따라 봇 이름과 사용자명을 정하면 **봇 토큰(HTTP API Token)**을 줍니다.
2. **내 Chat ID 확인하기**: 텔레그램 검색창에서 `@userinfobot`을 검색하고 `/start`를 누르면 내 **Id (숫자)**를 알려줍니다.
3. 방금 만든 봇 대화방으로 들어가서 **'시작(Start)'** 버튼을 한 번 눌러줍니다.
4. 발급받은 토큰과 Chat ID를 `.env` 파일에 적어두면 끝!

---

## ☁️ 3. GitHub Actions로 완전 무료 매일 아침 자동 발송하기 (PC 전원 불필요)

컴퓨터를 켜두지 않아도 **화~토요일 아침 06:40 KST**에 GitHub 서버가 알아서 실행하고 텔레그램으로 보내줍니다.

1. 본인의 **GitHub 새 리포지토리(Private 추천)**를 생성합니다.
2. 이 폴더의 파일들을 GitHub에 푸시합니다:
   ```bash
   git init
   git add .
   git commit -m "feat: Daily US Macro Briefing System"
   git remote add origin https://github.com/사용자아이디/리포지토리이름.git
   git push -u origin main
   ```
3. GitHub 리포지토리 페이지의 **Settings > Secrets and variables > Actions** 메뉴로 이동합니다.
4. **New repository secret** 버튼을 눌러 다음 3가지 시크릿을 등록합니다:
   - `GEMINI_API_KEY`: Google AI Studio에서 발급받은 Gemini 키
   - `TELEGRAM_BOT_TOKEN`: @BotFather가 준 봇 토큰
   - `TELEGRAM_CHAT_ID`: 본인의 텔레그램 숫자 ID
5. 이제 상단 **Actions** 탭으로 가면 매일 아침 자동 스케줄이 등록되어 있으며, **'Run workflow'** 버튼을 눌러 언제든 즉시 시험 발송해 볼 수 있습니다!

---

## ⚙️ 4. 나만의 관심 종목 및 브랜딩 변경 (`config.json`)

`config.json` 파일을 열어 원하는 대로 수정할 수 있습니다:
- **종목 추가/변경**: `indices`, `macro`, `m7` 배열에 원하는 티커(예: `NVDA`, `TSLA`, `BTC-USD`, `005930.KS`)를 자유롭게 추가하거나 변경할 수 있습니다.
- **프로필 브랜딩**: `branding` 섹션에서 성함, 직함, 연락처, 슬로건을 변경하거나 `show: false`로 숨길 수 있습니다.
- **타이틀 변경**: 상단 헤더 문구나 서브타이틀을 나만의 스터디명이나 브랜드명으로 변경할 수 있습니다.
