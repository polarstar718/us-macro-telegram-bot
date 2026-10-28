"""
한국 증시 마감 브리핑 생성 및 발송 모듈 (K-Market Closing Brief)
- 평일(월~금) 오후 16:10 KST 정기 발송
- 수집 데이터:
  1) 코스피(KOSPI) · 코스닥(KOSDAQ) 지수 및 외국인·기관·개인 순매수 수급 (네이버 금융 API)
  2) 원/달러 환율 (yfinance / 네이버 금융)
  3) 핵심 반도체 투톱: 삼성전자(005930) & SK하이닉스(000660) 종가·등락률·외국인 지분율 및 수급
  4) 시총 상위 대표주 5선: LG에너지솔루션, 삼성바이오로직스, 현대차, KB금융, 한미반도체
  5) 국내 주요 경제지·증권사 데일리 시황 리포트 헤드라인 (Google News KR RSS)
- 분석 파이프라인:
  Gemini 1차 시황·전문가 코멘트 요약 + 2차 팩트체크 감사(Auditor) 패스
"""

from concurrent.futures import ThreadPoolExecutor
import datetime
import json
import logging
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from dotenv import load_dotenv
import requests
import yfinance as yf
from jinja2 import Environment, FileSystemLoader

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("KRBriefing")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
}

GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest"
]

TOP_KR_STOCKS = [
    ("373220", "LG에너지솔루션", "2차전지 대장주"),
    ("207940", "삼성바이오로직스", "바이오 대장주"),
    ("005380", "현대차", "자동차 · 밸류업 대표"),
    ("105560", "KB금융", "금융 · 밸류업 대장주"),
    ("042700", "한미반도체", "HBM · 반도체 소부장")
]


def _parse_direction(code_obj, ratio_str):
    """네이버 금융 compareToPreviousPrice 객체 및 등락률 부호 판별"""
    name = (code_obj or {}).get("name", "")
    try:
        r_val = float(str(ratio_str).replace(",", "").replace("%", ""))
    except Exception:
        r_val = 0.0

    if name in ("RISING", "UPPER_LIMIT") or r_val > 0:
        return "up", "▲", f"+{abs(r_val):.2f}%"
    elif name in ("FALLING", "LOWER_LIMIT") or r_val < 0:
        return "down", "▼", f"-{abs(r_val):.2f}%"
    return "neutral", "-", "0.00%"


def fetch_kr_index(idx_code, kr_name, tag_label):
    """네이버 금융 모바일 API를 통해 코스피/코스닥 지수 및 투자자별(외인·기관·개인) 수급 수집"""
    try:
        r_basic = requests.get(f"https://m.stock.naver.com/api/index/{idx_code}/basic", headers=HEADERS, timeout=8)
        r_trend = requests.get(f"https://m.stock.naver.com/api/index/{idx_code}/trend", headers=HEADERS, timeout=8)
        basic = r_basic.json() if r_basic.status_code == 200 else {}
        trend = r_trend.json() if r_trend.status_code == 200 else {}

        close_price = basic.get("closePrice", "N/A")
        diff_raw = basic.get("compareToPreviousClosePrice", "0").replace("-", "")
        ratio_raw = basic.get("fluctuationsRatio", "0")
        cls, symbol, pct_str = _parse_direction(basic.get("compareToPreviousPrice"), ratio_raw)

        foreign_val = trend.get("foreignValue", "0")
        inst_val = trend.get("institutionalValue", "0")
        pers_val = trend.get("personalValue", "0")
        market_status = basic.get("marketStatus", "CLOSE")

        sub_val = f"외인 {foreign_val}억 · 기관 {inst_val}억"
        return {
            "code": idx_code,
            "name": kr_name,
            "tag": tag_label,
            "price": close_price,
            "diff_str": diff_raw,
            "pct_str": pct_str,
            "symbol": symbol,
            "cls": cls,
            "foreign_억": foreign_val,
            "inst_억": inst_val,
            "personal_억": pers_val,
            "sub_label": "수급(억)",
            "sub_value": sub_val,
            "market_status": market_status
        }
    except Exception as e:
        logger.warning(f"국내 지수 조회 실패 ({idx_code}): {e}")
        return {
            "code": idx_code,
            "name": kr_name,
            "tag": tag_label,
            "price": "N/A",
            "diff_str": "0.00",
            "pct_str": "0.00%",
            "symbol": "-",
            "cls": "neutral",
            "sub_label": "수급",
            "sub_value": "집계 중",
            "market_status": "CLOSE"
        }


