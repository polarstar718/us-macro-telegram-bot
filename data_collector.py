"""
미국 증시 및 매크로 지표 실시간 수집 모듈
- yfinance를 사용하여 주요 지수, 매크로 지표, 빅테크 M7 주가 수집
- Google News RSS를 통한 최신 미국 증시 헤드라인 수집
"""

import datetime
import json
import logging
import urllib.request
import xml.etree.ElementTree as ET
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def format_number(val, decimals=2, prefix="", suffix="", is_currency=False):
    if val is None:
        return "N/A"
    try:
        val = float(val)
        if is_currency:
            prefix = "$"
        if decimals == 0:
            formatted = f"{val:,.0f}"
        else:
            formatted = f"{val:,.{decimals}f}"
        return f"{prefix}{formatted}{suffix}"
    except Exception:
        return str(val)


def get_change_info(current, prev, is_yield=False):
    """
    등락폭 및 등락률, CSS 클래스(up/down/neutral), 기호(▲/▼/-) 계산
    """
    if current is None or prev is None or prev == 0:
        return {
            "diff_str": "0.00",
            "pct_str": "0.00%",
            "sign": "",
            "cls": "neutral",
            "is_up": False,
            "is_down": False
        }

    diff = current - prev
    pct = (diff / prev) * 100

    if abs(diff) < 1e-5:
        return {
            "diff_str": "0.00",
            "pct_str": "0.00%",
            "sign": "-",
            "cls": "neutral",
            "is_up": False,
            "is_down": False
        }

    if is_yield:
        # 국채금리의 경우 bp(0.01% = 1bp) 단위 표시
        bp = round(diff * 100)
        sign = "+" if bp > 0 else ""
        diff_str = f"{sign}{bp}bp"
        pct_str = f"{sign}{bp}bp"
    else:
        abs_diff = abs(diff)
        diff_str = f"{abs_diff:,.2f}"
        pct_str = f"{pct:+.2f}%"

    is_up = diff > 0
    cls = "up" if is_up else "down"
    symbol = "▲" if is_up else "▼"

    return {
        "diff": diff,
        "abs_diff": abs(diff),
        "pct": pct,
        "diff_str": diff_str,
        "pct_str": pct_str,
        "symbol": symbol,
        "cls": cls,
        "is_up": is_up,
        "is_down": not is_up
    }


def fetch_ticker_data(ticker_symbol, is_yield=False):
    """
    단일 티커의 최신 종가 및 변동률, 주간 변동률 조회
    """
    try:
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period="1mo")
        if hist.empty or len(hist) < 2:
            logger.warning(f"데이터 부족: {ticker_symbol}")
            return None

        current_close = float(hist['Close'].iloc[-1])
        prev_close = float(hist['Close'].iloc[-2])

        # 주간(5거래일) 변동률 계산
        if len(hist) >= 5:
            week_ago_close = float(hist['Close'].iloc[-5])
            week_pct = ((current_close - week_ago_close) / week_ago_close) * 100
            week_sign = "+" if week_pct > 0 else ""
            week_str = f"주간 {week_sign}{week_pct:.1f}%"
        else:
            week_str = ""

        change = get_change_info(current_close, prev_close, is_yield=is_yield)

        return {
            "symbol": ticker_symbol,
            "current": current_close,
            "prev": prev_close,
            "change": change,
            "week_str": week_str,
            "date": hist.index[-1].strftime("%Y.%m.%d")
        }
    except Exception as e:
        logger.error(f"티커 조회 실패 ({ticker_symbol}): {e}")
        return None


