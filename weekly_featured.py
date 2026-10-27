"""
미국 증시 주간 특징주 요약본 자동화 모듈
- 매주 일요일 저녁 발송
- 4대 핵심 축: 1. 시장 전반, 2. AI 인프라/반도체(NVDA·AMD 필수), 3. 주간 급등/지정학 테마, 4. 안전선호/방어주
- Google Gemini 3.5 Flash 기반 주간 테마 요약 및 한국형 컬러(상승 빨강, 하락 파랑) 서식 적용
"""

import datetime
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from dotenv import load_dotenv
import requests
import yfinance as yf
from jinja2 import Environment, FileSystemLoader

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("WeeklyFeatured")

# 모니터링할 대표 티커 유니버스
WATCH_TICKERS = {
    # 시장 지수
    "SPY": "S&P 500 ETF",
    "QQQ": "나스닥 100 ETF",
    "DIA": "다우존스 ETF",
    "SOXX": "반도체 ETF",
    "IWM": "러셀 2000 ETF",
    # AI 인프라 & 반도체
    "NVDA": "엔비디아",
    "AMD": "AMD",
    "MU": "마이크론",
    "ARM": "ARM",
    "INTC": "인텔",
    "AVGO": "브로드컴",
    "DELL": "델",
    "SMCI": "슈퍼마이크로",
    "HPE": "HPE",
    "ORCL": "오라클",
    # 에너지 & 방산
    "XOM": "엑손모빌",
    "CVX": "셰브론",
    "VLO": "발레로",
    "LMT": "록히드마틴",
    "RTX": "RTX",
    "NOC": "노스롭그루먼",
    # 방어주 & 헬스케어 & 필수소비재
    "LLY": "일라이릴리",
    "UNH": "유나이티드헬스",
    "WMT": "월마트",
    "KO": "코카콜라",
    "COST": "코스트코"
}

SECTOR_ETFS = {
    "XLK": "기술",
    "XLC": "통신",
    "XLY": "소비재",
    "XLP": "필수재",
    "XLE": "에너지",
    "XLF": "금융",
    "XLV": "헬스케어",
    "XLI": "산업재",
    "XLB": "소재",
    "XLRE": "부동산",
    "XLU": "유틸리티"
}


def fetch_sp500_sector_returns():
    """
    S&P 500 11개 섹터 ETF 주간 등락률 및 순위 수집
    """
    logger.info("S&P 500 11개 섹터 ETF 주간 등락률 및 순위 수집 중...")
    sectors = list(SECTOR_ETFS.keys())
    try:
        df = yf.download(sectors, period="1mo", interval="1d", progress=False)['Close']
        results = []
        for s in sectors:
            if s in df.columns:
                series = df[s].dropna()
                if len(series) >= 2:
                    curr = float(series.iloc[-1])
                    prev_idx = -5 if len(series) >= 5 else 0
                    prev = float(series.iloc[prev_idx])
                    pct = ((curr - prev) / prev) * 100
                    cls = "up" if pct > 0 else "down" if pct < 0 else "neutral"
                    sign = "+" if pct > 0 else ""
                    results.append({
                        "ticker": s,
                        "name": SECTOR_ETFS[s],
                        "pct": pct,
                        "pct_str": f"{sign}{pct:.1f}%",
                        "cls": cls
                    })
        results.sort(key=lambda x: x["pct"], reverse=True)
        return results
    except Exception as e:
        logger.warning(f"섹터 등락률 수집 실패 ({e})")
        return []