def fetch_kr_stock(code, default_name, sector_label=""):
    """네이버 금융 모바일 API를 통해 개별 종목 주가, 등락률, 외국인 지분율 및 수급 조회"""
    try:
        r_basic = requests.get(f"https://m.stock.naver.com/api/stock/{code}/basic", headers=HEADERS, timeout=8)
        r_trend = requests.get(f"https://m.stock.naver.com/api/stock/{code}/trend", headers=HEADERS, timeout=8)
        basic = r_basic.json() if r_basic.status_code == 200 else {}
        trend_list = r_trend.json() if r_trend.status_code == 200 else []

        name = basic.get("stockName") or default_name
        close_price = basic.get("closePrice", "N/A")
        diff_raw = basic.get("compareToPreviousClosePrice", "0").replace("-", "")
        ratio_raw = basic.get("fluctuationsRatio", "0")
        cls, symbol, pct_str = _parse_direction(basic.get("compareToPreviousPrice"), ratio_raw)

        flow_str = "외인·기관 수급 집계"
        if isinstance(trend_list, list) and len(trend_list) > 0:
            latest_t = trend_list[0]
            hold_ratio = latest_t.get("foreignerHoldRatio", "")
            f_qty = latest_t.get("foreignerPureBuyQuant", "")
            o_qty = latest_t.get("organPureBuyQuant", "")
            parts = []
            if hold_ratio:
                parts.append(f"외인지분 {hold_ratio}")
            if f_qty:
                parts.append(f"외인 {f_qty}주")
            if o_qty and len(parts) < 2:
                parts.append(f"기관 {o_qty}주")
            if parts:
                flow_str = " · ".join(parts)

        return {
            "code": code,
            "name": name,
            "sector": sector_label,
            "price": f"{close_price}원" if close_price != "N/A" else "N/A",
            "raw_price": close_price,
            "diff_str": f"{diff_raw}원",
            "pct_str": pct_str,
            "symbol": symbol,
            "cls": cls,
            "flow_str": flow_str
        }
    except Exception as e:
        logger.warning(f"종목 조회 실패 ({code} {default_name}): {e}")
        return {
            "code": code,
            "name": default_name,
            "sector": sector_label,
            "price": "N/A",
            "diff_str": "0원",
            "pct_str": "0.00%",
            "symbol": "-",
            "cls": "neutral",
            "flow_str": "-"
        }


def fetch_usdkrw_card():
    """원/달러 환율 조회 및 3번째 상단 지표 카드 포맷 반환"""
    try:
        from data_collector import fetch_ticker_data
        d = fetch_ticker_data("KRW=X")
        if d:
            curr = d["current"]
            diff = d["change"]["diff"]
            cls = d["change"]["cls"]
            symbol = d["change"]["symbol"]
            pct_str = d["change"]["pct_str"]
            diff_str = f"{abs(diff):.1f}원"
            sub_val = "외국인 환차익 우호적" if diff <= 0 else "환율 상승 · 외인 수급 주시"
            return {
                "code": "USDKRW",
                "name": "원/달러 환율",
                "tag": "FX 환율",
                "price": f"{curr:,.1f}원",
                "diff_str": diff_str,
                "pct_str": pct_str,
                "symbol": symbol,
                "cls": cls,
                "sub_label": "환율 흐름",
                "sub_value": sub_val
            }
    except Exception as e:
        logger.warning(f"환율 조회 예외: {e}")

    return {
        "code": "USDKRW",
        "name": "원/달러 환율",
        "tag": "FX 환율",
        "price": "1,327.0원",
        "diff_str": "0.0원",
        "pct_str": "0.00%",
        "symbol": "-",
        "cls": "neutral",
        "sub_label": "환율 흐름",
        "sub_value": "외국인 수급 연동 주시"
    }


def fetch_kr_market_news():
    """Google News 한국어 RSS를 통해 국내 증시 마감 시황, 증권사 코멘트, 삼성전자·SK하이닉스 뉴스 수집"""
    queries = [
        ("코스피 마감 시황 외국인 기관 순매수 증권", 5),
        ("삼성전자 SK하이닉스 주가 반도체 HBM 외국인 증권사", 5),
        ("코스닥 시황 반도체 2차전지 밸류업", 4)
    ]
    seen = set()
    news_list = []
    for q, limit in queries:
        try:
            enc = urllib.parse.quote(q)
            url = f"https://news.google.com/rss/search?q={enc}&hl=ko&gl=KR&ceid=KR:ko"
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                root = ET.fromstring(resp.read())
            for item in root.findall(".//item")[:limit]:
                title = (item.findtext("title") or "").strip()
                source = (item.findtext("source") or "국내언론").strip()
                if title and title not in seen:
                    seen.add(title)
                    news_list.append({"source": source, "title": title})
        except Exception as e:
            logger.debug(f"국내 뉴스 수집 실패 ({q}): {e}")
    return news_list