def fetch_news_headlines(query="US stock market economy", max_items=5):
    """
    Google News RSS를 통해 오늘 미국 증시의 주요 헤드라인 뉴스 수집 (출처 포함)
    """
    try:
        encoded_query = urllib.parse.quote(query)
        rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US:en"
        req = urllib.request.Request(rss_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            xml_data = response.read()

        root = ET.fromstring(xml_data)
        items = []
        for item in root.findall(".//item")[:max_items]:
            title = item.find("title").text if item.find("title") is not None else ""
            pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
            source = item.find("source").text if item.find("source") is not None else "Financial Media"
            items.append({"title": title, "published": pub_date, "source": source})
        return items
    except Exception as e:
        logger.warning(f"뉴스 수집 실패 ({query}): {e}")
        return []


def fetch_expert_market_opinions():
    """
    월가 주요 투자은행(IB), 애널리스트, 글로벌 경제 매체의 최신 시장 진단 및 전문가 의견 다중 수집
    """
    queries = [
        ("Wall Street strategist stock market S&P 500 Nasdaq Goldman Morgan Stanley JPMorgan Citi BofA when:2d", 5),
        ("US stock market close today S&P 500 Dow Nasdaq rally drop why when:1d", 5),
        ("Nvidia NVDA AMD AI chips analyst target price Wall Street commentary when:3d", 5),
        ("Federal Reserve Treasury yields inflation dollar market impact Reuters Bloomberg CNBC MarketWatch when:2d", 4),
    ]
    seen_titles = set()
    opinions = []
    for q, limit in queries:
        for item in fetch_news_headlines(q, max_items=limit):
            title = item.get("title", "").strip()
            if title and title not in seen_titles:
                seen_titles.add(title)
                opinions.append(item)
    return opinions


def fetch_verified_upcoming_earnings(tickers=None):
    """
    yfinance Ticker.calendar를 통해 주요 빅테크/반도체 기업의 실제 확정(또는 컨센서스) 실적 발표일과 예상 EPS/매출 조회
    """
    from concurrent.futures import ThreadPoolExecutor

    if not tickers:
        tickers = [
            ("NVDA", "엔비디아"), ("AMD", "AMD"), ("MSFT", "마이크로소프트"),
            ("GOOGL", "알파벳(구글)"), ("AMZN", "아마존"), ("AAPL", "애플"),
            ("META", "메타"), ("TSLA", "테슬라"), ("AVGO", "브로드컴"),
            ("MU", "마이크론"), ("INTC", "인텔"), ("ARM", "ARM")
        ]

    def _fetch_one(pair):
        sym, kr_name = pair
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

            return {
                "ticker": sym,
                "name": kr_name,
                "earnings_date": edate.strftime("%Y-%m-%d") if edate else "미정",
                "consensus_eps": eps_str,
                "consensus_rev": rev_str
            }
        except Exception as e:
            logger.debug(f"실적 일정 조회 실패 ({sym}): {e}")
            return None

    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(_fetch_one, tickers):
            if res:
                results.append(res)
    return results


def fetch_fear_and_greed_index():
    """
    CNN Fear & Greed Index (0~100) 수치 및 상태(Extreme Fear ~ Extreme Greed) 수집
    """
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    rating_kr_map = {
        "extreme fear": "극도의 공포",
        "fear": "공포",
        "neutral": "중립",
        "greed": "탐욕",
        "extreme greed": "극도의 탐욕"
    }
    color_map = {
        "extreme fear": "#DC2626", # 짙은 빨강
        "fear": "#EA580C",        # 주황
        "neutral": "#64748B",     # 슬레이트
        "greed": "#16A34A",       # 초록
        "extreme greed": "#059669"# 에메랄드
    }
    try:
        import requests
        res = requests.get(url, headers=headers, timeout=6)
        if res.status_code == 200:
            data = res.json()
            fg = data.get("fear_and_greed", {})
            score = round(float(fg.get("score", 50)))
            rating = str(fg.get("rating", "neutral")).lower()
            prev_close = round(float(fg.get("previous_close", score)))
            rating_kr = rating_kr_map.get(rating, "중립")
            color = color_map.get(rating, "#64748B")

            return {
                "score": score,
                "rating": rating.title(),
                "rating_kr": rating_kr,
                "color": color,
                "prev_close": prev_close,
                "status_text": f"{score}점 · {rating_kr}"
            }
        else:
            raise RuntimeError(f"HTTP {res.status_code}")
    except Exception as e:
        logger.warning(f"CNN Fear & Greed 지수 수집 실패 ({e}), 안전 기본값 사용")
        return {
            "score": 50,
            "rating": "Neutral",
            "rating_kr": "중립",
            "color": "#64748B",
            "prev_close": 50,
            "status_text": "50점 · 중립"
        }


def fetch_after_market_news(watch_tickers=None):
    """
    M7 및 주요 반도체 종목의 장 마감 후 실적 발표 및 시간외 거래(After-market) 팩트체크
    [표시 기준]
    1. 실적 발표 여부와 무관하게 시간외 등락률이 ±5.0% 이상인 종목
    2. 최근 마감된 거래일(또는 당일)에 분기 실적(Earnings)을 발표한 종목 (±5% 미만이어도 표시)
    [검증 방식]
    - yfinance의 postMarketChangePercent 단위 오류 방지를 위해 (시간외현재가 - 정규장종가) / 정규장종가 * 100 으로 직접 계산
    - postMarketChangePercent 원시값 및 타임스탬프(postMarketTime >= regularMarketTime)와 교차 검증 수행
    """
    from concurrent.futures import ThreadPoolExecutor

    if not watch_tickers:
        watch_tickers = [
            "NVDA", "AMD", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "TSLA",
            "AVGO", "MU", "INTC", "ARM", "TSM", "QCOM"
        ]

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    recent_dates = {
        (now_utc - datetime.timedelta(days=d)).date()
        for d in range(0, 2)
    }

    def _check_ticker(ticker_symbol):
        try:
            t = yf.Ticker(ticker_symbol)
            info = t.info or {}
            cal = t.calendar or {}

            # 1) 분기 실적 발표일 여부 확인 (yfinance calendar 기준)
            edates = cal.get("Earnings Date", [])
            is_earnings_today = any(ed in recent_dates for ed in edates if isinstance(ed, datetime.date))

            # 2) 정규장 종가 및 시간외 현재가 조회
            reg_price = info.get("regularMarketPrice") or info.get("currentPrice")
            post_price = info.get("postMarketPrice")
            reg_time = info.get("regularMarketTime")
            post_time = info.get("postMarketTime")
            raw_post_pct = info.get("postMarketChangePercent")

            # 시간외 가격이 유효하고 최신 세션 데이터인지 타임스탬프 검증
            has_valid_post = False
            direct_pct = 0.0
            if post_price and reg_price and float(reg_price) > 0:
                post_price_f = float(post_price)
                reg_price_f = float(reg_price)
                # (시간외 현재가 - 정규장 종가)로 직접 등락률(%) 계산
                direct_pct = ((post_price_f - reg_price_f) / reg_price_f) * 100.0

                # 타임스탬프 신선도 검증 (post_time이 reg_time 이후이고 36시간 이내인지)
                is_fresh = True
                if post_time and reg_time:
                    if post_time < reg_time or (now_utc.timestamp() - post_time) > 36 * 3600:
                        is_fresh = False

                # raw_post_pct와 교차 검증 (yfinance는 이미 % 단위이거나 소수점일 수 있으므로 둘 다 대조)
                if raw_post_pct is not None:
                    raw_f = float(raw_post_pct)
                    # raw_f 자체가 direct_pct와 가깝거나, raw_f*100이 direct_pct와 가까우면 통과
                    diff_as_pct = abs(raw_f - direct_pct)
                    diff_as_dec = abs((raw_f * 100.0) - direct_pct)
                    if min(diff_as_pct, diff_as_dec) > 1.0:
                        logger.warning(
                            f"[{ticker_symbol}] 시간외 등락률 교차검증 불일치 (직접계산={direct_pct:.2f}%, raw={raw_f}) -> 직접계산값 사용"
                        )

                if is_fresh and abs(direct_pct) < 50.0:
                    has_valid_post = True

            # 조건 1: 시간외 ±5.0% 이상 변동
            if has_valid_post and abs(direct_pct) >= 5.0:
                cls = "up" if direct_pct > 0 else "down"
                sign = "+" if direct_pct > 0 else ""
                tag_prefix = "실적·시간외" if is_earnings_today else "시간외 급변동"
                return {
                    "ticker": ticker_symbol,
                    "price": f"${float(post_price):,.2f}",
                    "reg_price": f"${float(reg_price):,.2f}",
                    "pct": round(direct_pct, 2),
                    "pct_str": f"{sign}{direct_pct:.2f}%",
                    "cls": cls,
                    "is_earnings": is_earnings_today,
                    "text": f"{ticker_symbol} ({tag_prefix}) {sign}{direct_pct:.2f}% (정규 ${float(reg_price):,.2f} → 시간외 ${float(post_price):,.2f})"
                }

            # 조건 2: 분기 실적 발표일인 종목 (±5% 미만 변동이어도 표기)
            if is_earnings_today:
                if has_valid_post:
                    cls = "up" if direct_pct > 0 else "down" if direct_pct < 0 else "neutral"
                    sign = "+" if direct_pct > 0 else ""
                    return {
                        "ticker": ticker_symbol,
                        "price": f"${float(post_price):,.2f}",
                        "reg_price": f"${float(reg_price):,.2f}",
                        "pct": round(direct_pct, 2),
                        "pct_str": f"{sign}{direct_pct:.2f}%",
                        "cls": cls,
                        "is_earnings": True,
                        "text": f"{ticker_symbol} [분기실적 발표] 시간외 {sign}{direct_pct:.2f}% (${float(post_price):,.2f})"
                    }
                elif reg_price:
                    eps_est = cal.get("Earnings Average")
                    eps_txt = f" (컨센서스 EPS ${float(eps_est):.2f})" if eps_est is not None else ""
                    return {
                        "ticker": ticker_symbol,
                        "price": f"${float(reg_price):,.2f}",
                        "reg_price": f"${float(reg_price):,.2f}",
                        "pct": 0.0,
                        "pct_str": "실적발표",
                        "cls": "neutral",
                        "is_earnings": True,
                        "text": f"{ticker_symbol} [분기실적 발표일] 종가 ${float(reg_price):,.2f}{eps_txt}"
                    }
        except Exception as e:
            logger.debug(f"시간외 거래/실적 조회 중 예외 ({ticker_symbol}): {e}")
        return None

    notable_moves = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for item in ex.map(_check_ticker, watch_tickers):
            if item:
                notable_moves.append(item)

    if notable_moves:
        # 변동폭 절대값 큰 순서 + 실적 발표 우선 정렬
        notable_moves.sort(key=lambda x: (x.get("is_earnings", False), abs(x.get("pct", 0.0))), reverse=True)
        combined_text = " · ".join([m["text"] for m in notable_moves[:3]])
        return {
            "has_event": True,
            "title": "⚡ 장 마감 후 실적 / 시간외 속보 (교차검증 완료)",
            "content": combined_text,
            "items": notable_moves
        }
    return None


def collect_all_market_data(config_path="config.json"):
    """
    설정 파일(config.json)에 정의된 모든 지수, 매크로, 빅테크 종목 데이터 일괄 수집
    """
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    now = datetime.datetime.now()
    # 날짜 서식 (예: 뉴욕 마감 기준 2026.10.23 (금))
    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    date_str = f"{now.strftime('%Y.%m.%d')} ({weekday_kr})"

    logger.info("미국 주요 지수 데이터 수집 중...")
    indices_data = []
    for item in config.get("indices", []):
        data = fetch_ticker_data(item["ticker"])
        if data:
            decimals = 2
            if "비트코인" in item["name"]:
                formatted_price = f"${data['current']:,.0f}"
            else:
                formatted_price = f"{data['current']:,.2f}"
            indices_data.append({
                "name": item["name"],
                "ticker": item["ticker"],
                "primary": item.get("primary", False),
                "price": formatted_price,
                "raw_price": data["current"],
                "diff_str": data["change"]["diff_str"],
                "pct_str": data["change"]["pct_str"],
                "symbol": data["change"]["symbol"],
                "cls": data["change"]["cls"],
                "week_str": data["week_str"]
            })

    logger.info("매크로 핵심 지표 데이터 수집 중...")
    macro_data = []
    for item in config.get("macro", []):
        is_yield = item.get("is_yield", False)
        data = fetch_ticker_data(item["ticker"], is_yield=is_yield)
        if data:
            unit = item.get("unit", "")
            if is_yield:
                formatted_price = f"{data['current']:.2f}%"
                change_str = data["change"]["pct_str"]
            elif unit == "$":
                if "금" in item["name"]:
                    formatted_price = f"${data['current']:,.0f}"
                else:
                    formatted_price = f"${data['current']:,.1f}"
                change_str = data["change"]["pct_str"]
            elif unit == "원":
                formatted_price = f"{data['current']:,.1f}원"
                change_diff = data["change"]["diff"]
                change_str = f"{change_diff:+.1f}원"
            else:
                formatted_price = f"{data['current']:,.1f}"
                change_str = data["change"]["pct_str"]

            macro_data.append({
                "name": item["name"],
                "ticker": item["ticker"],
                "price": formatted_price,
                "change_str": change_str,
                "cls": data["change"]["cls"]
            })

    logger.info("핵심 집중 투자 종목(NVDA, AMD) 데이터 수집 중...")
    focus_data = []
    for item in config.get("focus_stocks", []):
        data = fetch_ticker_data(item["ticker"])
        if data:
            focus_data.append({
                "name": item["name"],
                "ticker": item["ticker"],
                "tag": item.get("tag", ""),
                "color": item.get("color", "#16A34A"),
                "price": f"${data['current']:,.2f}",
                "diff_str": data["change"]["diff_str"],
                "pct_str": data["change"]["pct_str"],
                "symbol": data["change"]["symbol"],
                "cls": data["change"]["cls"],
                "week_str": data["week_str"]
            })

    logger.info("빅테크 M7 종목 데이터 수집 중...")
    m7_data = []
    for item in config.get("m7", []):
        data = fetch_ticker_data(item["ticker"])
        if data:
            m7_data.append({
                "name": item["name"],
                "ticker": item["ticker"],
                "price": f"{data['current']:,.2f}",
                "pct_str": data["change"]["pct_str"],
                "cls": data["change"]["cls"]
            })

    logger.info("최신 미국 증시, 반도체 뉴스 및 월가 전문가(IB·애널리스트) 의견 수집 중...")
    news = fetch_expert_market_opinions()

    logger.info("주요 빅테크 및 반도체 확정 실적 발표 일정(yfinance) 검증 중...")
    verified_earnings = fetch_verified_upcoming_earnings()

    logger.info("CNN Fear & Greed 공포·탐욕 지수 수집 중...")
    fear_greed = fetch_fear_and_greed_index()

    logger.info("장 마감 후 시간외 거래(After-market ±5% 기준) 및 당일 분기실적 팩트체크 중...")
    after_market = fetch_after_market_news()

    return {
        "date_str": date_str,
        "indices": indices_data,
        "macro": macro_data,
        "fear_greed": fear_greed,
        "focus_stocks": focus_data,
        "m7": m7_data,
        "after_market": after_market,
        "verified_earnings": verified_earnings,
        "news": news,
        "config": config
    }


if __name__ == "__main__":
    data = collect_all_market_data()
    print("=== 수집 완료 ===")
    print(f"날짜: {data['date_str']}")
    print(f"지수: {len(data['indices'])}개")
    print(f"매크로: {len(data['macro'])}개")
    print(f"M7: {len(data['m7'])}개")
    print(f"뉴스: {len(data['news'])}개")
