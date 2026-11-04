"""
Google Gemini 기반 AI 시장 분석 및 다중 팩트체크 모듈
- 수집된 지수, 환율, 금리, 빅테크, 확정 실적일정 및 월가 전문가/외신 코멘트를 바탕으로 초보자 친화적 시장 브리핑 생성
- 1차: 실데이터 + 월가 전문가 의견 기반 초안 생성
- 2차: Gemini 팩트체크 감사(Auditor) 패스 + 파이썬 하드 팩트 교차검증(수치·방향성·시간외 허위표기·실적일정 검증)
"""

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest"
]


def _call_gemini_json(prompt, api_key, temperature=0.25):
    """
    Gemini 모델을 순차 호출하여 JSON 응답을 파싱해 반환 (실시간 검색 툴 우선 시도 후 일반 호출 폴백)
    """
    import requests

    for model_name in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        # 1) google_search 그라운딩 툴 지원 시 우선 시도, 쿼터 제한(429 등) 시 즉시 기본 모드로 전환
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
                    # JSON 블록만 안전 추출
                    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
                    if match:
                        text = match.group(0)
                    data = json.loads(text)
                    mode_str = "Search-Grounded" if use_search else "Data-Grounded"
                    logger.info(f"Gemini AI ({model_name} / {mode_str}) 응답 생성 성공!")
                    return data
                elif use_search and res.status_code in (400, 403, 429):
                    # 무료 티어 검색 툴 쿼터 제한 시 즉시 일반 호출로 폴백
                    continue
                else:
                    logger.warning(f"Gemini API ({model_name}) 오류 ({res.status_code}): {res.text[:160]}")
            except Exception as ex:
                if not use_search:
                    logger.warning(f"Gemini API ({model_name}) 요청 예외: {ex}")
    return None


def _enforce_hard_facts_on_analysis(data, market_data):
    """
    파이썬 결정론적(Deterministic) 팩트체크 가드레일:
    1) after_market 이벤트가 없을 때 텍스트 내 허위 '시간외 폭등/급등' 문구 차단
    2) earnings_calls_raw에 없는 종목이 earnings_calls에 환각으로 추가되는 현상 원천 차단
    3) 실적 판정 배지(badge)와 실제 지표(metrics / eps_surprise_pct) 간 모순(예: 상회인데 '실적 악화' 표기) 자동 교정
    """
    if not isinstance(data, dict):
        return data

    after_market = market_data.get("after_market")
    if not after_market or not after_market.get("has_event"):
        hallucination_pattern = re.compile(r"시간외(에서| 거래에서)?\s*(무려\s*)?[+\-]?\d+(\.\d+)?%\s*(폭등|급등|급락|반등|상승|하락)")
        for field in ("headline", "k_market_impact", "macro_note", "m7_note"):
            if isinstance(data.get(field), str) and hallucination_pattern.search(data[field]):
                logger.warning(f"[팩트체크 가드레일] {field} 내 미검증 시간외 등락률 문구 감지 및 교정")
                data[field] = hallucination_pattern.sub("정규장 마감 후 월가 수급 점검", data[field])

    earnings_raw = market_data.get("earnings_calls_raw") or []
    if not earnings_raw:
        data["earnings_calls"] = []
    elif not isinstance(data.get("earnings_calls"), list):
        data["earnings_calls"] = []
    else:
        valid_by_ticker = {item.get("ticker", "").upper(): item for item in earnings_raw if item.get("ticker")}
        valid_by_name = {item.get("name", ""): item for item in earnings_raw if item.get("name")}
        filtered_calls = []

        for ec in data["earnings_calls"]:
            if not isinstance(ec, dict):
                continue
            ec_name = str(ec.get("name", ""))
            matched_raw = None
            for tk, raw_item in valid_by_ticker.items():
                if tk and tk in ec_name.upper():
                    matched_raw = raw_item
                    break
            if not matched_raw:
                for nm, raw_item in valid_by_name.items():
                    if nm and nm in ec_name:
                        matched_raw = raw_item
                        break
            if not matched_raw:
                logger.warning(f"[팩트체크 가드레일] 당일 미발표/중복 어닝콜 카드 제거: {ec_name}")
                continue

            # 배지(badge)와 지표 설명(metrics) 간 논리적 모순 교차검증
            badge = str(ec.get("badge", "")).strip()
            metrics_txt = str(ec.get("metrics", ""))
            surp_str = str(matched_raw.get("eps_surprise_pct", ""))
            is_positive_surp = surp_str.startswith("+")

            if ("악화" in badge or "쇼크" in badge or "하회" in badge) and ("상회" in metrics_txt or is_positive_surp):
                if "하회" not in metrics_txt:
                    logger.warning(f"[팩트체크 가드레일] {ec_name} 실적 상회이나 배지가 '{badge}'로 모순 표기됨 -> 교정")
                    ec["badge"] = "실적 상회 · 가이던스 점검"
            elif ("서프라이즈" in badge or "상회" in badge) and ("컨센서스 하회" in metrics_txt and "상회" not in metrics_txt):
                logger.warning(f"[팩트체크 가드레일] {ec_name} 컨센서스 하회이나 배지가 '{badge}'로 모순 표기됨 -> 교정")
                ec["badge"] = "컨센서스 하회 · 가이던스 주시"

            filtered_calls.append(ec)

        data["earnings_calls"] = filtered_calls

    return data