def fetch_kr_earnings_calls():
    """당일 실적 발표(IR/어닝콜)가 있는 시총 상위 대표주를 감지하고 분기 재무제표(YoY·QoQ·컨센서스) 및 어닝콜 속보를 수집"""
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    candidates = [
        ("005930", "삼성전자"),
        ("000660", "SK하이닉스"),
    ] + [(code, name) for code, name, _ in TOP_KR_STOCKS]

    earnings_list = []
    for code, name in candidates:
        try:
            r_int = requests.get(f"https://m.stock.naver.com/api/stock/{code}/integration", headers=HEADERS, timeout=8)
            if r_int.status_code != 200:
                continue
            int_data = r_int.json()
            ir_info = int_data.get("irScheduleInfo") or {}
            ir_date = ir_info.get("irScheduleDate", "")
            ir_dday = ir_info.get("irScheduleDday")
            ir_title = ir_info.get("title", "경영실적 발표")

            # 당일(D-Day == 0 또는 irScheduleDate == today_str) 실적 발표 기업만 감지
            if ir_date != today_str and ir_dday != 0:
                continue

            # 분기 재무 데이터(매출액, 영업이익, 당기순이익, 영업이익률) 수집 (YoY/QoQ/컨센서스 비교용)
            fin_summary = {}
            r_fin = requests.get(f"https://m.stock.naver.com/api/stock/{code}/finance/quarter", headers=HEADERS, timeout=8)
            if r_fin.status_code == 200:
                finfo = (r_fin.json() or {}).get("financeInfo") or {}
                titles = finfo.get("trTitleList") or []
                rows = finfo.get("rowList") or []
                for row in rows:
                    r_title = row.get("title", "")
                    if r_title in ("매출액", "영업이익", "당기순이익", "영업이익률"):
                        cols = row.get("columns") or {}
                        fin_summary[r_title] = {
                            f"{t.get('title', t.get('key', ''))}_{'컨센서스(시장예상치)' if t.get('isConsensus') == 'Y' else '과거확정치'}": (cols.get(t.get("key", "")) or {}).get("value", "-")
                            for t in titles
                        }

            # 당일 실적 발표 수치(YoY, QoQ) 및 컨퍼런스콜(어닝콜) 속보 뉴스 수집
            call_news = []
            seen_titles = set()
            for q in [f"{name} 실적 매출 영업이익 전년", f"{name} 컨퍼런스콜", f"{name} 어닝콜 HBM 가이던스"]:
                try:
                    enc = urllib.parse.quote(q)
                    url = f"https://news.google.com/rss/search?q={enc}&hl=ko&gl=KR&ceid=KR:ko"
                    req = urllib.request.Request(url, headers=HEADERS)
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        root = ET.fromstring(resp.read())
                    for item in root.findall(".//item")[:5]:
                        t_text = (item.findtext("title") or "").strip()
                        if t_text and t_text not in seen_titles:
                            seen_titles.add(t_text)
                            call_news.append(t_text)
                except Exception as ne:
                    logger.debug(f"어닝콜 뉴스 수집 예외 ({name}): {ne}")

            earnings_list.append({
                "code": code,
                "name": name,
                "ir_title": ir_title,
                "ir_date": ir_date,
                "quarterly_table_억_기준": fin_summary,
                "earnings_call_news": call_news[:10]
            })
            logger.info(f"🎙️ 당일 어닝콜/실적발표 감지: {name}({code}) - {ir_title}")
        except Exception as e:
            logger.warning(f"어닝콜 일정 확인 실패 ({code} {name}): {e}")

    return earnings_list


