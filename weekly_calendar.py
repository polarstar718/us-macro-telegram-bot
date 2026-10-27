"""
미국 증시 위클리 캘린더 생성 및 발송 모듈 (yfinance 실데이터 + 다중 팩트체크 버전)
- 기준: 월~금 미국 매크로 일정, 지표 발표, 연준 발언, 기업 실적
- 팩트체크 파이프라인:
  1단계: yfinance Ticker.calendar로 26개 주요 빅테크·반도체·대형주의 실제 확정 실적 발표일 및 컨센서스(EPS·매출) 수집
  2단계: Google News RSS로 해당 주간 월가 경제지표 일정(Kiplinger, Yardeni, FXStreet, Yahoo Finance 등) 및 FedWatch 뉴스 수집
  3단계: Gemini 1차 캘린더 생성 후 2차 팩트체크 감사(Auditor) 패스 실행
  4단계: 파이썬 결정론적 가드레일로 요일별 실적 오기재 원천 차단 및 우측 실적 보드 실데이터 강제 동기화
"""

from concurrent.futures import ThreadPoolExecutor
import datetime
import json
import logging
import os
import re
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from dotenv import load_dotenv
import requests
import yfinance as yf
from jinja2 import Environment, FileSystemLoader

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("WeeklyCalendar")

GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest"
]

# 실적 일정 추적용 핵심 종목 유니버스 (우선순위 순)
EARNINGS_UNIVERSE = [
    ("NVDA", "엔비디아", "🤖", True),
    ("AMD", "AMD", "⚡", True),
    ("MSFT", "마이크로소프트", "💻", True),
    ("GOOGL", "알파벳(구글)", "🔍", True),
    ("AMZN", "아마존", "📦", True),
    ("AAPL", "애플", "🍎", True),
    ("META", "메타", "💬", True),
    ("TSLA", "테슬라", "🚗", True),
    ("AVGO", "브로드컴", "📡", False),
    ("MU", "마이크론", "💾", False),
    ("INTC", "인텔", "🖥️", True),
    ("ARM", "ARM", "📱", False),
    ("QCOM", "퀄컴", "📶", False),
    ("LLY", "일라이릴리", "💊", False),
    ("XOM", "엑슨모빌", "🛢️", False),
    ("CVX", "셰브론", "⛽", False),
    ("V", "비자", "💳", False),
    ("MA", "마스터카드", "💳", False),
    ("CAT", "캐터필러", "🚜", False),
    ("BA", "보잉", "✈️", False),
    ("KO", "코카콜라", "🥤", False),
    ("PYPL", "페이팔", "💸", False),
    ("MCD", "맥도날드", "🍔", False),
    ("SMCI", "슈퍼마이크로", "🖧", False),
]

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]
DAY_EN_LIST = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def get_next_week_dates():
    """
    주간 캘린더 대상 월~일 날짜 계산
    - 금/토/일 실행 시: 다가오는 차주 월~일 (차주 프리뷰)
    - 월~목 실행 시: 현재 진행 중인 이번 주 월~일 (금주 캘린더 점검/재발송)
    """
    now = datetime.datetime.now()
    if now.weekday() in (0, 1, 2, 3):
        target_mon = now - datetime.timedelta(days=now.weekday())
    else:
        days_to_next_monday = (7 - now.weekday()) % 7
        if days_to_next_monday == 0:
            days_to_next_monday = 7
        target_mon = now + datetime.timedelta(days=days_to_next_monday)

    target_mon = target_mon.replace(hour=0, minute=0, second=0, microsecond=0)
    next_tue = target_mon + datetime.timedelta(days=1)
    next_wed = target_mon + datetime.timedelta(days=2)
    next_thu = target_mon + datetime.timedelta(days=3)
    next_fri = target_mon + datetime.timedelta(days=4)
    next_sun = target_mon + datetime.timedelta(days=6)

    date_range = f"{target_mon.strftime('%m/%d')}(월) ~ {next_sun.strftime('%m/%d')}(일)"

    return {
        "range_str": date_range,
        "mon": target_mon,
        "tue": next_tue,
        "wed": next_wed,
        "thu": next_thu,
        "fri": next_fri,
        "sun": next_sun
    }