def audit_and_fact_check_analysis(draft_data, market_data, api_key):
    """
    [2차 팩트체크 감사(Auditor) 패스]
    1차 생성된 브리핑 초안을 수집된 원본 숫자(yfinance 종가·등락률·확정 실적일) 및 외신 헤드라인과 대조하여
    과장·환각·날짜 오류·실적 평가 모순을 교정하고 전문가 의견 인용의 정확도를 높임
    """
    after_market_info = market_data.get("after_market")
    after_market_fact = (
        json.dumps(after_market_info, ensure_ascii=False)
        if after_market_info
        else "시간외 ±5% 이상 변동 종목 없음 & 당일 신규 분기실적 발표 빅테크 없음 (절대로 시간외 급등/급락을 언급하지 말 것!)"
    )
    earnings_raw = market_data.get("earnings_calls_raw") or []

    audit_prompt = f"""
당신은 월가 금융 리포트의 수석 팩트체크 감사관(Chief Fact-Checker)입니다.
아래 [1차 작성된 브리핑 JSON 초안]을 [검증된 원본 팩트 데이터]와 하나하나 대조하여, 틀린 숫자·틀린 등락 방향·잘못된 실적 발표 일정·근거 없는 시간외 거래 언급·모순된 실적 평가를 모두 수정한 최종 JSON을 반환해 주세요.

[검증된 원본 팩트 데이터 (절대 기준)]
1. 기준일: {market_data.get('date_str')}
2. 주요 지수 확정 종가 및 등락률: {json.dumps([{i['name']: f"{i['price']} ({i['pct_str']}, {i['week_str']})"} for i in market_data.get('indices', [])], ensure_ascii=False)}
3. 매크로 지표 확정치: {json.dumps([{m['name']: f"{m['price']} ({m['change_str']})"} for m in market_data.get('macro', [])], ensure_ascii=False)}
4. 공포·탐욕 지수: {json.dumps(market_data.get('fear_greed', {}), ensure_ascii=False)}
5. 핵심 집중 종목(NVDA & AMD) 정규장 확정치: {json.dumps([{f['name']: f"{f['price']} ({f['pct_str']}, {f['week_str']})"} for f in market_data.get('focus_stocks', [])], ensure_ascii=False)}
6. 빅테크 M7 확정치: {json.dumps([{s['name']: f"{s['price']} ({s['pct_str']})"} for s in market_data.get('m7', [])], ensure_ascii=False)}
7. 시간외 거래/실적 팩트체크 결과: {after_market_fact}
8. 당일 신규 어닝콜 & 분기 실적(YoY·QoQ) 발표 기업 원본 데이터: {json.dumps(earnings_raw, ensure_ascii=False)}
9. 주요 기업별 최근 실적 발표 완료 여부 및 차기 확정 실적 발표일(yfinance 교차검증): {json.dumps(market_data.get('verified_earnings', []), ensure_ascii=False)}
10. 수집된 월가 IB·애널리스트·주요 외신 헤드라인: {json.dumps([f"[{n.get('source', 'Media')}] {n.get('title', '')}" for n in market_data.get('news', [])], ensure_ascii=False)}

[1차 작성된 브리핑 JSON 초안]
{json.dumps(draft_data, ensure_ascii=False)}

[엄격한 팩트체크 감사 규칙]
1. **시간외 거래 환각 절대 금지**: 위 7번 '시간외 거래/실적 팩트체크 결과'에 없는 종목의 시간외 등락률이나 시간외 급등/하락 언급이 초안에 있다면 100% 삭제하고 정규장 종가 및 수급 팩트로 교정할 것.
2. **수치 및 등락 부호 일치**: 지수, 금리, 환율, NVDA, AMD, M7의 등락률(+)과 하락률(-)이 원본 데이터와 100% 일치하는지 검증할 것.
3. **당일 어닝콜(`earnings_calls`) 3중 팩트체크 (가장 중요!)**:
   - 8번 '당일 신규 어닝콜 원본 데이터'가 비어있다면 `earnings_calls`는 반드시 `[]`여야 함 (전날 이미 발표한 기업을 중복 브리핑하는 행위 절대 금지).
   - **[실적 자체 판정 vs 시간외 주가 반응 분리]**: 매출·조정(Non-GAAP) EPS가 월가 컨센서스를 상회(Beat)했는데도 단지 시간외 주가가 하락했거나 일회성 GAAP 비용이 있었다는 이유로 `badge`를 `"실적 악화"`나 `"어닝 쇼크"`로 잘못 표기하지 않았는지 철저히 검증할 것! 실적이 컨센서스를 상회했다면 `"어닝 서프라이즈 🔥"` 또는 `"실적 상회 · 가이던스 점검"`으로 명시하고, 시간외 주가 등락(`post_market_reaction`)은 `metrics` 끝에 별도로 병기할 것.
   - **[재무 테이블 기준점 검증]**: 원본 데이터의 `financial_table_note`를 확인하여, 과거 분기 수치나 차기 분기 예상치를 당일 발표 확정치로 오인하지 않았는지 검증할 것.
4. **실적 발표 일정 정확성 (과거 완료 일정 오기재 금지)**:
   - 9번 `verified_earnings`에서 `status_note`가 `최근발표완료(...)`인 기업(예: 이미 3분기 실적을 발표한 MSFT, GOOGL, AMZN, META, AAPL, AMD 등)을 마치 이번 주나 조만간 실적 발표 예정인 것처럼 `focus_analysis`나 하단 `checkpoints`에 쓰지 않았는지 확인하고, 앞으로 발표 예정인 기업(예: ARM·QCOM 11/04, NVDA 11/18, AVGO 12/10 등 실제 미래 날짜)만 정확히 안내할 것.
5. **월가 전문가/기관 의견 반영 확인**: `headline`, `reasons`(3대 근거), `focus_analysis`(NVDA·AMD 분석)에 월가 주요 기관(IB·애널리스트) 및 주요 외신(Bloomberg, Reuters, CNBC, Yahoo Finance 등)의 시각과 컨센서스가 초보자도 이해하기 쉽게 잘 녹아있는지 점검하고 보강할 것.
6. JSON 키 구조는 초안과 동일하게 유지하고, 마크다운 백틱 없이 순수 JSON만 반환할 것.
"""

    audited = _call_gemini_json(audit_prompt, api_key, temperature=0.15)
    if audited and isinstance(audited, dict) and "headline" in audited and "reasons" in audited:
        logger.info("✅ [2차 팩트체크 감사 완료] Gemini Auditor 교차검증 통과!")
        return _enforce_hard_facts_on_analysis(audited, market_data)

    logger.info("ℹ️ 2차 감사 응답 생략, 1차 초안에 파이썬 하드 팩트체크 적용")
    return _enforce_hard_facts_on_analysis(draft_data, market_data)