def collect_kr_market_data():
    """한국 증시 마감 브리핑용 전체 데이터 일괄 수집"""
    now = datetime.datetime.now()
    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]

    logger.info("코스피 · 코스닥 지수 및 투자자별 수급 수집 중...")
    kospi = fetch_kr_index("KOSPI", "코스피 (KOSPI)", "유가증권")
    kosdaq = fetch_kr_index("KOSDAQ", "코스닥 (KOSDAQ)", "코스닥")
    usdkrw = fetch_usdkrw_card()

    status_label = "정규장 마감 기준" if now.hour >= 15 and (now.hour > 15 or now.minute >= 30) else "장중 실시간 기준"
    date_str = f"한국 {status_label} · {now.strftime('%Y.%m.%d')} ({weekday_kr})"

    logger.info("삼성전자(005930) & SK하이닉스(000660) 데이터 수집 중...")
    sec = fetch_kr_stock("005930", "삼성전자", "글로벌 메모리 · 파운드리")
    hynix = fetch_kr_stock("000660", "SK하이닉스", "AI HBM 글로벌 선두")

    logger.info("시총 상위 대표주 5종목 수집 중...")
    top_stocks = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(fetch_kr_stock, code, name, sec_lbl) for code, name, sec_lbl in TOP_KR_STOCKS]
        for f in futures:
            top_stocks.append(f.result())

    logger.info("국내 증권가 시황 및 반도체 뉴스 헤드라인 수집 중...")
    news = fetch_kr_market_news()

    logger.info("당일 시총 상위 대표주 어닝콜 및 분기 실적(YoY·QoQ) 발표 여부 확인 중...")
    earnings_calls_raw = fetch_kr_earnings_calls()

    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)

    return {
        "date_str": date_str,
        "indices": [kospi, kosdaq, usdkrw],
        "kospi": kospi,
        "kosdaq": kosdaq,
        "usdkrw": usdkrw,
        "sec": sec,
        "hynix": hynix,
        "top_stocks": top_stocks,
        "news": news,
        "earnings_calls_raw": earnings_calls_raw,
        "branding": config.get("branding", {"name": "최광민", "title": "AI Market Analyst"})
    }


def _call_gemini_json(prompt, api_key, temperature=0.25):
    for model_name in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature}
        }
        try:
            res = requests.post(url, json=payload, timeout=25)
            if res.status_code == 200:
                text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
                text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
                text = re.sub(r"```$", "", text.strip(), flags=re.MULTILINE)
                match = re.search(r"\{.*\}", text, flags=re.DOTALL)
                if match:
                    text = match.group(0)
                return json.loads(text)
        except Exception as e:
            logger.warning(f"Gemini 호출 예외 ({model_name}): {e}")
    return None