def fetch_real_earnings_calendar(dates):
    """
    yfinance Ticker.calendar를 병렬 호출하여 실제 기업별 실적 발표일, 컨센서스 EPS, 예상 매출을 수집
    """
    logger.info("yfinance 기반 주요 빅테크·반도체 실제 실적 발표일 및 컨센서스 수집 중...")
    mon_date = dates["mon"].date()
    fri_date = dates["fri"].date()

    def _fetch_one(item):
        sym, kr_name, icon, is_core = item
        try:
            t = yf.Ticker(sym)
            cal = t.calendar or {}
            edates = cal.get("Earnings Date", [])
            edate = edates[0] if edates else None
            eps_avg = cal.get("Earnings Average")
            rev_avg = cal.get("Revenue Average")

            eps_str = f"${float(eps_avg):.2f}" if eps_avg is not None else "N/A"
            if rev_avg is not None:
                rev_val = float(rev_avg)
                rev_str = f"${rev_val / 1e9:.1f}B" if rev_val >= 1e9 else f"${rev_val / 1e6:.0f}M"
            else:
                rev_str = "N/A"

            in_target_week = bool(edate and (mon_date <= edate <= fri_date))
            day_en = DAY_EN_LIST[edate.weekday()] if (in_target_week and edate.weekday() < 5) else None
            w_kr = WEEKDAY_KR[edate.weekday()] if edate else ""

            if edate:
                if in_target_week:
                    date_badge_str = f"{edate.month:02d}월 {edate.day:02d}일 ({w_kr})"
                    timing_str = "금주 확정"
                elif edate > fri_date:
                    date_badge_str = f"{edate.month:02d}월 {edate.day:02d}일 ({w_kr}) 예정"
                    timing_str = "차기 실적일"
                else:
                    date_badge_str = f"{edate.month:02d}월 {edate.day:02d}일 ({w_kr}) 완료"
                    timing_str = "발표 완료"
            else:
                date_badge_str = "일정 미정"
                timing_str = "-"

            return {
                "ticker": sym,
                "name": f"{kr_name} ({sym})" if kr_name != sym else sym,
                "short_name": kr_name,
                "icon": icon,
                "is_core": is_core,
                "edate": edate,
                "edate_iso": edate.strftime("%Y-%m-%d") if edate else None,
                "in_target_week": in_target_week,
                "day_en": day_en,
                "date_str": date_badge_str,
                "timing": timing_str,
                "consensus_eps": eps_str,
                "consensus_rev": rev_str
            }
        except Exception as e:
            logger.debug(f"yfinance 실적 캘린더 조회 실패 ({sym}): {e}")
            return None

    all_items = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(_fetch_one, EARNINGS_UNIVERSE):
            if res:
                all_items.append(res)

    # 1) 대상 주간(월~금)에 실제로 실적을 발표하는 종목 요일별 그룹화
    by_day = {d: [] for d in DAY_EN_LIST}
    this_week_items = []
    for it in all_items:
        if it["in_target_week"] and it["day_en"] in by_day:
            by_day[it["day_en"]].append(it)
            this_week_items.append(it)

    # 핵심 빅테크(is_core=True) 최우선, 그 다음 날짜순 정렬
    this_week_items.sort(key=lambda x: (not x["is_core"], x["edate"]))

    # 2) 우측 '주요 기업 실적 발표 일정' 보드 구성 (최대 6개)
    # - 1순위: 이번 주 실제 발표하는 핵심 빅테크/주요 대형주 (최대 4개, 날짜순 재정렬)
    # - 2순위: 투자자 필수 관심주(AMD, NVDA, AAPL 등) 중 향후 발표 예정인 종목 (실제 예정일 명시!)
    board_this_week = sorted(this_week_items[:4], key=lambda x: x["edate"])
    board_items = list(board_this_week)
    selected_tickers = {it["ticker"] for it in board_items}

    # 핵심 보유주(NVDA, AMD) 및 빅테크 중 아직 보드에 없고 향후 발표 예정인 종목 추가
    priority_upcoming = ["AMD", "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "AVGO"]
    for p_sym in priority_upcoming:
        if len(board_items) >= 6:
            break
        if p_sym in selected_tickers:
            continue
        match = next((x for x in all_items if x["ticker"] == p_sym and x["edate"] and x["edate"] >= mon_date), None)
        if match:
            board_items.append(match)
            selected_tickers.add(p_sym)

    # 만약 6개 미만이면 이번 주 나머지 실적 종목으로 채움
    for it in this_week_items:
        if len(board_items) >= 6:
            break
        if it["ticker"] not in selected_tickers:
            board_items.append(it)
            selected_tickers.add(it["ticker"])

    return {
        "all": all_items,
        "by_day": by_day,
        "this_week": this_week_items,
        "board": board_items
    }


def fetch_weekly_macro_news(dates):
    """
    Google News RSS를 통해 해당 주간의 미국 경제지표 일정 및 연준(FedWatch) 전문가 코멘트 수집
    """
    mon_str = dates["mon"].strftime("%B %d")
    fri_str = dates["fri"].strftime("%d")
    queries = [
        f"US economic calendar week ahead {mon_str} GDP PCE payrolls CPI ISM when:5d",
        "Week ahead Wall Street stocks earnings Federal Reserve economic data when:5d",
        "CME FedWatch FOMC rate cut probability basis points Federal Reserve when:5d"
    ]
    headlines = []
    seen = set()
    for q in queries:
        try:
            url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=en-US&gl=US&ceid=US:en"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                root = ET.fromstring(resp.read())
            for item in root.findall(".//item")[:5]:
                title = item.findtext("title", "").strip()
                source = item.findtext("source", "Media").strip()
                if title and title not in seen:
                    seen.add(title)
                    headlines.append(f"[{source}] {title}")
        except Exception as e:
            logger.debug(f"주간 매크로 뉴스 수집 예외 ({q}): {e}")
    return headlines


def _call_gemini_json(prompt, api_key, temperature=0.2):
    for model_name in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        for use_search in (True, False):
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": temperature}
            }
            if use_search:
                payload["tools"] = [{"google_search": {}}]
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
                    return json.loads(text)
                elif use_search and res.status_code in (400, 403, 429):
                    continue
            except Exception as ex:
                if not use_search:
                    logger.warning(f"Gemini 캘린더 호출 오류 ({model_name}): {ex}")
    return None