def fetch_weekly_ticker_returns():
    """
    대표 티커들의 최근 5거래일(주간) 등락률 계산
    """
    logger.info("주간 특징주 분석용 티커 데이터 수집 중...")
    results = {}
    for ticker_sym in WATCH_TICKERS:
        try:
            t = yf.Ticker(ticker_sym)
            hist = t.history(period="1mo")
            if len(hist) >= 2:
                curr = float(hist['Close'].iloc[-1])
                # 5거래일 전 종가 또는 첫 종가
                prev_idx = -5 if len(hist) >= 5 else 0
                prev = float(hist['Close'].iloc[prev_idx])
                pct = ((curr - prev) / prev) * 100
                cls = "up" if pct > 0 else "down" if pct < 0 else "neutral"
                sign = "+" if pct > 0 else ""
                results[ticker_sym] = {
                    "price": curr,
                    "pct": pct,
                    "pct_str": f"{sign}{pct:.1f}%",
                    "cls": cls
                }
        except Exception as e:
            logger.warning(f"티커 수집 실패 {ticker_sym}: {e}")

    return results


def generate_weekly_ai_analysis(ticker_data):
    """
    수집된 주간 수익률을 바탕으로 Gemini 3.5 Flash를 통해 4대 테마 요약문 생성
    """
    api_key = os.getenv("GEMINI_API_KEY")
    now = datetime.datetime.now()
    # 최근 마감된 금요일 기준 날짜 계산
    days_back = (now.weekday() - 4) % 7
    if days_back == 0 and now.weekday() != 4:
        days_back = 7
    last_friday = now - datetime.timedelta(days=days_back)
    start_monday = last_friday - datetime.timedelta(days=4)
    date_range = f"{start_monday.strftime('%y.%m.%d')} ~ {last_friday.strftime('%m.%d')}"

    # 기본 스마트 템플릿
    fallback = get_default_weekly_data(ticker_data, date_range)

    if not api_key:
        logger.warning("GEMINI_API_KEY 미설정, 기본 스마트 템플릿 사용")
        return fallback, date_range

    prompt = f"""
당신은 미국 주식 시장의 한 주간 흐름을 일목요연하게 정리해 주는 전문 주간 브리퍼입니다.
아래의 [이번 주 종목별 주간 등락률 데이터]를 분석하여, 투자자가 한눈에 이해할 수 있는 '주간 특징주 요약' JSON을 작성해 주세요.

[이번 주 종목별 주간 등락률(5거래일)]
{json.dumps({k: v['pct_str'] for k, v in ticker_data.items()}, ensure_ascii=False)}

[작성 지침]
1. 총 4개의 행(Rows)으로 구성:
   - Row 1: No 1, 분류 "시장" (이모지 📊) -> 지수 전반 동향 요약 및 SPY, QQQ, DIA, SOXX 등락률
   - Row 2: No 2, 분류 "AI 인프라" (이모지 🤖) -> 엔비디아(NVDA)와 AMD의 움직임을 필수로 포함하여 반도체/서버 흐름 요약
   - Row 3: No 3, 분류 "지정학/에너지" 또는 그 주 최대 급등 섹터 (이모지 ⚔️ 또는 🚀) -> 에너지/방산 등 강세 테마
   - Row 4: No 4, 분류 "안전선호" 또는 방어주/특징주 (이모지 🦺 또는 🛡️) -> 헬스케어, 필수소비재 등
2. 각 행마다:
   - highlight: 노란색 형광펜에 들어갈 핵심 헤드라인 1줄 (명확하고 직관적인 어조)
   - bullets: 주요 원인 키워드나 요인 (예: "① 코어위브 ② 빅테크 캡엑스 ③ 엔비디아-AMD 경쟁 등", 없으면 빈 문자열)
   - groups: 세부 섹터별 종목 리스트 (label 및 items: [ticker, pct_str, cls])
     - cls는 상승이면 "up", 하락이면 "down"
3. "momentum_leaders" 배열 (주간 52주 신고가 경신 또는 강력한 주도주 3개):
   - name: 종목/자산명 (예: "엔비디아 (NVDA)", "비트코인 (BTC)", "월마트 (WMT)" 등)
   - badge: 배지 텍스트 (예: "52주 신고가", "신고가권 모멘텀", "수급 집중" 등)
   - desc: 한 줄 요약 이유 (15~25자 내외)
4. 반드시 순수 JSON만 반환할 것 (추가 설명이나 마크다운 백틱 금지)

[JSON 형식]
{{
  "rows": [
    {{
      "no": 1,
      "category": "시장",
      "emoji": "📊",
      "highlight": "나스닥 기술주 차익 실현 속 다우 지수 선방",
      "bullets": "",
      "groups": [
        {{
          "label": "주요지수",
          "items": [
            {{"ticker": "SPY", "pct_str": "-0.5%", "cls": "down"}},
            {{"ticker": "QQQ", "pct_str": "-1.8%", "cls": "down"}},
            {{"ticker": "DIA", "pct_str": "+0.5%", "cls": "up"}},
            {{"ticker": "SOXX", "pct_str": "+0.4%", "cls": "up"}}
          ]
        }}
      ]
    }},
    {{
      "no": 2,
      "category": "AI 인프라",
      "emoji": "🤖",
      "highlight": "엔비디아 숨고르기 속 AMD 가속기 반등, 반도체 차별화",
      "bullets": "① 빅테크 캡엑스 유지 ② 차세대 칩 양산 ③ 가성비 가속기 수요",
      "groups": [
        {{
          "label": "GPU/가속기",
          "items": [
            {{"ticker": "NVDA", "pct_str": "-0.6%", "cls": "down"}},
            {{"ticker": "AMD", "pct_str": "+1.7%", "cls": "up"}}
          ]
        }},
        {{
          "label": "메모리/CPU",
          "items": [
            {{"ticker": "MU", "pct_str": "-2.1%", "cls": "down"}},
            {{"ticker": "ARM", "pct_str": "+1.4%", "cls": "up"}},
            {{"ticker": "INTC", "pct_str": "-1.2%", "cls": "down"}}
          ]
        }},
        {{
          "label": "서버",
          "items": [
            {{"ticker": "DELL", "pct_str": "+1.2%", "cls": "up"}},
            {{"ticker": "SMCI", "pct_str": "-2.4%", "cls": "down"}}
          ]
        }}
      ]
    }},
    {{
      "no": 3,
      "category": "지정학/에너지",
      "emoji": "⚔️",
      "highlight": "국제 유가 100달러 돌파에 따른 정유·에너지 및 방산주 강세",
      "bullets": "",
      "groups": [
        {{
          "label": "에너지",
          "items": [
            {{"ticker": "XOM", "pct_str": "+3.8%", "cls": "up"}},
            {{"ticker": "CVX", "pct_str": "+2.5%", "cls": "up"}}
          ]
        }},
        {{
          "label": "방산",
          "items": [
            {{"ticker": "LMT", "pct_str": "+4.2%", "cls": "up"}},
            {{"ticker": "RTX", "pct_str": "+3.1%", "cls": "up"}}
          ]
        }}
      ]
    }},
    {{
      "no": 4,
      "category": "안전선호",
      "emoji": "🦺",
      "highlight": "금리 부담과 경기 둔화 우려로 헬스케어 및 필수소비재 선호",
      "bullets": "",
      "groups": [
        {{
          "label": "헬스케어",
          "items": [
            {{"ticker": "LLY", "pct_str": "+3.2%", "cls": "up"}},
            {{"ticker": "UNH", "pct_str": "+2.8%", "cls": "up"}}
          ]
        }},
        {{
          "label": "필수소비재",
          "items": [
            {{"ticker": "WMT", "pct_str": "+2.1%", "cls": "up"}},
            {{"ticker": "COST", "pct_str": "+1.8%", "cls": "up"}}
          ]
        }}
      ]
    }}
  ],
  "momentum_leaders": [
    {{"name": "엔비디아 (NVDA)", "badge": "52주 신고가권", "desc": "차세대 블랙웰 공급 기대감 및 빅테크 AI 수요 지속"}},
    {{"name": "비트코인 (BTC)", "badge": "주간 모멘텀", "desc": "반감기 후 기관 자금 순유입 가속화 및 위험선호 개선"}},
    {{"name": "월마트 (WMT)", "badge": "52주 신고가", "desc": "소비 둔화 속 필수소비재 점유율 확대 및 실적 호조"}}
  ]
}}
"""

    models_to_try = [
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-flash-lite-latest"
    ]
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.25}
        }
        try:
            res = requests.post(url, json=payload, timeout=25)
            if res.status_code == 200:
                result = res.json()
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
                text = re.sub(r"```$", "", text.strip(), flags=re.MULTILINE)
                match = re.search(r"\{.*\}", text, flags=re.DOTALL)
                if match:
                    text = match.group(0)
                data = json.loads(text)
                # [팩트체크 가드레일] 모든 종목 등락률을 yfinance 실측 데이터로 100% 강제 동기화
                for row in data.get("rows", []):
                    for group in row.get("groups", []):
                        if "items" in group:
                            for st in group["items"]:
                                sym = st.get("ticker", "").upper()
                                if sym in ticker_data:
                                    st["pct_str"] = ticker_data[sym]["pct_str"]
                                    st["cls"] = ticker_data[sym]["cls"]
                            group["stocks"] = group["items"]
                logger.info(f"Gemini AI ({model_name}) 주간 특징주 생성 및 실측 수익률 교차검증 성공! 🎉")
                return data, date_range
        except Exception as ex:
            logger.warning(f"Gemini 주간 특징주 생성 중 오류 ({model_name}): {ex}")

    return fallback, date_range