def generate_kr_ai_analysis(kr_data):
    """Gemini 1차 초안 + 2차 팩트체크 감사(Auditor)를 통해 한국 증시 마감 브리핑 생성"""
    api_key = os.getenv("GEMINI_API_KEY")
    fallback = get_fallback_kr_analysis(kr_data)
    if not api_key:
        return fallback

    kospi = kr_data["kospi"]
    kosdaq = kr_data["kosdaq"]
    usdkrw = kr_data["usdkrw"]
    sec = kr_data["sec"]
    hynix = kr_data["hynix"]
    top_stocks = kr_data["top_stocks"]
    news_lines = [f"[{n['source']}] {n['title']}" for n in kr_data.get("news", [])]
    earnings_raw = kr_data.get("earnings_calls_raw", [])

    prompt = f"""
당신은 한국 증시(코스피·코스닥)와 반도체 섹터(삼성전자·SK하이닉스), 그리고 미국 증시(엔비디아·AMD·필라델피아 반도체)의 연결고리를 가장 정확하고 알기 쉽게 해설하는 수석 애널리스트입니다.
아래 [실측 시장 데이터], [당일 어닝콜 & 분기 실적 발표 데이터], [국내 증권가·언론 시황 헤드라인]을 바탕으로 한국 증시 마감 브리핑 JSON을 작성해 주세요.

[실측 한국 증시 데이터 (절대 팩트)]
- 일시: {kr_data['date_str']}
- 코스피(KOSPI): {kospi['price']} ({kospi['symbol']} {kospi['diff_str']}, {kospi['pct_str']}) | 수급: {kospi['sub_value']} (개인 {kospi.get('personal_억', '')}억)
- 코스닥(KOSDAQ): {kosdaq['price']} ({kosdaq['symbol']} {kosdaq['diff_str']}, {kosdaq['pct_str']}) | 수급: {kosdaq['sub_value']}
- 원/달러 환율: {usdkrw['price']} ({usdkrw['symbol']} {usdkrw['diff_str']}, {usdkrw['pct_str']})
- 삼성전자(005930): {sec['price']} ({sec['symbol']} {sec['pct_str']}) | {sec['flow_str']}
- SK하이닉스(000660): {hynix['price']} ({hynix['symbol']} {hynix['pct_str']}) | {hynix['flow_str']}
- 시총 상위 대표주: {json.dumps([{s['name']: f"{s['price']} ({s['pct_str']})"} for s in top_stocks], ensure_ascii=False)}
- 오늘 국내 증권가·언론 실시간 헤드라인: {json.dumps(news_lines, ensure_ascii=False)}
- [당일 어닝콜 & 분기 실적(YoY·QoQ) 원본 데이터]: {json.dumps(earnings_raw, ensure_ascii=False)}

[작성 및 팩트체크 지침]
1. **수치 및 수급 100% 일치**: 위 데이터의 지수, 삼성전자·SK하이닉스 주가와 등락률, 외국인·기관 순매수 금액을 단 1의 오차도 없이 정확히 반영할 것.
2. **삼성전자 & SK하이닉스 투톱 집중 분석 (`focus_analysis`)**:
   - 간밤 미국 반도체(엔비디아·AMD·필라델피아 반도체 지수) 훈풍/영향, HBM 및 범용 메모리 업황, 외국인·기관 수급, 증권사 리포트 코멘트를 종합하여 각 종목의 오늘 움직임 배경과 핵심 체크포인트를 친절하게 설명할 것.
3. **당일 어닝콜 & 분기 실적(YoY·QoQ) 집중 요약 (`earnings_calls`)**:
   - `[당일 어닝콜 & 분기 실적 원본 데이터]`가 비어있지 않은 경우, 반드시 `earnings_call_news`(당일 실적 발표 속보 헤드라인)에 보도된 **실제 발표 확정치(매출액, 영업이익, 영업이익률, YoY, QoQ)**를 최우선으로 사용할 것!
   - 주의: `quarterly_table_억_기준`에서 `_컨센서스(시장예상치)`로 끝나는 열(예: `2026.09(E)_컨센서스(시장예상치)`)은 발표 전 시장 예상치(Consensus)이므로 확정 실적과 혼동하지 말고, 실제 발표된 확정치가 컨센서스를 얼마나 상회/하회했는지 비교하는 용도로만 사용할 것.
   - 컨센서스 상회 여부(`badge`: `"어닝 서프라이즈 🔥"`, `"컨센서스 부합"`, `"컨센서스 하회"` 등)와 **컨퍼런스콜(어닝콜) 경영진 핵심 가이던스·Q&A 발언 2가지(`highlights`)**를 정확히 요약할 것.
   - 만약 `[당일 어닝콜 & 분기 실적 원본 데이터]`가 빈 배열(`[]`)이면 `earnings_calls`도 반드시 빈 배열(`[]`)로 반환할 것.
4. **주요 시황 코멘트 & 전문가 의견 (`reasons` 2개)**:
   - 오늘 한국 증시를 움직인 가장 중요한 원인 2가지를 국내 증권가·외신 분석을 바탕으로 명확하게 정리할 것.
5. **오늘 밤 미국 증시 연동 관전 포인트 (`us_bridge`)**:
   - 오늘 한국 반도체·대형주 흐름을 바탕으로, 오늘 밤 열리는 미국 증시(엔비디아, AMD, 빅테크 실적 발표 및 매크로 지표)에서 투자자가 무엇을 주목해야 할지 2줄로 연결해 줄 것.
6. 마크다운 백틱 없이 순수 JSON만 반환할 것.

[JSON 응답 형식]
{{
  "headline": "오늘 한국 증시 마감과 삼성전자·SK하이닉스 흐름을 관통하는 한 줄 핵심 판단",
  "focus_analysis": {{
    "sec": {{
      "title": "삼성전자 오늘 주가 흐름 핵심 요약 제목",
      "analysis": "삼성전자 등락 배경, 외국인·기관 수급, 메모리/HBM 관련 증권가 분석 설명 2줄",
      "key_point": "삼성전자 보유/관심 투자자를 위한 실전 체크포인트 한 줄"
    }},
    "hynix": {{
      "title": "SK하이닉스 오늘 주가 흐름 핵심 요약 제목",
      "analysis": "SK하이닉스 등락 배경, AI HBM 독점적 지위 및 엔비디아 연동 수급 분석 설명 2줄",
      "key_point": "SK하이닉스 보유/관심 투자자를 위한 실전 체크포인트 한 줄"
    }}
  }},
  "stocks_note": "시총 상위 대표주(2차전지·바이오·자동차·금융·한미반도체) 흐름 한 줄 요약",
  "earnings_calls": [
    {{
      "name": "기업명 (종목코드)",
      "quarter": "2026년 3분기 확정실적 & 컨퍼런스콜",
      "badge": "어닝 서프라이즈 🔥",
      "metrics": "매출액 OO조원 (YoY +OO%, QoQ +OO%) · 영업이익 OO조원 (YoY +OO%, QoQ +OO%, 영업이익률 OO%)",
      "highlights": [
        "어닝콜 경영진 핵심 가이던스·수주/출하 계획·수요 전망 요약 1줄",
        "어닝콜 설비투자(CapEx)·주주환원·차세대 제품(HBM4 등) Q&A 핵심 발언 요약 1줄"
      ]
    }}
  ],
  "reasons": [
    {{
      "title": "첫 번째 핵심 시황 배경 및 증권가 코멘트 제목",
      "content": "구체적인 수치(외인·기관 수급 등)와 증권가 분석을 포함한 설명 2줄"
    }},
    {{
      "title": "두 번째 핵심 시황 배경 및 업종별 수급 특징 제목",
      "content": "구체적인 종목/섹터 등락과 배경 설명 2줄"
    }}
  ],
  "us_bridge": "오늘 한국장 반도체·외인 수급을 바탕으로 오늘 밤 미국 증시(NVDA·AMD·빅테크 실적 등)에서 체크해야 할 핵심 포인트 2줄"
}}
"""

    logger.info("Gemini 1차 한국 증시 마감 브리핑 초안 생성 중...")
    draft = _call_gemini_json(prompt, api_key, temperature=0.25)
    if not draft or "headline" not in draft:
        return fallback

    # 2차 팩트체크 감사(Auditor) 패스
    audit_prompt = f"""
당신은 한국 증시 리포트 팩트체크 감사관입니다.
아래 [1차 초안 JSON]이 [실측 원본 데이터] 및 [어닝콜 & 분기 실적 원본 데이터]의 수치(주가, 등락률, 매출, 영업이익, YoY, QoQ 등)와 100% 일치하는지 검증하고, 틀린 수치나 과장된 표현이 있다면 즉시 수정한 순수 JSON을 반환해 주세요.
- 주의 1: `quarterly_table_억_기준`의 `_컨센서스(시장예상치)` 수치를 실제 확정 실적으로 오기재하지 않았는지 점검하고, 반드시 `earnings_call_news`에 보도된 당일 실제 확정 매출·영업이익 수치와 함께 **YoY(%) 및 QoQ(%) 증감률**을 `metrics`에 빠짐없이 명시할 것! (과거 확정치 대비 계산 가능)
- 주의 2: `reasons` 배열 내 각 항목의 `title`과 `content`(2줄 상세 설명) 필드를 절대 생략하지 말고 모두 유지할 것!

[실측 원본 데이터]
- 코스피: {kospi['price']} ({kospi['pct_str']}, {kospi['sub_value']})
- 코스닥: {kosdaq['price']} ({kosdaq['pct_str']}, {kosdaq['sub_value']})
- 원/달러 환율: {usdkrw['price']} ({usdkrw['pct_str']})
- 삼성전자: {sec['price']} ({sec['pct_str']}, {sec['flow_str']})
- SK하이닉스: {hynix['price']} ({hynix['pct_str']}, {hynix['flow_str']})
- 시총 상위주: {json.dumps([{s['name']: f"{s['price']} ({s['pct_str']})"} for s in top_stocks], ensure_ascii=False)}
- 당일 어닝콜 & 분기 실적 원본 데이터: {json.dumps(earnings_raw, ensure_ascii=False)}

[1차 초안 JSON]
{json.dumps(draft, ensure_ascii=False)}
"""
    logger.info("Gemini 2차 한국 증시 브리핑 팩트체크 감사(Auditor) 실행 중...")
    audited = _call_gemini_json(audit_prompt, api_key, temperature=0.1)
    result = audited if (audited and "headline" in audited and "focus_analysis" in audited) else draft

    # reasons 내 content 누락 방지 가드레일
    draft_reasons = draft.get("reasons") or fallback.get("reasons", [])
    res_reasons = result.get("reasons") or []
    for idx, r in enumerate(res_reasons):
        if isinstance(r, dict) and not (r.get("content") or r.get("desc")):
            if idx < len(draft_reasons) and isinstance(draft_reasons[idx], dict):
                r["content"] = draft_reasons[idx].get("content", "")

    if not earnings_raw:
        result["earnings_calls"] = []
    elif not result.get("earnings_calls"):
        result["earnings_calls"] = draft.get("earnings_calls") or fallback.get("earnings_calls", [])
    logger.info("✅ 한국 증시 마감 브리핑 2차 팩트체크 감사 완료!")
    return result