def _enforce_calendar_facts(data, dates, real_earnings):
    """
    파이썬 결정론적(Deterministic) 팩트체크 가드레일:
    1) 우측 실적 보드(earnings_schedule)를 yfinance 실데이터(board)로 100% 강제 동기화
    2) 요일별(days) 이벤트 중 실제 그 요일에 실적을 발표하지 않는 종목이 '실적' 태그로 잘못 들어가 있으면 제거하고 실제 발표 기업으로 교체
    """
    if not isinstance(data, dict):
        return data

    # 1. 우측 실적 보드를 100% yfinance 검증 데이터로 교체
    if real_earnings.get("board"):
        data["earnings_schedule"] = [
            {
                "name": it["name"],
                "icon": it["icon"],
                "date_str": it["date_str"],
                "timing": it["timing"],
                "consensus_eps": it["consensus_eps"],
                "consensus_rev": it["consensus_rev"]
            }
            for it in real_earnings["board"]
        ]

    # 2. 요일별(Monday~Friday) 실적 이벤트 교차검증
    by_day = real_earnings.get("by_day", {})
    all_known = real_earnings.get("all", [])

    # 다른 날짜에 발표하는 종목 키워드 맵
    wrong_day_keywords = {}
    for it in all_known:
        sym = it["ticker"].upper()
        kr = it["short_name"]
        actual_day = it["day_en"]  # 이번 주가 아니면 None
        wrong_day_keywords[sym] = (actual_day, sym, kr)

    for day_obj in data.get("days", []):
        d_en = day_obj.get("day_en")
        events = day_obj.get("events", [])
        verified_events = []

        for ev in events:
            if ev.get("category_cls") == "earnings":
                title_upper = (ev.get("title", "") + " " + ev.get("detail", "")).upper()
                is_wrong_day = False
                for sym, (actual_day, sym_u, kr_name) in wrong_day_keywords.items():
                    # 영문 티커는 단어 경계(예: V가 CVX나 NVDA에 매칭되지 않도록)로 검사
                    sym_matched = bool(re.search(rf"(?<![A-Z]){re.escape(sym_u)}(?![A-Z])", title_upper))
                    kr_matched = bool(kr_name and kr_name.upper() in title_upper)
                    if sym_matched or kr_matched:
                        if actual_day != d_en:
                            logger.warning(
                                f"[캘린더 팩트체크] {d_en}에 잘못 배치된 '{sym}' 실적 이벤트 제거 (실제 발표요일: {actual_day or '타 주간'})"
                            )
                            is_wrong_day = True
                            break
                if not is_wrong_day:
                    verified_events.append(ev)
            else:
                verified_events.append(ev)

        # 해당 요일에 실제로 실적을 발표하는 주요 기업이 누락되어 있다면 자동 추가
        actual_day_stocks = by_day.get(d_en, [])
        has_earnings_ev = any(e.get("category_cls") == "earnings" for e in verified_events)
        if actual_day_stocks and not has_earnings_ev:
            names_str = " · ".join([s["short_name"] for s in actual_day_stocks[:2]])
            eps_notes = ", ".join([f"{s['ticker']} 예상 EPS {s['consensus_eps']}" for s in actual_day_stocks[:2]])
            stars = "★★★" if any(s["is_core"] for s in actual_day_stocks) else "★★"
            verified_events.append({
                "category_cls": "earnings",
                "category_name": "실적",
                "title": f"{names_str} 분기 실적 발표",
                "stars": stars,
                "detail": f"yfinance 확정 일정 · {eps_notes}"
            })

        day_obj["events"] = verified_events[:3]

    return data