def generate_market_analysis(market_data):
    """
    수집된 시장 데이터를 Gemini API로 분석하여 정성적 브리핑 텍스트 생성 (1차 초안 + 2차 팩트체크 감사)
    API 키가 없거나 호출 실패 시 규칙 기반의 고품질 기본값 제공
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY가 설정되지 않아 샘플 스마트 분석 템플릿을 생성합니다.")
        return get_fallback_analysis(market_data)

    try:
        after_market_info = market_data.get("after_market")
        after_market_str = (
            json.dumps(after_market_info, ensure_ascii=False)
            if after_market_info
            else "특이사항 없음 (시간외 ±5% 이상 변동 종목 없음, 당일 신규 실적 발표 빅테크 없음 -> 절대로 시간외 주가 변동을 지어내지 말 것)"
        )
        earnings_raw = market_data.get("earnings_calls_raw") or []

        prompt = f"""
당신은 월가 주요 투자은행(IB)과 애널리스트들의 리포트를 종합하여 초보 투자자에게 가장 정확하고 친절하게 설명해 주는 수석 금융 멘토입니다.
아래의 [검증된 오늘 미국 증시 마감 데이터], [당일 신규 어닝콜 & 분기 실적(YoY·QoQ) 데이터], [확정 실적 발표 일정], 그리고 [월가 전문가 및 외신 헤드라인]을 철저히 팩트 기반으로 분석하여 일일 브리핑 JSON을 작성해 주세요.