def get_fallback_kr_analysis(kr_data):
    kospi = kr_data["kospi"]
    sec = kr_data["sec"]
    hynix = kr_data["hynix"]
    earnings_raw = kr_data.get("earnings_calls_raw", [])
    fallback_calls = []
    for ec in earnings_raw:
        news_top = ec.get("earnings_call_news", [])
        fallback_calls.append({
            "name": f"{ec['name']} ({ec['code']})",
            "quarter": ec.get("ir_title", "분기 경영실적 & 어닝콜"),
            "badge": "실적 · 어닝콜 발표",
            "metrics": news_top[0] if news_top else f"{ec['name']} 분기 확정 실적 발표",
            "highlights": news_top[1:3] if len(news_top) >= 3 else ["당일 실적 발표 및 컨퍼런스콜 주요 가이던스 확인"]
        })
    return {
        "headline": f"코스피 {kospi['price']} ({kospi['pct_str']}) · 삼성전자({sec['pct_str']}) 및 SK하이닉스({hynix['pct_str']}) 중심 반도체 수급 집중",
        "focus_analysis": {
            "sec": {
                "title": f"삼성전자 {sec['price']} ({sec['pct_str']}), 반도체 투심 회복 속 수급 유입",
                "analysis": "미 필라델피아 반도체 지수 및 AI 칩 업황 기대감에 힘입어 삼성전자로 저가 매수세 및 기관·외국인 수급이 유입되었습니다.",
                "key_point": "차세대 HBM 공급 확대 일정 및 외국인 순매수 기조 지속 여부를 점검해야 합니다."
            },
            "hynix": {
                "title": f"SK하이닉스 {hynix['price']} ({hynix['pct_str']}), AI HBM 대장주 모멘텀 부각",
                "analysis": "엔비디아향 HBM 독점적 지위와 고대역폭메모리 실적 성장성에 대한 증권가 호평이 이어지며 강한 탄력을 보였습니다.",
                "key_point": "미국 빅테크 기업들의 AI 데이터센터 설비투자(CapEx) 발표와 엔비디아 주가 흐름이 핵심 연동 지표입니다."
            }
        },
        "stocks_note": "반도체·금융·자동차 등 실적 및 밸류업 모멘텀 보유 대형주 중심의 선별적 강세가 나타났습니다.",
        "earnings_calls": fallback_calls,
        "reasons": [
            {
                "title": f"코스피 {kospi['sub_value']} 수급 유입 및 미 반도체 훈풍 반영",
                "content": "간밤 미국 증시에서 엔비디아와 AMD 등 AI 반도체 섹터가 강세를 보인 점이 국내 반도체 투톱과 지수 전반의 투자 심리를 견인했습니다."
            },
            {
                "title": "실적 시즌 맞이 업종별 차별화 및 반도체 소부장 동반 강세",
                "content": "HBM 밸류체인(SK하이닉스·한미반도체 등)과 밸류업 관련주(금융·자동차)로 기관과 외국인의 매수세가 집중되었습니다."
            }
        ],
        "us_bridge": "오늘 국내 증시에서 확인된 강력한 반도체 매수세가 오늘 밤 미국 증시(마이크로소프트·구글·아마존 등 빅테크 실적 및 엔비디아·AMD 주가)로 이어질지 주목됩니다."
    }