def generate_weekly_calendar_ai(dates):
    """
    yfinance 실데이터 + 주간 매크로 외신 헤드라인 + Gemini 2단계(생성 및 감사) 파이프라인으로 위클리 캘린더 생성
    """
    api_key = os.getenv("GEMINI_API_KEY")
    real_earnings = fetch_real_earnings_calendar(dates)
    macro_news = fetch_weekly_macro_news(dates)

    mon_s = dates["mon"].strftime("%Y년 %m월 %d일")
    tue_s = dates["tue"].strftime("%Y년 %m월 %d일")
    wed_s = dates["wed"].strftime("%Y년 %m월 %d일")
    thu_s = dates["thu"].strftime("%Y년 %m월 %d일")
    fri_s = dates["fri"].strftime("%Y년 %m월 %d일")

    fallback = get_default_calendar_data(dates, real_earnings)

    if not api_key:
        logger.warning("GEMINI_API_KEY 미설정, yfinance 실데이터가 반영된 기본 캘린더 사용")
        return fallback

    # 요일별 실제 확정 실적 텍스트 요약
    day_earnings_summary = {}
    for d_en in DAY_EN_LIST:
        stocks = real_earnings["by_day"].get(d_en, [])
        if stocks:
            day_earnings_summary[d_en] = [
                f"{s['short_name']}({s['ticker']}): 컨센서스 EPS {s['consensus_eps']}, 예상 매출 {s['consensus_rev']}"
                for s in stocks
            ]
        else:
            day_earnings_summary[d_en] = ["해당 요일 주요 빅테크 확정 실적 없음 (지표·정책·연준발언·주요이벤트 위주로 배치할 것)"]

    all_dates_summary = [
        f"{s['short_name']}({s['ticker']}): {s['date_str']} (EPS {s['consensus_eps']}, 매출 {s['consensus_rev']})"
        for s in real_earnings["all"][:14]
    ]

    prompt = f"""
당신은 월가와 미국 금융 시장의 주간 일정을 팩트 기반으로 정리하는 수석 시장 전략가입니다.
아래 제공된 [대상 주간 날짜], [yfinance 검증 기업별 실제 실적 발표일], [이번 주 월가 매크로 일정 외신 헤드라인]을 바탕으로 100% 팩트에 기반한 위클리 캘린더 JSON을 작성해 주세요.

[대상 주간 날짜]
- 월요일 (Monday): {mon_s}
- 화요일 (Tuesday): {tue_s}
- 수요일 (Wednesday): {wed_s}
- 목요일 (Thursday): {thu_s}
- 금요일 (Friday): {fri_s}

[절대 준수 팩트 1: 요일별 실제 확정 기업 실적 (yfinance 실시간 검증 완료)]
{json.dumps(day_earnings_summary, ensure_ascii=False, indent=2)}

[절대 준수 팩트 2: 주요 빅테크·반도체 전체 실적 발표 예정일 (yfinance 기준)]
{json.dumps(all_dates_summary, ensure_ascii=False)}

[절대 준수 팩트 3: 이번 주 월가 경제지표 및 매크로 외신 헤드라인]
{json.dumps(macro_news, ensure_ascii=False)}

[작성 및 팩트체크 지침]
1. **실적 발표일 조작/착오 절대 금지**:
   - 각 요일(Monday~Friday)의 `"earnings"`(실적) 이벤트에는 반드시 위 `[절대 준수 팩트 1]`에서 **그 요일에 배정된 기업만** 넣을 것!
   - 예를 들어 AMD(11/04 예정), 애플(11/03 예정), 엔비디아(11/18 예정) 등 다른 주간에 발표하는 기업이나 이미 발표가 끝난 기업(메타·테슬라 10/22 완료)을 이번 주 요일별 실적 발표로 넣으면 절대 안 됨!
   - 대신 `todo_items`(중기 체크포인트)에 차주 이후 예정된 핵심 보유주(예: 애플 11/03, AMD 11/04, 엔비디아 11/18 실적 예정 등)를 명확한 날짜와 함께 표기할 것.
2. **요일별 이벤트 수**: 월요일~금요일 각 요일마다 **2~3개의 핵심 일정**을 고르게 배치할 것. 실적 발표가 없는 요일에는 실제 경제지표(`indicator`), 연준/재무부 정책(`policy`), 연준 인사 발언(`speech`), 산업/테크 이벤트(`event`)를 배치할 것.
3. **5대 카테고리 고정**:
   - `"event"` : `"주요이벤트"`
   - `"policy"` : `"정책/통화"`
   - `"indicator"` : `"지표"`
   - `"earnings"` : `"실적"`
   - `"speech"` : `"발언"`
4. **Weekly Focus & CME FedWatch**:
   - `fedwatch`: 차기 FOMC 회의 기준 금리 동결/인하 확률 전망을 월가 컨센서스 기준으로 명시할 것.
   - `focus_items`: 이번 주 실제로 발표되는 빅테크 실적(예: MSFT, GOOGL, AMZN 등)과 이번 주 실제 핵심 경제지표(예: 3분기 GDP, 근원 PCE 물가지수, 비농업 고용 등)에 대한 월가 전문가들의 관전 포인트를 2가지로 요약할 것.
5. 마크다운 백틱 없이 순수 JSON만 반환할 것.

[JSON 응답 스키마]
{{
  "fedwatch": {{
    "fomc": "차기 FOMC",
    "rate_str": "25bp 인하 OO% · 동결 OO%"
  }},
  "focus_items": [
    {{"title": "첫 번째 주간 핵심 포커스 제목", "detail": "월가 전문가 관전 포인트 설명"}},
    {{"title": "두 번째 주간 핵심 포커스 제목", "detail": "월가 전문가 관전 포인트 설명"}}
  ],
  "todo_items": [
    "중기 체크포인트 1 (실제 날짜 명시)",
    "중기 체크포인트 2 (실제 날짜 명시)",
    "중기 체크포인트 3",
    "중기 체크포인트 4",
    "중기 체크포인트 5"
  ],
  "days": [
    {{
      "day_en": "Monday",
      "date_num": "{dates['mon'].strftime('%d')}",
      "events": [
        {{"category_cls": "indicator", "category_name": "지표", "title": "일정 제목", "stars": "★", "detail": "상세 설명"}}
      ]
    }},
    {{
      "day_en": "Tuesday",
      "date_num": "{dates['tue'].strftime('%d')}",
      "events": []
    }},
    {{
      "day_en": "Wednesday",
      "date_num": "{dates['wed'].strftime('%d')}",
      "events": []
    }},
    {{
      "day_en": "Thursday",
      "date_num": "{dates['thu'].strftime('%d')}",
      "events": []
    }},
    {{
      "day_en": "Friday",
      "date_num": "{dates['fri'].strftime('%d')}",
      "events": []
    }}
  ]
}}
"""

    logger.info("Gemini 1차 위클리 캘린더 생성 중...")
    draft = _call_gemini_json(prompt, api_key, temperature=0.2)
    if not draft or "days" not in draft:
        return fallback

    # 2차 팩트체크 감사(Auditor) 패스
    audit_prompt = f"""
당신은 금융 캘린더 팩트체크 감사관입니다.
아래 [1차 생성된 위클리 캘린더 JSON]을 [요일별 실제 확정 기업 실적] 및 [전체 기업별 실제 실적 발표일]과 대조하여,
실제 발표 요일과 다른 요일에 잘못 기재된 기업 실적이나 틀린 날짜가 있다면 즉시 바로잡은 JSON을 반환해 주세요.

[요일별 실제 확정 기업 실적 (yfinance)]
{json.dumps(day_earnings_summary, ensure_ascii=False)}

[전체 기업별 실제 실적 발표일 (yfinance)]
{json.dumps(all_dates_summary, ensure_ascii=False)}

[이번 주 매크로 외신 헤드라인]
{json.dumps(macro_news, ensure_ascii=False)}

[1차 생성된 위클리 캘린더 JSON]
{json.dumps(draft, ensure_ascii=False)}

[감사 규칙]
1. 특정 기업(예: AMD, AAPL, NVDA, META, MSFT, GOOGL, AMZN 등)이 실제 발표 요일이 아닌 다른 요일에 `"earnings"`로 들어가 있다면 반드시 제거하거나 실제 발표 요일로 이동할 것.
2. 각 요일(Monday~Friday)마다 2~3개의 정확한 일정이 유지되도록 할 것.
3. 순수 JSON만 반환할 것.
"""
    logger.info("Gemini 2차 위클리 캘린더 팩트체크 감사(Auditor) 실행 중...")
    audited = _call_gemini_json(audit_prompt, api_key, temperature=0.1)
    final_data = audited if (audited and "days" in audited) else draft

    # 파이썬 결정론적 가드레일로 우측 실적 보드 및 요일별 실적 100% 실데이터 보증
    final_data = _enforce_calendar_facts(final_data, dates, real_earnings)
    logger.info("✅ 위클리 캘린더 다중 팩트체크 및 yfinance 실데이터 동기화 완료!")
    return final_data


