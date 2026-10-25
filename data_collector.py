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
    Google News RSS를 통해 오늘 미국 증시의 주요 헤드라인 뉴스 수집
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
            items.append({"title": title, "published": pub_date})
        return items
    except Exception as e:
        logger.warning(f"뉴스 수집 실패: {e}")
        return []


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
    실적 발표 기간 주요 종목(M7, 반도체 등)의 시간외 거래 등락률 또는 주요 어닝 속보 확인
    유의미한 변동(±1.8% 이상)이 있을 때만 조건부 반환
    """
    if not watch_tickers:
        watch_tickers = ["NVDA", "AMD", "TSLA", "AAPL", "MSFT", "GOOGL", "AMZN", "META"]

    notable_moves = []
    try:
        for ticker_symbol in watch_tickers[:5]:  # 빠른 응답을 위해 상위 5개 우선 점검
            t = yf.Ticker(ticker_symbol)
            info = t.info or {}
            post_price = info.get("postMarketPrice")
            post_pct = info.get("postMarketChangePercent")

            if post_price and post_pct is not None and abs(post_pct) >= 0.018:
                pct_val = post_pct * 100
                cls = "up" if pct_val > 0 else "down"
                sign = "+" if pct_val > 0 else ""
                notable_moves.append({
                    "ticker": ticker_symbol,
                    "price": f"${post_price:,.2f}",
                    "pct_str": f"{sign}{pct_val:.2f}%",
                    "cls": cls,
                    "text": f"{ticker_symbol} 시간외 {sign}{pct_val:.2f}% (${post_price:,.2f})"
                })
    except Exception as e:
        logger.debug(f"시간외 거래 조회 중 예외 ({e})")

    if notable_moves:
        combined_text = " · ".join([m["text"] for m in notable_moves[:2]])
        return {
            "has_event": True,
            "title": "⚡ 장 마감 후 실적 / 시간외 속보",
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

    logger.info("최신 미국 증시 및 반도체 뉴스 헤드라인 수집 중...")
    news = fetch_news_headlines("US stock market economy", max_items=4)
    semicon_news = fetch_news_headlines("Nvidia AMD semiconductor AI chips", max_items=3)
    news.extend(semicon_news)

    logger.info("CNN Fear & Greed 공포·탐욕 지수 수집 중...")
    fear_greed = fetch_fear_and_greed_index()

    logger.info("장 마감 후 시간외 거래(After-market) 및 어닝 점검 중...")
    after_market = fetch_after_market_news(["NVDA", "AMD", "TSLA", "AAPL", "MSFT"])

    return {
        "date_str": date_str,
        "indices": indices_data,
        "macro": macro_data,
        "fear_greed": fear_greed,
        "focus_stocks": focus_data,
        "m7": m7_data,
        "after_market": after_market,
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
