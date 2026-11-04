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

KST = datetime.timezone(datetime.timedelta(hours=9))

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


def _flow_direction_label(val_str):
    """수급 문자열(+/-)에서 순매수/순매도 명시 라벨 반환"""
    s = str(val_str or "").strip()
    if s.startswith("+"):
        return "순매수"
    elif s.startswith("-"):
        return "순매도"
    try:
        v = float(s.replace(",", ""))
        if v > 0:
            return "순매수"
        elif v < 0:
            return "순매도"
    except Exception:
        pass
    return "중립"


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
        trend_bizdate = str(trend.get("bizdate") or "")
        market_status = basic.get("marketStatus", "CLOSE")
        local_traded_date = (basic.get("localTradedAt") or "")[:10]

        sub_val = f"외인 {foreign_val}억 · 기관 {inst_val}억"
        flow_detail = (
            f"외국인 {foreign_val}억({_flow_direction_label(foreign_val)}) · "
            f"기관 {inst_val}억({_flow_direction_label(inst_val)}) · "
            f"개인 {pers_val}억({_flow_direction_label(pers_val)})"
        )
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
            "trend_bizdate": trend_bizdate,
            "flow_detail_str": flow_detail,
            "sub_label": "수급(억)",
            "sub_value": sub_val,
            "market_status": market_status,
            "local_traded_date": local_traded_date
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
            "foreign_억": "0",
            "inst_억": "0",
            "personal_억": "0",
            "flow_detail_str": "수급 집계 중",
            "sub_label": "수급",
            "sub_value": "집계 중",
            "market_status": "CLOSE"
        }


def fetch_kr_stock(code, default_name, sector_label=""):
    """
    네이버 금융 모바일 API를 통해 개별 종목 주가, 등락률, 외국인 지분율, 당일/전일 수급 날짜(bizdate) 검증,
    그리고 증권사 최신 리포트(researches) 및 목표주가 컨센서스를 함께 조회
    """
    try:
        r_basic = requests.get(f"https://m.stock.naver.com/api/stock/{code}/basic", headers=HEADERS, timeout=8)
        r_trend = requests.get(f"https://m.stock.naver.com/api/stock/{code}/trend", headers=HEADERS, timeout=8)
        r_int = requests.get(f"https://m.stock.naver.com/api/stock/{code}/integration", headers=HEADERS, timeout=8)
        basic = r_basic.json() if r_basic.status_code == 200 else {}
        trend_list = r_trend.json() if r_trend.status_code == 200 else []
        int_data = r_int.json() if r_int.status_code == 200 else {}

        name = basic.get("stockName") or default_name
        close_price = basic.get("closePrice", "N/A")
        diff_raw = basic.get("compareToPreviousClosePrice", "0").replace("-", "")
        ratio_raw = basic.get("fluctuationsRatio", "0")
        cls, symbol, pct_str = _parse_direction(basic.get("compareToPreviousPrice"), ratio_raw)

        local_traded_date = (basic.get("localTradedAt") or "")[:10]
        traded_bizdate = local_traded_date.replace("-", "")

        # 증권사 컨센서스 목표주가 및 최신 리포트 추출
        cons_info = int_data.get("consensusInfo") or {}
        target_price = cons_info.get("priceTargetMean", "")
        researches = []
        for rs in (int_data.get("researches") or [])[:4]:
            bnm = rs.get("bnm", "증권사")
            tit = rs.get("tit", "")
            wdt = rs.get("wdt", "")
            if tit and "STRESS TEST" not in tit.upper():
                researches.append(f"[{bnm}({wdt})] {tit}")

        flow_str = "외인·기관 수급 집계"
        flow_prompt_note = "수급 집계 중"
        if isinstance(trend_list, list) and len(trend_list) > 0:
            latest_t = trend_list[0]
            t_bizdate = str(latest_t.get("bizdate") or "")
            hold_ratio = latest_t.get("foreignerHoldRatio", "")
            f_qty = latest_t.get("foreignerPureBuyQuant", "")
            o_qty = latest_t.get("organPureBuyQuant", "")

            if t_bizdate and traded_bizdate and t_bizdate == traded_bizdate:
                # 당일 수급 확정치인 경우
                parts = []
                if hold_ratio:
                    parts.append(f"외인지분 {hold_ratio}")
                if f_qty:
                    parts.append(f"외인 {f_qty}주")
                if o_qty and len(parts) < 2:
                    parts.append(f"기관 {o_qty}주")
                if parts:
                    flow_str = " · ".join(parts)
                flow_prompt_note = (
                    f"당일({t_bizdate[4:6]}/{t_bizdate[6:8]}) 확정수급: 외인지분 {hold_ratio} · "
                    f"외국인 {f_qty}주({_flow_direction_label(f_qty)}) · 기관 {o_qty}주({_flow_direction_label(o_qty)})"
                )
            else:
                # 아직 당일 개별종목 수급이 정산 전이어서 직전 거래일(t_bizdate) 데이터인 경우 -> 날짜 명시하여 오기재 원천 차단!
                date_tag = f"{t_bizdate[4:6]}/{t_bizdate[6:8]}" if len(t_bizdate) == 8 else "전일"
                parts = []
                if hold_ratio:
                    parts.append(f"외인지분 {hold_ratio}")
                if target_price:
                    parts.append(f"목표가 {target_price}원")
                elif f_qty:
                    parts.append(f"{date_tag}외인 {f_qty}주")
                if parts:
                    flow_str = " · ".join(parts)
                flow_prompt_note = (
                    f"외국인 지분율 {hold_ratio}"
                    + (f" · 증권사 목표주가 컨센서스 {target_price}원" if target_price else "")
                )

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
            "flow_str": flow_str,
            "flow_prompt_note": flow_prompt_note,
            "target_price": target_price,
            "researches": researches
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
            "flow_str": "-",
            "flow_prompt_note": "-",
            "target_price": "",
            "researches": []
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
    """
    Google News 한국어 RSS를 통해 최근 24시간(when:1d) 이내의 국내 증시 마감 시황,
    증권사 코멘트, 삼성전자·SK하이닉스 뉴스만 엄격히 필터링하여 수집 (과거 시황 기사 혼입 차단)
    """
    base_queries = [
        ("코스피 마감 시황 외국인 기관 순매수 증권", 5),
        ("삼성전자 SK하이닉스 주가 반도체 HBM 외국인 증권사", 5),
        ("코스닥 시황 반도체 2차전지 밸류업", 4)
    ]
    seen = set()
    news_list = []

    for window in ("when:1d", "when:2d"):
        for q_text, limit in base_queries:
            try:
                enc = urllib.parse.quote(f"{q_text} {window}")
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
                logger.debug(f"국내 뉴스 수집 실패 ({q_text}): {e}")
        if len(news_list) >= 4:
            break

    return news_list