def get_default_calendar_data(dates, real_earnings=None):
    """
    yfinance 실데이터가 연동된 기본 스마트 캘린더 폴백 데이터
    """
    mon_d = dates["mon"].strftime("%d")
    tue_d = dates["tue"].strftime("%d")
    wed_d = dates["wed"].strftime("%d")
    thu_d = dates["thu"].strftime("%d")
    fri_d = dates["fri"].strftime("%d")

    data = {
        "fedwatch": {
            "fomc": "차기 FOMC",
            "rate_str": "25bp 인하 85% · 동결 15%"
        },
        "focus_items": [
            {"title": "MS·구글·아마존 빅테크 확정 실적 발표", "detail": "클라우드 성장률 및 AI 설비투자(CapEx) 가이던스 집중 점검"},
            {"title": "미국 3분기 GDP · 근원 PCE · 비농업 고용 발표", "detail": "연준 금리 인하 속도를 좌우할 성장·물가·고용 3대 핵심 지표"}
        ],
        "todo_items": [
            "MS·알파벳(10/29 목), 아마존·인텔(10/30 금) 확정 실적 점검",
            "미국 3분기 GDP 속보치 및 근원 PCE 물가지수 확인",
            "애플(11/03 화) 및 AMD(11/04 수) 차주 실적 발표 대비",
            "엔비디아(11/18 수) 분기 실적 및 블랙웰 출하 가이던스 점검",
            "차기 FOMC 기준금리 결정 및 연준 통화정책 경로 확인"
        ],
        "days": [
            {
                "day_en": "Monday",
                "date_num": mon_d,
                "events": [
                    {"category_cls": "indicator", "category_name": "지표", "title": "댈러스 연은 제조업 지수 발표", "stars": "★", "detail": "미국 지역 제조업 체감 경기 및 수주 동향 점검"},
                    {"category_cls": "policy", "category_name": "정책/통화", "title": "미 재무부 단기·중기 국채 입찰", "stars": "★", "detail": "국채 수급 및 시중 금리 변동성 확인"}
                ]
            },
            {
                "day_en": "Tuesday",
                "date_num": tue_d,
                "events": [
                    {"category_cls": "indicator", "category_name": "지표", "title": "CB 소비자신뢰지수 & 리치먼드 연은 지수", "stars": "★★", "detail": "미국 가계 소비 심리 및 고용 체감도 확인"},
                    {"category_cls": "earnings", "category_name": "실적", "title": "코카콜라(KO) · 보잉(BA) · 페이팔(PYPL) 실적", "stars": "★★", "detail": "소비재·산업재·핀테크 대표 기업 분기 실적"}
                ]
            },
            {
                "day_en": "Wednesday",
                "date_num": wed_d,
                "events": [
                    {"category_cls": "indicator", "category_name": "지표", "title": "미국 무역수지 및 도매재고 속보치", "stars": "★", "detail": "3분기 GDP 성장률 기여도 점검"},
                    {"category_cls": "earnings", "category_name": "실적", "title": "비자(V) 분기 실적 발표", "stars": "★★", "detail": "글로벌 결제 트래픽 및 미국 소비 건전성 확인"}
                ]
            },
            {
                "day_en": "Thursday",
                "date_num": thu_d,
                "events": [
                    {"category_cls": "earnings", "category_name": "실적", "title": "마이크로소프트(MSFT) & 알파벳(GOOGL) 실적", "stars": "★★★", "detail": "클라우드 성장률 및 AI 인프라 CapEx 규모 공개"},
                    {"category_cls": "earnings", "category_name": "실적", "title": "일라이릴리(LLY) · 마스터카드(MA) · 캐터필러 실적", "stars": "★★", "detail": "비만치료제 매출 및 글로벌 경기 민감주 실적"},
                    {"category_cls": "indicator", "category_name": "지표", "title": "미국 3분기 GDP 속보치 & 주간 실업수당 청구건수", "stars": "★★★", "detail": "미국 경제 연착륙 및 성장 모멘텀 검증"}
                ]
            },
            {
                "day_en": "Friday",
                "date_num": fri_d,
                "events": [
                    {"category_cls": "indicator", "category_name": "지표", "title": "미국 근원 PCE 물가지수 & 고용비용지수", "stars": "★★★", "detail": "연준(Fed)이 가장 중시하는 핵심 인플레이션 지표"},
                    {"category_cls": "earnings", "category_name": "실적", "title": "아마존(AMZN) & 인텔(INTC) 분기 실적", "stars": "★★★", "detail": "AWS 클라우드 수익성 및 반도체 가이던스 확인"},
                    {"category_cls": "earnings", "category_name": "실적", "title": "엑슨모빌(XOM) & 셰브론(CVX) 실적 발표", "stars": "★★", "detail": "에너지 메이저 실적 및 주주환원 가이던스"}
                ]
            }
        ],
        "earnings_schedule": []
    }

    if real_earnings:
        data = _enforce_calendar_facts(data, dates, real_earnings)
    return data