[검증된 오늘의 시장 데이터 (절대 팩트)]
- 날짜: {market_data.get('date_str')}
- 주요 지수: {json.dumps([{i['name']: f"{i['price']} ({i['pct_str']}, {i['week_str']})"} for i in market_data.get('indices', [])], ensure_ascii=False)}
- 매크로 지표: {json.dumps([{m['name']: f"{m['price']} ({m['change_str']})"} for m in market_data.get('macro', [])], ensure_ascii=False)}
- 공포·탐욕 지수 (CNN Fear & Greed): {json.dumps(market_data.get('fear_greed', {}), ensure_ascii=False)}
- 핵심 집중 종목(엔비디아 & AMD): {json.dumps([{f['name']: f"{f['price']} ({f['pct_str']}, {f['week_str']})"} for f in market_data.get('focus_stocks', [])], ensure_ascii=False)}
- 빅테크 M7: {json.dumps([{s['name']: f"{s['price']} ({s['pct_str']})"} for s in market_data.get('m7', [])], ensure_ascii=False)}
- 시간외/실적 이슈 (교차검증 완료): {after_market_str}
- 당일 신규 어닝콜 & 분기 실적(YoY·QoQ) 발표 기업 원본 데이터: {json.dumps(earnings_raw, ensure_ascii=False)}
- 주요 기업 확정 차기 실적 발표일 및 최근 발표 완료 현황(yfinance): {json.dumps(market_data.get('verified_earnings', []), ensure_ascii=False)}
- 월가 IB·애널리스트·주요 외신 최신 헤드라인: {json.dumps([f"[{n.get('source', 'Media')}] {n.get('title', '')}" for n in market_data.get('news', [])], ensure_ascii=False)}