def get_default_weekly_data(ticker_data, date_range):
    """
    기본 스마트 템플릿
    """
    def g(ticker, default_pct, default_cls):
        t = ticker_data.get(ticker)
        if t:
            return {"ticker": ticker, "pct_str": t["pct_str"], "cls": t["cls"]}
        return {"ticker": ticker, "pct_str": default_pct, "cls": default_cls}

    default_data = {
        "rows": [
            {
                "no": 1,
                "category": "시장",
                "emoji": "📊",
                "highlight": "나스닥 기술주 차익 실현 속 금리·유가 부담에 숨고르기 장세",
                "bullets": "",
                "groups": [
                    {
                        "label": "주요지수",
                        "items": [
                            g("SPY", "-0.9%", "down"),
                            g("QQQ", "-1.8%", "down"),
                            g("DIA", "+0.5%", "up"),
                            g("SOXX", "+0.4%", "up")
                        ]
                    }
                ]
            },
            {
                "no": 2,
                "category": "AI 인프라",
                "emoji": "🤖",
                "highlight": "엔비디아 숨고르기 속 AMD 가속기 반등, 반도체 차별화 장세",
                "bullets": "① 빅테크 캡엑스 확인 대기 ② 엔비디아 차세대 칩 양산 ③ AMD 대안 수요 부각",
                "groups": [
                    {
                        "label": "GPU/가속기",
                        "items": [
                            g("NVDA", "-2.8%", "down"),
                            g("AMD", "-4.0%", "down")
                        ]
                    },
                    {
                        "label": "메모리/CPU",
                        "items": [
                            g("MU", "-3.5%", "down"),
                            g("ARM", "+1.2%", "up"),
                            g("INTC", "-2.1%", "down")
                        ]
                    },
                    {
                        "label": "서버",
                        "items": [
                            g("DELL", "+0.8%", "up"),
                            g("SMCI", "-3.2%", "down")
                        ]
                    }
                ]
            },
            {
                "no": 3,
                "category": "지정학/에너지",
                "emoji": "⚔️",
                "highlight": "중동 불안과 국제 유가 급등세에 따른 에너지 및 방산 섹터 강세",
                "bullets": "",
                "groups": [
                    {
                        "label": "에너지",
                        "items": [
                            g("XOM", "+4.2%", "up"),
                            g("CVX", "+3.1%", "up"),
                            g("VLO", "+2.5%", "up")
                        ]
                    },
                    {
                        "label": "방산",
                        "items": [
                            g("LMT", "+3.8%", "up"),
                            g("RTX", "+2.9%", "up")
                        ]
                    }
                ]
            },
            {
                "no": 4,
                "category": "안전선호",
                "emoji": "🦺",
                "highlight": "증시 변동성 확대 속 피난처로 헬스케어 및 대형 필수소비재 선호",
                "bullets": "",
                "groups": [
                    {
                        "label": "헬스케어",
                        "items": [
                            g("LLY", "+2.8%", "up"),
                            g("UNH", "+2.1%", "up")
                        ]
                    },
                    {
                        "label": "필수소비재",
                        "items": [
                            g("WMT", "+1.9%", "up"),
                            g("COST", "+1.5%", "up"),
                            g("KO", "+1.2%", "up")
                        ]
                    }
                ]
            }
        ]
    }
    default_data["momentum_leaders"] = [
        {"name": "엔비디아 (NVDA)", "badge": "52주 신고가권", "desc": "차세대 블랙웰 공급 기대감 및 빅테크 AI 수요 지속"},
        {"name": "비트코인 (BTC)", "badge": "주간 모멘텀", "desc": "반감기 후 기관 자금 순유입 가속화 및 위험선호 개선"},
        {"name": "월마트 (WMT)", "badge": "52주 신고가", "desc": "소비 둔화 속 필수소비재 점유율 확대 및 실적 호조"}
    ]
    for row in default_data["rows"]:
        for group in row["groups"]:
            group["stocks"] = group["items"]
    return default_data