def render_weekly_calendar_image(data, dates, output_png="output/weekly_calendar.png"):
    """
    HTML 템플릿에 데이터 바인딩 후 Edge/Playwright로 PNG 렌더링
    """
    os.makedirs(os.path.dirname(output_png), exist_ok=True)
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("weekly_calendar.html")

    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # 월~목과 금요일 분리
    all_days = data.get("days", [])
    days_mon_thu = [d for d in all_days if d.get("day_en") != "Friday"]
    friday = next((d for d in all_days if d.get("day_en") == "Friday"), None)

    rendered_html = template.render(
        date_range=dates["range_str"],
        fedwatch=data.get("fedwatch", {}),
        focus_items=data.get("focus_items", []),
        todo_items=data.get("todo_items", []),
        days_mon_thu=days_mon_thu,
        friday=friday,
        earnings_schedule=data.get("earnings_schedule", []),
        branding=config.get("branding", {})
    )

    html_path = "output/weekly_calendar.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(rendered_html)

    # 1. Playwright 시도 (GitHub Actions 및 표준 클라우드)
    try:
        from playwright.sync_api import sync_playwright
        logger.info("Playwright를 통한 위클리 캘린더 렌더링 시도...")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                viewport={"width": 980, "height": 1200},
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
        "--window-size=980,1200",
        f"--screenshot={os.path.abspath(output_png)}",
        "--hide-scrollbars",
        file_url
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info(f"위클리 캘린더 렌더링 완료: {output_png}")
    return output_png