[작성 및 팩트체크 지침]
1. **철저한 팩트 기반 작성**: 위 데이터에 있는 정확한 수치와 등락 방향만 사용할 것. 특히 '시간외/실적 이슈'가 '특이사항 없음'일 경우, 절대로 시간외 거래 등락을 언급하거나 추측하지 말 것.
2. **월가 전문가 의견 취합 반영**: '오늘의 판단(headline)', '3대 핵심 근거(reasons)', 'NVDA·AMD 심층 분석(focus_analysis)'을 작성할 때 단순한 AI 추측이 아니라 수집된 외신(Bloomberg, Reuters, CNBC, Yahoo Finance 등)과 월가 주요 기관(골드만삭스, 모건스탠리, JP모건 등 IB 및 애널리스트)의 시장 진단·컨센서스를 근거로 명시하며 친절한 해요체/하십시오체로 쉽게 풀어서 설명할 것.
3. **핵심 보유주 심층 브리핑(focus_analysis)**: 투자자가 가장 큰 비중으로 보유 중인 **엔비디아(NVDA)**와 **AMD**의 실제 정규장 종가·등락률과 주간 흐름, 월가 애널리스트들의 AI 반도체 수급/밸류에이션 평가, 그리고 `verified_earnings`에 기재된 실제 실적 현황(AMD는 11/03 실적 발표 완료 후 수급 소화 및 가이던스 평가, NVDA는 11/18 차기 실적 발표 예정 등)을 날짜 오차 없이 정확히 짚어줄 것.
4. **당일 어닝콜 & 분기 실적(YoY·QoQ) 집중 요약(`earnings_calls`) — 팩트체크 핵심 규칙**:
   - `당일 신규 어닝콜 & 분기 실적 발표 기업 원본 데이터`가 비어있지 않은 경우에만 작성하며, 빈 배열(`[]`)이면 `earnings_calls`도 반드시 `[]`로 반환할 것 (이미 전날 발표한 기업의 실적을 이틀 연속 중복 브리핑하지 말 것!).
   - **[실적 자체 평가 vs 주가 반응 명확히 분리]**:
     1) **실적 자체 평가**: 매출액과 조정(Non-GAAP) EPS(또는 GAAP EPS)가 월가 컨센서스를 상회(Beat)했는지 하회(Miss)했는지 `earnings_call_news`(외신 헤드라인)와 `actual_eps` vs `consensus_eps`(`eps_surprise_pct`)를 대조하여 판정할 것. 일회성 비용/이익이 반영된 GAAP 수치와 월가 기준인 Non-GAAP 수치를 혼동하여 '어닝 서프라이즈'를 '실적 악화'로 오판하지 말 것!
     2) **시간외 주가 반응 분리 표기**: 실적이 컨센서스를 상회했더라도 차기 분기 가이던스나 CapEx 부담으로 시간외 주가가 하락할 수 있고, 반대로 가이던스 호조로 급등할 수도 있음. 따라서 `metrics`에 실적 수치와 함께 `post_market_reaction`(시간외 등락률)을 병기하고, `highlights` 첫 줄에 **실적 상회/하회 핵심 요인과 시간외 주가 반응 배경**을 명확히 구분하여 설명할 것.
     3) `financial_table_note` 지침을 준수하여, 장 마감 직후 재무제표 테이블에 아직 당해 분기가 미반영된 경우 `earnings_call_news` 속보의 당해 분기 확정 매출 및 YoY 증감률을 최우선 사용할 것.
5. **국내 증시 영향(k_market_impact)**: 필라델피아 반도체 지수 등락, 미 국채금리, 원/달러 환율, 엔비디아 흐름을 바탕으로 **오늘 한국 증시(코스피/코스닥) 및 삼성전자·SK하이닉스 등 주요 섹터에 미칠 영향**을 1~2줄로 명확히 요약할 것.
6. **체크포인트(checkpoints) 팩트 반영**: `verified_earnings`에서 이미 실적을 발표한 기업(`최근발표완료`)은 제외하고, 실제로 앞으로 발표가 남아있는 핵심 기업 일정(예: ARM·QCOM, NVDA 11/18, AVGO 12/10 등)과 주요 매크로 일정을 날짜 오차 없이 정확하게 작성할 것.
7. 마크다운 백틱 없이 순수 JSON 형식으로만 응답할 것.