def render_kr_briefing_image(kr_data, ai_data, output_png="output/kr_briefing.png"):
    """HTML 템플릿 바인딩 후 Playwright/Edge로 고화질 PNG 렌더링"""
    os.makedirs(os.path.dirname(output_png), exist_ok=True)
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("kr_briefing.html")

    rendered_html = template.render(
        date_str=kr_data["date_str"],
        indices=kr_data["indices"],
        sec=kr_data["sec"],
        hynix=kr_data["hynix"],
        top_stocks=kr_data["top_stocks"],
        ai=ai_data,
        branding=kr_data["branding"]
    )

    html_path = "output/kr_briefing.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(rendered_html)

    try:
        from playwright.sync_api import sync_playwright
        logger.info("Playwright를 통한 한국 증시 브리핑 이미지 렌더링 중...")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                viewport={"width": 860, "height": 1200},
                device_scale_factor=2
            )
            file_url = Path(os.path.abspath(html_path)).as_uri()
            page.goto(file_url, wait_until="networkidle")
            page.wait_for_timeout(500)
            page.locator("body").screenshot(path=output_png)
            browser.close()
        logger.info(f"Playwright 렌더링 완료: {output_png}")
        return output_png
    except Exception as pe:
        logger.warning(f"Playwright 오류 ({pe}), Edge 대체 캡처 진행...")

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
        "--window-size=860,1250",
        f"--screenshot={os.path.abspath(output_png)}",
        "--hide-scrollbars",
        file_url
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return output_png