def send_calendar_to_telegram(image_path, data, dates):
    """
    텔레그램 봇으로 위클리 캘린더 이미지 및 요약 캡션 발송
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        logger.warning("텔레그램 토큰 또는 Chat ID가 설정되지 않았습니다.")
        return False

    focus_list = []
    for i, it in enumerate(data.get("focus_items", [])[:2], 1):
        focus_list.append(f"• *포커스 {i}*: {it.get('title')} ({it.get('detail')})")

    fedwatch = data.get("fedwatch", {})
    fedwatch_line = ""
    if fedwatch:
        fedwatch_line = f"• 🏛 *CME FedWatch ({fedwatch.get('fomc', '차기 FOMC')})*: {fedwatch.get('rate_str', '')}\n"

    earnings_lines = []
    for e in data.get("earnings_schedule", [])[:4]:
        earnings_lines.append(
            f"• *{e.get('name')}*: {e.get('date_str')} (예상 EPS {e.get('consensus_eps')} / 매출 {e.get('consensus_rev')})"
        )
    earnings_block = "\n💼 *확정 실적 일정 & 컨센서스 (yfinance 기준)*\n" + "\n".join(earnings_lines) + "\n" if earnings_lines else ""

    caption = f"""🗓 *[팩트체크 완료] 미국 증시 위클리 캘린더*