def render_weekly_featured_image(data, date_range, output_png="output/weekly_featured.png", sector_rankings=None):
    """
    HTML 템플릿에 데이터 바인딩 후 Edge/Playwright로 PNG 렌더링
    """
    os.makedirs(os.path.dirname(output_png), exist_ok=True)
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("weekly_featured.html")

    # config.json 에서 브랜딩 정보 로드
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    rendered_html = template.render(
        date_range=date_range,
        rows=data.get("rows", []),
        momentum_leaders=data.get("momentum_leaders", []),
        sector_rankings=sector_rankings,
        branding=config.get("branding", {})
    )

    html_path = "output/weekly_featured.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(rendered_html)

    # 1. Playwright 시도 (GitHub Actions 및 표준 클라우드)
    try:
        from playwright.sync_api import sync_playwright
        logger.info("Playwright를 통한 주간 특징주 렌더링 시도...")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                viewport={"width": 860, "height": 1300},
                device_scale_factor=2
            )
            file_url = Path(os.path.abspath(html_path)).as_uri()
            page.goto(file_url, wait_until="networkidle")
            page.wait_for_timeout(500)
            body = page.locator("body")
            body.screenshot(path=output_png)
            browser.close()
        logger.info(f"Playwright 렌더링 완료: {output_png}")
        return output_png
    except Exception as pe:
        logger.warning(f"Playwright 미지원 또는 오류 ({pe}), Edge fallback 진행...")

    # 2. Windows 내장 Edge headless fallback
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    ]
    edge_binary = next((p for p in edge_paths if os.path.exists(p)), None)
    if not edge_binary:
        raise FileNotFoundError("Microsoft Edge 브라우저를 찾을 수 없습니다.")

    file_url = Path(os.path.abspath(html_path)).as_uri()
    cmd = [
        edge_binary,
        "--headless",
        "--disable-gpu",
        "--window-size=860,1300",
        f"--screenshot={os.path.abspath(output_png)}",
        "--hide-scrollbars",
        file_url
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info(f"주간 특징주 렌더링 완료: {output_png}")
    return output_png


from subscriber_manager import get_subscribers

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", ".weekly_featured_state.json")


def check_already_sent_this_week() -> bool:
    """이번 주(ISO week 기준) 이미 특징주 브리핑을 발송했는지 확인"""
    if not os.path.exists(STATE_FILE):
        return False
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        today = datetime.datetime.now()
        current_week_key = f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}"
        if state.get("last_sent_week") == current_week_key:
            return True
    except Exception as e:
        logger.warning(f"상태 파일 확인 중 오류: {e}")
    return False