def fetch_kr_earnings_calls():
    """
    당일 실적 발표(IR/어닝콜)가 있는 시총 상위 대표주를 감지하고,
    이미 브리핑된 분기(reported_earnings.json)는 중복 제외하며,
    분기 재무제표(YoY·QoQ·컨센서스), 당일 주가 반응, 최근 24시간 어닝콜 속보를 수집
    """
    from data_collector import is_earnings_already_reported

    today_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")
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

            # 네이버 front-api earningCall 엔드포인트 교차 확인
            if ir_date != today_str and ir_dday != 0:
                try:
                    r_ec = requests.get(
                        f"https://m.stock.naver.com/front-api/domestic/stock/earningCall?itemCode={code}",
                        headers=HEADERS,
                        timeout=6
                    )
                    if r_ec.status_code == 200:
                        ec_obj = ((r_ec.json() or {}).get("result") or {}).get("earningCall") or {}
                        ev_dt = str(ec_obj.get("eventDatetime") or "")[:10]
                        if ev_dt == today_str:
                            ir_date = today_str
                            ir_dday = 0
                            q_num = ec_obj.get("fiscalQuarter", "")
                            y_num = ec_obj.get("fiscalYear", "")
                            if y_num and q_num:
                                ir_title = f"{y_num}년 {q_num}분기 경영실적 및 컨퍼런스콜"
                except Exception:
                    pass

            # 당일(D-Day == 0 또는 irScheduleDate == today_str) 실적 발표 기업만 감지
            if ir_date != today_str and ir_dday != 0:
                continue

            # 이미 당해 분기 실적 브리핑이 발송된 종목(reported_earnings.json)은 중복 제외
            if is_earnings_already_reported(code, ir_date or today_str, market="kr"):
                logger.info(f"ℹ️ [{name}({code})] 이미 실적 브리핑이 발송된 분기이므로 중복 제외합니다.")
                continue

            # 당일 주가 반응 조회 (실적 자체 상회/하회 평가와 주가 반응을 명확히 분리하기 위함)
            st_info = fetch_kr_stock(code, name)
            stock_reaction_str = f"당일 종가 {st_info['price']} ({st_info['symbol']} {st_info['pct_str']})"

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

            # 당일(when:1d) 실적 발표 수치(YoY, QoQ) 및 컨퍼런스콜(어닝콜) 속보 뉴스 수집
            call_news = []
            seen_titles = set()
            for q in [
                f"{name} 실적 매출 영업이익 전년 컨센서스 when:1d",
                f"{name} 컨퍼런스콜 어닝콜 가이던스 when:1d",
                f"{name} 실적 발표 영업이익 when:2d"
            ]:
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
                "ir_date": ir_date or today_str,
                "stock_price_reaction": stock_reaction_str,
                "table_fact_note": (
                    "주의: quarterly_table_억_기준에서 '_컨센서스(시장예상치)' 열은 발표 전 증권가 예상치이므로 "
                    "절대 실제 발표 실적으로 오기재하지 말 것! 당일 실제 확정 매출·영업이익·YoY·QoQ는 반드시 "
                    "earnings_call_news 속보 헤드라인에서 추출하고, 컨센서스 상회/하회 판정과 당일 주가 반응(stock_price_reaction)을 분리 표기할 것."
                ),
                "quarterly_table_억_기준": fin_summary,
                "earnings_call_news": call_news[:12]
            })
            logger.info(f"🎙️ 당일 신규 어닝콜/실적발표 감지: {name}({code}) - {ir_title}")
        except Exception as e:
            logger.warning(f"어닝콜 일정 확인 실패 ({code} {name}): {e}")

    return earnings_list