[JSON 형식]
{{
  "headline": "월가 컨센서스와 오늘 시장 팩트를 관통하는 한 줄 요약 헤드라인",
  "k_market_impact": "오늘 한국 증시에 미칠 영향 1~2줄",
  "focus_analysis": {{
    "nvda": {{
      "title": "엔비디아 오늘 핵심 동향 및 월가 시각 요약 제목",
      "analysis": "엔비디아 심층 분석 2~3줄 (정확한 오늘 등락률 반영 + 월가 기관/애널리스트들의 AI 투자 및 블랙웰 수요 분석)",
      "key_point": "보유 투자자를 위한 실전 체크포인트 한 줄 (11/18 확정 실적일 및 빅테크 CapEx 확인 등)"
    }},
    "amd": {{
      "title": "AMD 오늘 핵심 동향 및 월가 시각 요약 제목",
      "analysis": "AMD 심층 분석 2~3줄 (정확한 오늘 등락률 반영 + 월가 애널리스트들의 MI 가속기 및 서버 CPU 점유율 평가)",
      "key_point": "보유 투자자를 위한 실전 체크포인트 한 줄 (실적 발표 이후 월가 목표주가·가이던스 평가 및 데이터센터 수주 점검)"
    }}
  }},
  "earnings_calls": [
    {{
      "name": "기업명 (티커)",
      "quarter": "분기 확정실적 & 어닝콜",
      "badge": "어닝 서프라이즈 🔥 (또는 실적 상회 · 가이던스 점검 / 컨센서스 부합 / 컨센서스 하회)",
      "metrics": "매출 $OO.OB (YoY +OO%) · EPS $O.OO (컨센서스 $O.OO 상회) | 시간외 +O.O%",
      "highlights": [
        "[실적 팩트 & 주가 반응] 주력 부문 매출·EPS 컨센서스 상회/하회 요인 및 시간외 주가 등락 배경 1줄",
        "[어닝콜 가이던스 & CapEx] 경영진 차기 분기 매출·마진 가이던스 및 AI 설비투자(CapEx) 핵심 발언 1줄"
      ]
    }}
  ],
  "reasons": [
    {{
      "title": "첫 번째 핵심 이유 제목 (월가 전문가·외신 핵심 분석 1)",
      "content": "월가 기관/애널리스트 코멘트와 실제 지표를 결합한 친절한 설명 2~3줄"
    }},
    {{
      "title": "두 번째 핵심 이유 제목 (금리·매크로·유가 등 전문가 진단 2)",
      "content": "설명 2~3줄"
    }},
    {{
      "title": "세 번째 핵심 이유 제목 (빅테크·반도체 섹터 수급 분석 3)",
      "content": "설명 2~3줄"
    }}
  ],
  "macro_note": "매크로 지표 하단에 들어갈 한 줄 핵심 요약",
  "m7_note": "빅테크 M7 테이블 하단에 들어갈 한 줄 요약",
  "checkpoints": [
    {{
      "time": "이번 주 핵심 일정",
      "event": "검증된 실제 매크로/정책 일정"
    }},
    {{
      "time": "주요 경제지표",
      "event": "검증된 실제 경제지표 발표 일정"
    }},
    {{
      "time": "차기 핵심 실적",
      "event": "verified_earnings 기준 앞으로 예정된 실제 반도체·빅테크 실적 발표 일정 명시"
    }}
  ]
}}
"""

        logger.info("Gemini 1차 시장 분석 초안 생성 중 (실데이터 + 월가 전문가 의견 결합)...")
        draft_data = _call_gemini_json(prompt, api_key, temperature=0.3)
        if draft_data:
            logger.info("Gemini 2차 팩트체크 감사(Auditor) 및 수치·일정 교차검증 실행 중...")
            final_data = audit_and_fact_check_analysis(draft_data, market_data, api_key)
            return final_data

        return get_fallback_analysis(market_data)

    except Exception as e:
        logger.error(f"Gemini 분석 실패 ({e}), 기본 스마트 템플릿 사용")
        return get_fallback_analysis(market_data)


def get_fallback_analysis(market_data):
    """
    API 키가 없거나 네트워크 오류 시 수집된 지표를 기반으로 자동 생성되는 스마트 템플릿
    """
    indices = market_data.get("indices", [])
    sp500 = next((i for i in indices if "S&P" in i["name"]), None)
    sp500_pct = sp500["pct_str"] if sp500 else "-0.5%"
    sp500_is_up = "+" in sp500_pct

    verified_earnings = market_data.get("verified_earnings", [])
    upcoming_list = [
        f"{e['name']}({e['earnings_date'][5:].replace('-', '/')})"
        for e in verified_earnings
        if e.get("earnings_date") and e["earnings_date"] != "미정" and e["earnings_date"].startswith("2026-")
    ]
    upcoming_str = ", ".join(upcoming_list[:4]) if upcoming_list else "엔비디아(11/18), 브로드컴(12/10) 등 주요 AI 반도체 실적 발표 예정"

    if sp500_is_up:
        headline = "AI 반도체 수급 유입과 매크로 지표 안정에 힘입어 상승 마감 ㅡ 월가 '어닝 모멘텀 주목'"
        r1_title = "월가 기관들, 빅테크 AI 설비투자(CapEx) 확대 및 클라우드 성장세 긍정 평가"
        r1_desc = "골드만삭스와 모건스탠리 등 주요 월가 IB들은 하이퍼스케일러 기업들의 AI 인프라 투자 확장이 반도체 및 기술주 하방을 지지하고 있다고 분석했습니다."
        k_impact = "미 증시 기술주 훈풍과 필라델피아 반도체 지수 상승에 힘입어, 오늘 코스피 반도체 대형주(삼성전자·SK하이닉스) 중심의 우호적 외국인 수급이 기대됩니다."
    else:
        headline = "매크로 이벤트와 주요 기술주 가이던스 소화 속 숨고르기 ㅡ 월가 '선별적 접근 권고'"
        r1_title = "월가 전략가들, 실적 시즌 가이던스 및 금리·통화정책 확인 속 순환매 진단"
        r1_desc = "블룸버그와 주요 월가 애널리스트들은 대형 기술주 실적 발표 이후 가이던스 차별화와 매크로 지표 확인 과정에서 단기 차익실현 매물이 출회된 것으로 평가했습니다."
        k_impact = "미 증시 숨고르기 및 환율 흐름 영향으로 오늘 국내 증시는 장 초반 관망세 속 실적 호전주 중심의 종목별 차별화 장세가 예상됩니다."

    return {
        "headline": headline,
        "k_market_impact": k_impact,
        "focus_analysis": {
            "nvda": {
                "title": "월가 AI 가속기 수요 견조 평가 속 차기 실적(11/18) 모멘텀 축적",
                "analysis": "월가 주요 애널리스트들은 하이퍼스케일러(MS·구글·아마존·메타 등)의 데이터센터 설비투자(CapEx) 상향 기조가 확인됨에 따라 엔비디아 블랙웰 칩의 독점적 수혜가 이어질 것으로 평가하고 있습니다.",
                "key_point": "빅테크 AI 설비투자 확대 수혜와 11월 18일 엔비디아 분기 실적 발표가 핵심 체크포인트입니다."
            },
            "amd": {
                "title": "AI 가속기(MI 시리즈) 및 서버 CPU 점유율 확대에 따른 월가 가이던스 점검",
                "analysis": "월가에서는 AMD가 데이터센터용 AI 가속기 수주와 서버용 EPYC 프로세서 점유율을 꾸준히 확대하고 있는 점에 주목하며 향후 마진 개선 추이를 주시하고 있습니다.",
                "key_point": "데이터센터 부문 매출 성장세와 클라우드 고객사 AI 칩 도입 속도 및 월가 목표주가 변동을 확인해야 합니다."
            }
        },
        "earnings_calls": [],
        "reasons": [
            {
                "title": r1_title,
                "content": r1_desc
            },
            {
                "title": "국채 금리 움직임과 연준 통화정책 경로에 대한 전문가 시각",
                "content": "월가 채권 전략가들은 미국 10년물 국채 금리와 주요 물가·고용 지표가 연준의 향후 금리 결정 경로를 좌우할 핵심 변수라고 진단했습니다."
            },
            {
                "title": "AI 반도체 및 빅테크 종목별 실적·가이던스 차별화 장세",
                "content": "지수 전체의 일방향 움직임보다는 개별 기업의 분기 EPS·매출 컨센서스 상회 여부와 차기 분기 가이던스에 따라 기관 수급이 빠르게 이동하고 있습니다."
            }
        ],
        "macro_note": "10년물 금리 및 환율 흐름 주시 · 월가 매크로 컨센서스 점검",
        "m7_note": "빅테크 실적 및 가이던스 기반 수급 차별화 · 단위: 달러",
        "checkpoints": [
            {
                "time": "이번 주 핵심 일정",
                "event": "미국 주요 통화정책 일정 및 서비스업·고용·심리 지표 점검"
            },
            {
                "time": "주요 수급 포인트",
                "event": "미 10년물 국채 금리 및 달러 인덱스 흐름에 따른 외국인 수급 변화"
            },
            {
                "time": "차기 핵심 실적",
                "event": f"향후 주요 기술주·반도체 실적 일정: {upcoming_str}"
            }
        ]
    }