def record_sent_this_week():
    """이번 주 발송 기록 저장"""
    try:
        today = datetime.datetime.now()
        current_week_key = f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}"
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "last_sent_week": current_week_key,
                "last_sent_date": today.strftime("%Y-%m-%d %H:%M:%S")
            }, f, indent=2)
        logger.info(f"✅ 주간 특징주 발송 이력 저장 완료: {current_week_key}")
    except Exception as e:
        logger.warning(f"발송 상태 저장 실패: {e}")


def send_weekly_to_telegram(image_path, data, date_range, heatmap_path="output/weekly_heatmap.png"):
    """
    텔레그램 봇으로 주간 특징주 요약 + S&P 500 히트맵 2장을 한꺼번에(sendMediaGroup 앨범) 전체 구독자에게 발송
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logger.warning("텔레그램 토큰이 설정되지 않았습니다.")
        return False

    subscribers = get_subscribers()
    if not subscribers:
        logger.warning("발송할 구독자 목록이 비어 있습니다.")
        return False

    logger.info(f"📢 주간 특징주 & 히트맵 발송 대상: 총 {len(subscribers)}명 ({subscribers})")

    summary_lines = []
    for r in data.get("rows", []):
        cat = r.get("category")
        emoji = r.get("emoji", "")
        hl = r.get("highlight", "")
        summary_lines.append(f"• {emoji} *{cat}*: {hl}")

    momentum_lines = []
    for m in data.get("momentum_leaders", [])[:3]:
        momentum_lines.append(f"• *{m.get('name', '')}* ({m.get('badge', '')}): {m.get('desc', '')}")

    momentum_block = ""
    if momentum_lines:
        momentum_block = "\n🔥 *주간 주도주 & 신고가 트렌드*\n" + "\n".join(momentum_lines) + "\n"

    caption = f"""🗓 *[주간 결산 1/2] 미국 증시 특징주 & S&P 500 히트맵*