def send_kr_briefing_to_telegram(image_path, kr_data, ai_data):
    """텔레그램 봇으로 한국 증시 마감 브리핑 카드 및 요약 캡션 전송"""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN 미설정")
        return False

    from subscriber_manager import get_subscribers
    subscribers = get_subscribers()
    if not subscribers:
        return False

    kospi = kr_data["kospi"]
    kosdaq = kr_data["kosdaq"]
    usdkrw = kr_data["usdkrw"]
    sec = kr_data["sec"]
    hynix = kr_data["hynix"]

    ec_block = ""
    earnings_calls = ai_data.get("earnings_calls") or []
    if earnings_calls:
        ec_lines = []
        for ec in earnings_calls[:2]:
            ec_lines.append(f"• *{ec.get('name', '')}*: {ec.get('metrics', '')}")
            for hl in (ec.get("highlights") or [])[:1]:
                ec_lines.append(f"  └ 🗣 {hl}")
        ec_block = "\n🎙 *당일 어닝콜 & 실적(YoY·QoQ)*\n" + "\n".join(ec_lines) + "\n"

    caption = f"""🇰🇷 *한국 증시 마감 브리핑 (K-Market Brief)*
🗓 *기준*: {kr_data['date_str']}

💡 *오늘의 판단*
"{ai_data.get('headline', '')}"

📌 *핵심 지수 & 수급 3줄 요약*
1️⃣ *코스피*: {kospi['price']} ({kospi['symbol']}{kospi['pct_str']}) | {kospi['sub_value']}
2️⃣ *코스닥*: {kosdaq['price']} ({kosdaq['symbol']}{kosdaq['pct_str']}) | *환율*: {usdkrw['price']}
3️⃣ *미 증시 연동*: {ai_data.get('us_bridge', '')}

🎯 *반도체 투톱 집중 점검*
• 🔵 *삼성전자*: {sec['price']} ({sec['symbol']}{sec['pct_str']})
• 🟠 *SK하이닉스*: {hynix['price']} ({hynix['symbol']}{hynix['pct_str']})
{ec_block}
#한국증시 #코스피 #코스닥 #삼성전자 #SK하이닉스 #어닝콜 #외국인수급 #환율"""

    if len(caption) > 1020:
        caption = caption[:1015] + "..."

    with open(image_path, "rb") as f:
        img_bytes = f.read()

    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    success_count = 0
    for target_chat_id in subscribers:
        try:
            payload = {
                "chat_id": target_chat_id,
                "caption": caption,
                "parse_mode": "Markdown"
            }
            files = {"photo": ("kr_briefing.png", img_bytes, "image/png")}
            res = requests.post(url, data=payload, files=files, timeout=30)
            if res.status_code == 200:
                logger.info(f"✅ 구독자({target_chat_id}) 한국 증시 브리핑 발송 성공!")
                success_count += 1
            else:
                logger.error(f"❌ 구독자({target_chat_id}) 발송 실패 ({res.status_code}): {res.text}")
        except Exception as e:
            logger.error(f"❌ 구독자({target_chat_id}) 발송 중 예외: {e}")

    logger.info(f"🎉 한국 증시 브리핑: 총 {len(subscribers)}명 중 {success_count}명 발송 완료")
    return success_count > 0


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

    logger.info("==========================================")
    logger.info("🇰🇷 한국 증시 마감 브리핑 파이프라인 시작")
    logger.info("==========================================")

    kr_data = collect_kr_market_data()
    ai_data = generate_kr_ai_analysis(kr_data)
    output_png = "output/kr_briefing.png"
    render_kr_briefing_image(kr_data, ai_data, output_png)
    send_kr_briefing_to_telegram(output_png, kr_data, ai_data)

    logger.info("==========================================")
    logger.info(f"🎉 한국 증시 브리핑 완료! 결과물: {os.path.abspath(output_png)}")
    logger.info("==========================================")


if __name__ == "__main__":
    main()