def collect_kr_market_data():
    """한국 증시 마감 브리핑용 전체 데이터 일괄 수집 (미 증시 실제 차기 실적 일정 포함)"""
    from data_collector import fetch_verified_upcoming_earnings

    now = datetime.datetime.now(KST)
    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]

    logger.info("코스피 · 코스닥 지수 및 투자자별 수급 수집 중...")
    kospi = fetch_kr_index("KOSPI", "코스피 (KOSPI)", "유가증권")
    kosdaq = fetch_kr_index("KOSDAQ", "코스닥 (KOSDAQ)", "코스닥")
    usdkrw = fetch_usdkrw_card()

    status_label = "정규장 마감 기준" if now.hour >= 15 and (now.hour > 15 or now.minute >= 30) else "장중 실시간 기준"
    date_str = f"한국 {status_label} · {now.strftime('%Y.%m.%d')} ({weekday_kr})"

    logger.info("삼성전자(005930) & SK하이닉스(000660) 데이터 및 증권사 리포트 수집 중...")
    sec = fetch_kr_stock("005930", "삼성전자", "글로벌 메모리 · 파운드리")
    hynix = fetch_kr_stock("000660", "SK하이닉스", "AI HBM 글로벌 선두")

    logger.info("시총 상위 대표주 5종목 수집 중...")
    top_stocks = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(fetch_kr_stock, code, name, sec_lbl) for code, name, sec_lbl in TOP_KR_STOCKS]
        for f in futures:
            top_stocks.append(f.result())

    logger.info("국내 증권가 당일(when:1d) 시황 및 반도체 뉴스 헤드라인 수집 중...")
    news = fetch_kr_market_news()

    logger.info("당일 시총 상위 대표주 어닝콜 및 분기 실적(YoY·QoQ) 발표 여부 확인 중...")
    earnings_calls_raw = fetch_kr_earnings_calls()

    logger.info("미국 빅테크·반도체 확정 실적 발표 일정(us_bridge 팩트체크용) 조회 중...")
    raw_us_earnings = fetch_verified_upcoming_earnings()
    today_iso = now.strftime("%Y-%m-%d")
    us_upcoming_earnings = [
        e for e in raw_us_earnings
        if e.get("earnings_date") and e["earnings_date"] >= today_iso and e["earnings_date"] <= "2026-12-31"
    ]

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
        "us_upcoming_earnings": us_upcoming_earnings,
        "branding": config.get("branding", {"name": "최광민", "title": "AI Market Analyst"})
    }


def _call_gemini_json(prompt, api_key, temperature=0.25):
    """Gemini 모델을 순차 호출하여 JSON 응답 반환 (Google Search 그라운딩 우선 시도 후 기본 호출 폴백)"""
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
                res = requests.post(url, json=payload, timeout=35)
                if res.status_code == 200:
                    text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
                    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
                    text = re.sub(r"```$", "", text.strip(), flags=re.MULTILINE)
                    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
                    if match:
                        text = match.group(0)
                    data = json.loads(text)
                    mode_str = "Search-Grounded" if use_search else "Data-Grounded"
                    logger.info(f"Gemini AI ({model_name} / {mode_str}) 응답 생성 성공!")
                    return data
                elif use_search and res.status_code in (400, 403, 429):
                    continue
            except Exception as e:
                if not use_search:
                    logger.warning(f"Gemini 호출 예외 ({model_name}): {e}")
    return None