📅 *기준 주간*: {date_range}

📌 *4대 핵심 테마주 주간 결산*
{chr(10).join(summary_lines)}
{momentum_block}
🗺 *S&P 500 주간(1-Week) 히트맵 가이드*
• 🟢 *초록(Green)*: 주간 상승 섹터 (월가 글로벌 표준)
• 🔴 *빨강(Red)*: 주간 하락 및 차익실현 섹터
• ⚡ *NVDA & AMD*: 핵심 보유주 섹터 주간 수급 및 밸류에이션 점검

#미국증시 #주간특징주 #NVDA #AMD #히트맵 #섹터순위"""

    if len(caption) > 1020:
        caption = caption[:1015] + "..."

    # 이미지 바이트 미리 로드
    with open(image_path, "rb") as f1:
        img_bytes = f1.read()

    heatmap_bytes = None
    has_heatmap = bool(heatmap_path and os.path.exists(heatmap_path) and os.path.getsize(heatmap_path) > 1000)
    if has_heatmap:
        with open(heatmap_path, "rb") as f2:
            heatmap_bytes = f2.read()

    media_url = f"https://api.telegram.org/bot{bot_token}/sendMediaGroup"
    photo_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"

    success_count = 0
    for target_chat_id in subscribers:
        sent = False
        # 1. 2장 앨범 발송 시도
        if has_heatmap and heatmap_bytes:
            try:
                media_group = [
                    {
                        "type": "photo",
                        "media": "attach://photo1",
                        "caption": caption,
                        "parse_mode": "Markdown"
                    },
                    {
                        "type": "photo",
                        "media": "attach://photo2"
                    }
                ]
                files = {
                    "photo1": ("featured.png", img_bytes, "image/png"),
                    "photo2": ("heatmap.png", heatmap_bytes, "image/png")
                }
                payload = {
                    "chat_id": target_chat_id,
                    "media": json.dumps(media_group)
                }
                res = requests.post(media_url, data=payload, files=files, timeout=40)
                if res.status_code == 200:
                    logger.info(f"✅ 구독자({target_chat_id}) 주간 2장 앨범 발송 성공!")
                    sent = True
                    success_count += 1
                else:
                    logger.warning(f"구독자({target_chat_id}) 앨범 발송 실패 ({res.status_code}), 단일 사진 시도...")
            except Exception as me:
                logger.warning(f"구독자({target_chat_id}) 앨범 발송 중 예외 ({me}), 단일 사진 시도...")

        # 2. 히트맵 없거나 앨범 실패 시 단일 발송
        if not sent:
            try:
                payload = {
                    "chat_id": target_chat_id,
                    "caption": caption,
                    "parse_mode": "Markdown"
                }
                files = {"photo": ("featured.png", img_bytes, "image/png")}
                res = requests.post(photo_url, data=payload, files=files, timeout=30)
                if res.status_code == 200:
                    logger.info(f"✅ 구독자({target_chat_id}) 단일 사진 발송 성공!")
                    success_count += 1
                else:
                    logger.error(f"❌ 구독자({target_chat_id}) 발송 실패 ({res.status_code}): {res.text}")
            except Exception as e:
                logger.error(f"❌ 구독자({target_chat_id}) 발송 중 예외: {e}")

    logger.info(f"🎉 총 {len(subscribers)}명 중 {success_count}명 발송 완료")
    if success_count > 0:
        record_sent_this_week()
    return success_count > 0