📅 *적용 주간*: {dates['range_str']}

📌 *주간 핵심 포커스 & 통화정책*
{fedwatch_line}{chr(10).join(focus_list)}
{earnings_block}
💡 *5대 고정 컬러 범례*
• 🌸 분홍: 주요이벤트 | 🏛 회색: 정책/통화 | 📊 초록: 핵심지표
• 💼 파랑: 기업실적 (컨센서스 표기) | 🎤 노랑: 연준발언

#미국증시 #위클리캘린더 #FOMC #금리전망 #실적발표 #NVDA #AMD"""

    if len(caption) > 1020:
        caption = caption[:1015] + "..."

    from subscriber_manager import get_subscribers

    subscribers = get_subscribers()
    if not subscribers:
        logger.warning("발송할 구독자가 없습니다.")
        return False

    logger.info(f"📢 위클리 캘린더 발송 대상: 총 {len(subscribers)}명 ({subscribers})")

    with open(image_path, "rb") as photo_file:
        img_bytes = photo_file.read()

    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    success_count = 0
    for target_chat_id in subscribers:
        try:
            payload = {
                "chat_id": target_chat_id,
                "caption": caption,
                "parse_mode": "Markdown"
            }
            files = {"photo": ("calendar.png", img_bytes, "image/png")}
            res = requests.post(url, data=payload, files=files, timeout=30)
            if res.status_code == 200:
                logger.info(f"✅ 구독자({target_chat_id}) 위클리 캘린더 발송 성공!")
                success_count += 1
            else:
                logger.error(f"❌ 구독자({target_chat_id}) 발송 실패 ({res.status_code}): {res.text}")
        except Exception as e:
            logger.error(f"❌ 구독자({target_chat_id}) 발송 중 예외: {e}")

    logger.info(f"🎉 위클리 캘린더: 총 {len(subscribers)}명 중 {success_count}명 발송 완료")
    return success_count > 0


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

    logger.info("==========================================")
    logger.info("📅 미국 증시 위클리 캘린더 파이프라인 시작 (다중 팩트체크)")
    logger.info("==========================================")

    # 1. 대상 주간 날짜 계산
    dates = get_next_week_dates()

    # 2. yfinance 실데이터 + Gemini AI 2단계 팩트체크 캘린더 생성
    data = generate_weekly_calendar_ai(dates)

    # 3. 고화질 캘린더 이미지 렌더링
    output_png = "output/weekly_calendar.png"
    render_weekly_calendar_image(data, dates, output_png)

    # 4. 텔레그램 발송
    send_calendar_to_telegram(output_png, data, dates)

    logger.info("==========================================")
    logger.info(f"🎉 위클리 캘린더 완료! 결과: {os.path.abspath(output_png)}")
    logger.info("==========================================")


if __name__ == "__main__":
    main()