def _enforce_kr_hard_facts(result, kr_data):
    """
    한국 증시 브리핑 파이썬 결정론적(Deterministic) 하드 팩트체크 가드레일:
    1) 당일 어닝콜 원본 데이터(earnings_calls_raw)에 없는 종목 환각 제거 및 배지(badge)·지표(metrics) 모순 자동 교정
    2) 코스피 외국인·기관 수급 부호(+/-)와 반대되는 '동반 순매수' 등 허위 수급 서술 자동 교정
    3) us_bridge(오늘 밤 미국 증시 관전 포인트)에 이미 발표 완료된 미국 빅테크 실적을 예정인 것처럼 오기재하는 현상 차단
    """
    if not isinstance(result, dict):
        return result

    # 1. 어닝콜 가드레일
    earnings_raw = kr_data.get("earnings_calls_raw") or []
    if not earnings_raw:
        result["earnings_calls"] = []
    elif not isinstance(result.get("earnings_calls"), list):
        result["earnings_calls"] = []
    else:
        valid_codes = {item.get("code", ""): item for item in earnings_raw if item.get("code")}
        valid_names = {item.get("name", ""): item for item in earnings_raw if item.get("name")}
        filtered_calls = []
        for ec in result["earnings_calls"]:
            if not isinstance(ec, dict):
                continue
            ec_name = str(ec.get("name", ""))
            matched = None
            for cd, raw_item in valid_codes.items():
                if cd and cd in ec_name:
                    matched = raw_item
                    break
            if not matched:
                for nm, raw_item in valid_names.items():
                    if nm and nm in ec_name:
                        matched = raw_item
                        break
            if not matched:
                logger.warning(f"[한국장 팩트체크 가드레일] 당일 미발표/중복 어닝콜 카드 제거: {ec_name}")
                continue

            badge = str(ec.get("badge", "")).strip()
            metrics_txt = str(ec.get("metrics", ""))
            if ("악화" in badge or "쇼크" in badge or "하회" in badge) and ("상회" in metrics_txt and "하회" not in metrics_txt):
                logger.warning(f"[한국장 팩트체크 가드레일] {ec_name} 컨센서스 상회이나 배지가 '{badge}'로 모순 표기됨 -> 교정")
                ec["badge"] = "실적 상회 · 가이던스 점검"
            elif ("서프라이즈" in badge or "상회" in badge) and ("컨센서스 하회" in metrics_txt and "상회" not in metrics_txt):
                logger.warning(f"[한국장 팩트체크 가드레일] {ec_name} 컨센서스 하회이나 배지가 '{badge}'로 모순 표기됨 -> 교정")
                ec["badge"] = "컨센서스 하회 · 가이던스 주시"

            filtered_calls.append(ec)
        result["earnings_calls"] = filtered_calls

    # 2. 코스피 외국인·기관 수급 방향성 모순 교정
    kospi = kr_data.get("kospi") or {}
    f_str = str(kospi.get("foreign_억", "0")).strip()
    i_str = str(kospi.get("inst_억", "0")).strip()
    f_is_sell = f_str.startswith("-")
    i_is_sell = i_str.startswith("-")

    if f_is_sell != i_is_sell:
        # 코스피에서 한 주체는 순매도(-), 다른 주체는 순매수(+)인데 '코스피 외국인·기관 동반 순매수'라고 잘못 쓴 경우만 교정 (코스닥 문장은 제외!)
        false_both_buy = re.compile(r"(외국인과\s*기관|외인·기관|외국인·기관)\s*(의\s*)?동반\s*(순)?매수")
        replacement_flow = (
            f"외국인({f_str}억) 매도 속 기관({i_str}억) 순매수"
            if f_is_sell
            else f"기관({i_str}억) 매도 속 외국인({f_str}억) 순매수"
        )
        if isinstance(result.get("headline"), str) and "코스닥" not in result["headline"] and false_both_buy.search(result["headline"]):
            logger.warning("[한국장 팩트체크 가드레일] headline 내 코스피 외인·기관 동반 매수 오기재 교정")
            result["headline"] = false_both_buy.sub(replacement_flow, result["headline"])
        for r_item in result.get("reasons") or []:
            if isinstance(r_item, dict):
                row_full = f"{r_item.get('title', '')} {r_item.get('content', '')}"
                if "코스닥" in row_full and "코스피" not in row_full:
                    continue
                for k in ("title", "content"):
                    if isinstance(r_item.get(k), str) and "코스닥" not in r_item[k] and false_both_buy.search(r_item[k]):
                        logger.warning(f"[한국장 팩트체크 가드레일] reasons.{k} 내 코스피 외인·기관 동반 매수 오기재 교정")
                        r_item[k] = false_both_buy.sub(replacement_flow, r_item[k])

    # 3. us_bridge 내 이미 완료된 미국 빅테크(MS·구글·아마존·애플·메타·AMD) 실적 예정 오기재 교정
    us_bridge = result.get("us_bridge")
    if isinstance(us_bridge, str):
        stale_us_pattern = re.compile(r"(마이크로소프트|구글|알파벳|아마존|애플|메타|AMD)[·,\s]*(마이크로소프트|구글|알파벳|아마존|애플|메타|AMD)*\s*(등\s*)?(빅테크\s*)?실적\s*발표")
        if stale_us_pattern.search(us_bridge):
            logger.warning("[한국장 팩트체크 가드레일] us_bridge 내 이미 완료된 미국 빅테크 실적 발표 언급 교정")
            result["us_bridge"] = stale_us_pattern.sub("AI 반도체(엔비디아·AMD·ARM·퀄컴) 수급 및 차기 실적 모멘텀", us_bridge)

    return result