def main():
    import sys
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

    force = "--force" in sys.argv
    if not force and check_already_sent_this_week():
        today = datetime.datetime.now()
        current_week_key = f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}"
        logger.info("==========================================")
        logger.info(f"ℹ️ 이번 주차({current_week_key}) 주간 특징주 & 히트맵은 이미 발송되었습니다.")
        logger.info("ℹ️ 내일(일요일) 스케줄에서는 자동으로 건너뛰고 위클리 캘린더만 발송됩니다.")
        logger.info("ℹ️ 강제로 다시 발송하려면 '--force' 인자를 붙여 실행하세요.")
        logger.info("==========================================")
        return

    logger.info("==========================================")
    logger.info("📊 미국 증시 주간 특징주 & 히트맵 파이프라인 시작")
    logger.info("==========================================")

    # 1. 티커 데이터 및 11개 섹터 주간 등락률 수집
    ticker_data = fetch_weekly_ticker_returns()
    sector_rankings = fetch_sp500_sector_returns()

    # 2. Gemini AI 주간 분석
    data, date_range = generate_weekly_ai_analysis(ticker_data)

    # 3. 고화질 특징주 이미지 렌더링
    output_png = "output/weekly_featured.png"
    render_weekly_featured_image(data, date_range, output_png, sector_rankings=sector_rankings)

    # 4. S&P 500 주간 히트맵 캡처
    heatmap_png = "output/weekly_heatmap.png"
    try:
        from weekly_heatmap import capture_finviz_heatmap
        capture_finviz_heatmap(heatmap_png)
    except Exception as he:
        logger.warning(f"히트맵 캡처 중 오류: {he}")

    # 5. 텔레그램 2장 묶음(앨범) 발송
    send_weekly_to_telegram(output_png, data, date_range, heatmap_png)

    logger.info("==========================================")
    logger.info(f"🎉 주간 특징주 및 히트맵 완료! 결과: {os.path.abspath(output_png)}")
    logger.info("==========================================")


if __name__ == "__main__":
    main()