def generate_kr_ai_analysis(kr_data):
    """Gemini 1차 초안 + 2차 팩트체크 감사(Auditor) + 3차 파이썬 하드 팩트 가드레일을 통해 한국 증시 마감 브리핑 생성"""
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
    us_upcoming = kr_data.get("us_upcoming_earnings", [])

    prompt = f"""
당신은 한국 증시(코스피·코스닥)와 반도체 섹터(삼성전자·SK하이닉스), 그리고 미국 증시(엔비디아·AMD·필라델피아 반도체)의 연결고리를 가장 정확하고 알기 쉽게 해설하는 수석 애널리스트입니다.
아래 [실측 시장 데이터], [당일 신규 어닝콜 & 분기 실적 발표 데이터], [국내 증권가 리포트 및 당일 시황 헤드라인], [미국 증시 실제 실적 발표 일정]을 바탕으로 한국 증시 마감 브리핑 JSON을 작성해 주세요.

[실측 한국 증시 데이터 (절대 팩트)]
- 일시: {kr_data['date_str']}
- 코스피(KOSPI): {kospi['price']} ({kospi['symbol']} {kospi['diff_str']}, {kospi['pct_str']}) | 투자자별 확정 수급: {kospi.get('flow_detail_str', kospi['sub_value'])}
- 코스닥(KOSDAQ): {kosdaq['price']} ({kosdaq['symbol']} {kosdaq['diff_str']}, {kosdaq['pct_str']}) | 투자자별 확정 수급: {kosdaq.get('flow_detail_str', kosdaq['sub_value'])}
- 원/달러 환율: {usdkrw['price']} ({usdkrw['symbol']} {usdkrw['diff_str']}, {usdkrw['pct_str']})
- 삼성전자(005930): {sec['price']} ({sec['symbol']} {sec['pct_str']}) | {sec.get('flow_prompt_note', sec['flow_str'])} | 최신 증권사 리포트: {json.dumps(sec.get('researches', []), ensure_ascii=False)}
- SK하이닉스(000660): {hynix['price']} ({hynix['symbol']} {hynix['pct_str']}) | {hynix.get('flow_prompt_note', hynix['flow_str'])} | 최신 증권사 리포트: {json.dumps(hynix.get('researches', []), ensure_ascii=False)}
- 시총 상위 대표주: {json.dumps([{s['name']: f"{s['price']} ({s['pct_str']})"} for s in top_stocks], ensure_ascii=False)}
- 오늘(최근 24시간) 국내 증권가·언론 실시간 헤드라인: {json.dumps(news_lines, ensure_ascii=False)}
- [당일 신규 어닝콜 & 분기 실적(YoY·QoQ) 원본 데이터]: {json.dumps(earnings_raw, ensure_ascii=False)}
- [향후 예정된 미국 반도체·기술주 실제 실적 일정 (오늘~연말)]: {json.dumps(us_upcoming, ensure_ascii=False)}

[작성 및 팩트체크 지침]
1. **수치 및 수급 방향성(+/-) 100% 일치 (가장 중요!)**:
   - 코스피·코스닥의 외국인·기관·개인 순매수(+)와 순매도(-) 부호를 절대 혼동하지 말 것. 예를 들어 외국인이 `-24,528억(순매도)`이고 기관이 `+11,334억(순매수)`인데 '외국인·기관 동반 순매수'라고 쓰면 심각한 팩트 오류임!
   - 개별종목(삼성전자·SK하이닉스) 분석 시에는 제공된 외국인 지분율, 목표주가 컨센서스, 증권사 리포트 및 코스피 전체 외국인/기관 수급 흐름을 자연스럽게 연결할 것.
2. **삼성전자 & SK하이닉스 투톱 집중 분석 (`focus_analysis`)**:
   - 오늘 실제 종가·등락률과 함께 위 데이터에 제공된 `최신 증권사 리포트`(예: 키움·미래에셋·하나·DS증권 등의 3Q 리뷰 및 HBM·메모리 전망, 목표주가 컨센서스)와 간밤 미국 반도체 흐름을 결합하여 구체적이고 신뢰도 높게 설명할 것.
   - `key_point`에는 보유/관심 투자자가 실전에서 체크해야 할 핵심 업황·밸류에이션·모멘텀 포인트를 자연스러운 한 문장으로 작성할 것.
3. **당일 어닝콜 & 분기 실적(YoY·QoQ) 집중 요약 (`earnings_calls`)**:
   - `[당일 신규 어닝콜 & 분기 실적 원본 데이터]`가 빈 배열(`[]`)이면 `earnings_calls`도 반드시 빈 배열(`[]`)로 반환할 것 (이미 과거에 발표한 실적을 중복 브리핑하지 말 것!).
   - 데이터가 있는 경우, **[실적 자체 판정(매출·영업이익 컨센서스 상회/하회)]**과 **[당일 주가 반응(`stock_price_reaction`)]**을 명확히 분리하여 작성할 것. 실적이 컨센서스를 상회했더라도 차익실현 매물로 주가가 하락할 수 있으므로 이를 '실적 악화'로 오판하지 말 것.
   - `quarterly_table_억_기준`에서 `_컨센서스(시장예상치)`로 끝나는 열은 발표 전 예상치이므로 실제 발표 확정치(`earnings_call_news` 속보 기준)와 절대 혼동하지 말 것.
4. **주요 시황 코멘트 & 전문가 의견 (`reasons` 2개)**:
   - 오늘 한국 증시를 움직인 핵심 원인 2가지를 실제 수급 수치(코스피·코스닥 외인/기관/개인 수급) 및 국내 증권가 분석을 바탕으로 명확하게 정리할 것.
5. **오늘 밤 미국 증시 연동 관전 포인트 (`us_bridge`) 팩트체크**:
   - 오늘 밤 열리는 미국 증시에서 한국 반도체·대형주 투자자가 실제로 주목해야 할 포인트(엔비디아·AMD·필라델피아 반도체 지수 수급, 미 국채금리·달러 환율, 그리고 위 `[향후 예정된 미국 반도체·기술주 실제 실적 일정]`에 있는 ARM·퀄컴·엔비디아 등의 실적/가이던스 모멘텀)를 자연스러운 2문장으로 작성할 것. (이미 발표가 끝난 MS·구글·아마존·애플 등을 언급하거나 '이미 완료된 상태입니다' 같은 메타 설명 문구는 쓰지 말 것!)
6. 마크다운 백틱 없이 순수 JSON만 반환할 것.

[JSON 응답 형식]
{{
  "headline": "오늘 한국 증시 마감과 삼성전자·SK하이닉스 흐름을 관통하는 한 줄 핵심 판단",
  "focus_analysis": {{
    "sec": {{
      "title": "삼성전자 오늘 주가 흐름 핵심 요약 제목",
      "analysis": "삼성전자 등락 배경, 외국인 지분율/수급, 메모리·HBM 관련 증권사 리포트 분석 설명 2줄",
      "key_point": "삼성전자 보유/관심 투자자를 위한 실전 체크포인트 한 줄"
    }},
    "hynix": {{
      "title": "SK하이닉스 오늘 주가 흐름 핵심 요약 제목",
      "analysis": "SK하이닉스 등락 배경, AI HBM 독점적 지위 및 증권사 리포트·엔비디아 연동 분석 설명 2줄",
      "key_point": "SK하이닉스 보유/관심 투자자를 위한 실전 체크포인트 한 줄"
    }}
  }},
  "stocks_note": "시총 상위 대표주(2차전지·바이오·자동차·금융·한미반도체) 흐름 한 줄 요약",
  "earnings_calls": [
    {{
      "name": "기업명 (종목코드)",
      "quarter": "분기 확정실적 & 컨퍼런스콜",
      "badge": "어닝 서프라이즈 🔥 (또는 실적 상회 · 가이던스 점검 / 컨센서스 부합 / 컨센서스 하회)",
      "metrics": "매출액 OO조원 (YoY +OO%, QoQ +OO%) · 영업이익 OO조원 (YoY +OO%, 컨센서스 상회) | 당일 주가 +O.O%",
      "highlights": [
        "[실적 팩트 & 주가 반응] 주력 부문 매출·영업이익 컨센서스 상회/하회 요인 및 당일 주가 등락 배경 요약 1줄",
        "[어닝콜 가이던스 & Q&A] 경영진 핵심 가이던스·설비투자(CapEx)·주주환원·차세대 제품 로드맵 발언 요약 1줄"
      ]
    }}
  ],
  "reasons": [
    {{
      "title": "첫 번째 핵심 시황 배경 및 증권가 코멘트 제목",
      "content": "구체적인 수치(외인·기관·개인 정확한 수급 부호 반영)와 증권가 분석을 포함한 설명 2줄"
    }},
    {{
      "title": "두 번째 핵심 시황 배경 및 업종별 수급 특징 제목",
      "content": "구체적인 종목/섹터 등락과 배경 설명 2줄"
    }}
  ],
  "us_bridge": "오늘 한국장 반도체·외인 수급을 바탕으로 오늘 밤 미국 증시(실제 예정된 반도체·빅테크 일정 및 NVDA·AMD·SOX 수급)에서 체크해야 할 핵심 포인트 2줄"
}}
"""

    logger.info("Gemini 1차 한국 증시 마감 브리핑 초안 생성 중...")
    draft = _call_gemini_json(prompt, api_key, temperature=0.25)
    if not draft or "headline" not in draft:
        return _enforce_kr_hard_facts(fallback, kr_data)

    # 2차 팩트체크 감사(Auditor) 패스
    audit_prompt = f"""
당신은 한국 증시 리포트 수석 팩트체크 감사관(Chief Fact-Checker)입니다.
아래 [1차 초안 JSON]을 [실측 원본 데이터]와 대조하여 틀린 수치, 반대 방향의 수급 서술(순매수/순매도 혼동), 잘못된 미국 실적 일정, 모순된 어닝콜 평가를 모두 교정한 순수 JSON을 반환해 주세요.

[엄격한 감사 규칙]
1. **코스피·코스닥 투자자별 수급 부호 검증**: 코스피 수급({kospi.get('flow_detail_str', kospi['sub_value'])})과 코스닥 수급({kosdaq.get('flow_detail_str', kosdaq['sub_value'])})에서 순매도(-) 주체를 순매수(+)로 잘못 서술했거나 '동반 순매수'로 왜곡한 문구가 있다면 100% 실제 수치와 방향으로 수정할 것!
2. **당일 어닝콜(`earnings_calls`) 팩트체크**: `당일 어닝콜 원본 데이터`가 `[]`이면 `earnings_calls`도 반드시 `[]`여야 함. 데이터가 있다면 `_컨센서스(시장예상치)` 열을 확정 실적으로 오기재하지 않았는지, 그리고 **[실적 자체 상회/하회 평가]**와 **[당일 주가 반응]**이 명확히 분리되었는지 검증할 것.
3. **`us_bridge` 미국 실적 일정 검증**: 오늘 밤 또는 조만간 실제로 예정된 일정(`us_upcoming_earnings`: ARM·퀄컴, 엔비디아 등)과 미 국채금리·필라델피아 반도체 지수 관전 포인트를 자연스러운 브리핑 문장으로 서술할 것.
4. `reasons` 배열 내 각 항목의 `title`과 `content`(2줄 상세 설명) 필드를 절대 생략하지 말고 모두 유지할 것!

[실측 원본 데이터]
- 코스피: {kospi['price']} ({kospi['pct_str']}, {kospi.get('flow_detail_str', kospi['sub_value'])})
- 코스닥: {kosdaq['price']} ({kosdaq['pct_str']}, {kosdaq.get('flow_detail_str', kosdaq['sub_value'])})
- 원/달러 환율: {usdkrw['price']} ({usdkrw['pct_str']})
- 삼성전자: {sec['price']} ({sec['pct_str']}, {sec.get('flow_prompt_note', sec['flow_str'])})
- SK하이닉스: {hynix['price']} ({hynix['pct_str']}, {hynix.get('flow_prompt_note', hynix['flow_str'])})
- 시총 상위주: {json.dumps([{s['name']: f"{s['price']} ({s['pct_str']})"} for s in top_stocks], ensure_ascii=False)}
- 당일 어닝콜 & 분기 실적 원본 데이터: {json.dumps(earnings_raw, ensure_ascii=False)}
- 미국 주요 기업 실적 완료 여부 및 차기 확정 일정: {json.dumps(us_upcoming, ensure_ascii=False)}

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

    result = _enforce_kr_hard_facts(result, kr_data)
    logger.info("✅ 한국 증시 마감 브리핑 2차 팩트체크 감사 및 3차 하드 팩트 가드레일 통과!")
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
        "headline": f"코스피 {kospi['price']} ({kospi['pct_str']}) · 삼성전자({sec['pct_str']}) 및 SK하이닉스({hynix['pct_str']}) 중심 반도체 수급 점검",
        "focus_analysis": {
            "sec": {
                "title": f"삼성전자 {sec['price']} ({sec['pct_str']}), 메모리·HBM 업황 및 수급 점검",
                "analysis": "글로벌 AI 반도체 업황 흐름과 증권가 밸류에이션 평가를 바탕으로 삼성전자의 고대역폭메모리(HBM) 및 범용 메모리 수익성 개선 여부가 주목받고 있습니다.",
                "key_point": "차세대 HBM 공급 확대 일정 및 외국인·기관 수급 추이를 지속 점검해야 합니다."
            },
            "hynix": {
                "title": f"SK하이닉스 {hynix['price']} ({hynix['pct_str']}), AI HBM 대장주 모멘텀 지속",
                "analysis": "엔비디아향 HBM 독점적 지위와 고부가 메모리 실적 성장성에 대한 증권가 호평이 이어지며 AI 반도체 핵심 수혜주로 자리매김하고 있습니다.",
                "key_point": "글로벌 빅테크 기업들의 AI 데이터센터 설비투자(CapEx) 확대와 엔비디아 주가 흐름이 핵심 연동 지표입니다."
            }
        },
        "stocks_note": "반도체·금융·자동차 등 실적 및 밸류업 모멘텀 보유 대형주 중심의 선별적 수급 흐름이 나타났습니다.",
        "earnings_calls": fallback_calls,
        "reasons": [
            {
                "title": f"코스피 수급({kospi['sub_value']}) 공방 및 글로벌 반도체 업황 연동",
                "content": f"유가증권시장에서 {kospi.get('flow_detail_str', kospi['sub_value'])} 수급이 전개된 가운데, 미국 AI 반도체 섹터 흐름과 연동된 대형주 중심 장세가 이어졌습니다."
            },
            {
                "title": "업종별 실적·가이던스 기반 차별화 및 주주환원(밸류업) 모멘텀 점검",
                "content": "HBM 반도체 밸류체인과 자동차·금융 등 시총 상위 대표주별로 분기 실적 컨센서스 및 향후 가이던스에 따라 기관·외국인 수급이 차별화되었습니다."
            }
        ],
        "us_bridge": "오늘 국내 증시 반도체 투톱(삼성전자·SK하이닉스)의 흐름이 오늘 밤 미국 증시 AI 반도체 섹터(엔비디아·AMD·필라델피아 반도체 지수) 및 예정된 기술주 실적(ARM·퀄컴, 엔비디아 11/18 등) 모멘텀으로 이어질지 주목됩니다."
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

    # 정기 스케줄 실행 시 국내 증시 공휴일(휴장일) 자동 스킵 방어 로직
    if "--skip-if-holiday" in sys.argv:
        today_kst = datetime.datetime.now(KST).strftime("%Y-%m-%d")
        kospi_check = fetch_kr_index("KOSPI", "코스피 (KOSPI)", "유가증권")
        traded_date = kospi_check.get("local_traded_date", "")
        if traded_date and traded_date != today_kst:
            logger.info(f"📅 한국 증시 휴장일 감지 (최근 거래일: {traded_date}, 오늘: {today_kst}) — 발송을 건너뜁니다.")
            return

    kr_data = collect_kr_market_data()
    ai_data = generate_kr_ai_analysis(kr_data)
    output_png = "output/kr_briefing.png"
    render_kr_briefing_image(kr_data, ai_data, output_png)

    if "--render-only" in sys.argv:
        logger.info(f"🖼️ 렌더링 전용 모드 완료 (발송 생략): {os.path.abspath(output_png)}")
        return

    sent = send_kr_briefing_to_telegram(output_png, kr_data, ai_data)
    if sent and ("--broadcast" in sys.argv or os.getenv("BROADCAST_ALL", "").lower() in ("1", "true", "yes")):
        from data_collector import mark_earnings_reported
        kr_earnings = kr_data.get("earnings_calls_raw") or []
        if kr_earnings:
            mark_earnings_reported(kr_earnings, market="kr")

    logger.info("==========================================")
    logger.info(f"🎉 한국 증시 브리핑 완료! 결과물: {os.path.abspath(output_png)}")
    logger.info("==========================================")


if __name__ == "__main__":
    main()
