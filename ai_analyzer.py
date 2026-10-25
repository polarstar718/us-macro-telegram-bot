"""
Google Gemini 기반 AI 시장 분석 모듈
- 수집된 지수, 환율, 금리, 빅테크 및 뉴스를 바탕으로 초보자 친화적 시장 브리핑 생성
- '오늘의 판단' 1줄 요약, '왜 이렇게 판단했나' 3대 핵심 근거, 주요 체크포인트 추출
"""

import json
import logging
import os
import re

logger = logging.getLogger(__name__)


def generate_market_analysis(market_data):
    """
    수집된 시장 데이터를 Gemini API로 분석하여 정성적 브리핑 텍스트 생성
    API 키가 없거나 호출 실패 시 규칙 기반의 고품질 기본값 제공
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY가 설정되지 않아 샘플 스마트 분석 템플릿을 생성합니다.")
        return get_fallback_analysis(market_data)

    try:
        # 공식 google-genai SDK 또는 requests를 통한 유연한 호출
        import requests

        prompt = f"""
당신은 미국 주식과 글로벌 매크로 경제를 초보자에게 가장 쉽고 친절하게 설명해 주는 전문 금융 멘토입니다.
아래의 오늘 미국 증시 마감 데이터와 주요 뉴스를 분석하여, 초보 투자자도 바로 이해할 수 있는 일일 브리핑을 작성해 주세요.

[오늘의 시장 데이터]
- 날짜: {market_data.get('date_str')}
- 주요 지수: {json.dumps([{i['name']: f"{i['price']} ({i['pct_str']})"} for i in market_data.get('indices', [])], ensure_ascii=False)}
- 매크로 지표: {json.dumps([{m['name']: f"{m['price']} ({m['change_str']})"} for m in market_data.get('macro', [])], ensure_ascii=False)}
- 핵심 집중 종목(엔비디아 & AMD): {json.dumps([{f['name']: f"{f['price']} ({f['pct_str']}, {f['week_str']})"} for f in market_data.get('focus_stocks', [])], ensure_ascii=False)}
- 빅테크 M7: {json.dumps([{s['name']: f"{s['price']} ({s['pct_str']})"} for s in market_data.get('m7', [])], ensure_ascii=False)}
- 최근 뉴스 헤드라인: {json.dumps([n['title'] for n in market_data.get('news', [])], ensure_ascii=False)}

[작성 지침]
1. 말투: 친절하고 이해하기 쉬운 해요체/하십시오체 혼용 (초보자 맞춤형, 복잡한 전문 용어는 일상적인 비유로 풀어서 설명)
2. 특히 투자자가 가장 큰 비중으로 투자 중인 **엔비디아(NVDA)** 와 **AMD**에 대해 오늘 주가 움직임의 배경과 AI 반도체 수급/경쟁 관점에서 실질적인 도움이 되는 심층 브리핑(focus_analysis)을 반드시 포함할 것.
3. JSON 형식으로만 응답할 것 (추가 설명이나 마크다운 코드블록 백틱 없이 순수 JSON만 반환)

[JSON 형식]
{{
  "headline": "오늘 시장을 관통하는 한 줄 요약 헤드라인",
  "focus_analysis": {{
    "nvda": {{
      "title": "엔비디아 오늘 핵심 동향 요약 제목 (예: AI 가속기 수요 견고, 블랙웰 출하 모멘텀 지속)",
      "analysis": "엔비디아 심층 분석 2~3줄 (오늘 등락 원인, 빅테크의 AI 설비투자 수요, 제품 경쟁력 등 초보자 맞춤 설명)",
      "key_point": "보유 투자자를 위한 실전 체크포인트 한 줄 (예: 차세대 칩 양산 일정 및 빅테크 실적 발표 주시)"
    }},
    "amd": {{
      "title": "AMD 오늘 핵심 동향 요약 제목 (예: MI300 가속기 수주 확대 및 데이터센터 CPU 점유율 증가)",
      "analysis": "AMD 심층 분석 2~3줄 (엔비디아 대항마로서의 가성비 대안 칩 수요, 서버 시장 점유율 등 초보자 맞춤 설명)",
      "key_point": "보유 투자자를 위한 실전 체크포인트 한 줄 (예: 주요 클라우드 고객사 납품 확대 여부 확인)"
    }}
  }},
  "reasons": [
    {{
      "title": "첫 번째 핵심 이유 제목",
      "content": "이유 설명 2~3줄"
    }},
    {{
      "title": "두 번째 핵심 이유 제목",
      "content": "이유 설명 2~3줄"
    }},
    {{
      "title": "세 번째 핵심 이유 제목",
      "content": "이유 설명 2~3줄"
    }}
  ],
  "macro_note": "매크로 지표 하단에 들어갈 한 줄 핵심 요약",
  "m7_note": "빅테크 M7 테이블 하단에 들어갈 한 줄 요약",
  "checkpoints": [
    {{
      "time": "이번 주 주요 일정",
      "event": "일정 내용"
    }},
    {{
      "time": "경제지표 발표",
      "event": "일정 내용"
    }},
    {{
      "time": "빅테크 실적",
      "event": "일정 내용"
    }}
  ]
}}
"""

        models_to_try = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]
        for model_name in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.4}
            }
            try:
                res = requests.post(url, json=payload, timeout=20)
                if res.status_code == 200:
                    result = res.json()
                    text = result["candidates"][0]["content"]["parts"][0]["text"]
                    # JSON 클리닝 (백틱 제거)
                    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
                    text = re.sub(r"```$", "", text.strip(), flags=re.MULTILINE)
                    data = json.loads(text)
                    logger.info(f"Gemini AI ({model_name}) 실시간 분석 브리핑 생성 성공! 🎉")
                    return data
                else:
                    logger.warning(f"Gemini API 모델 ({model_name}) 호출 오류 ({res.status_code}): {res.text}")
            except Exception as ex:
                logger.warning(f"Gemini API 모델 ({model_name}) 요청 중 예외: {ex}")

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

    if sp500_is_up:
        headline = "주요 기술주 매수세와 지표 안정에 힘입어 상승 마감 ㅡ 안도 랠리 지속"
        r1_title = "성장주 중심의 저가 매수세 유입"
        r1_desc = "주요 빅테크 기업들의 탄탄한 실적과 AI 생태계 성장에 대한 기대감이 시장의 투자 심리를 지지했습니다."
    else:
        headline = "금리는 안정세를 찾았지만 경기 지표와 유가 변동성으로 숨고르기 ㅡ 경계 속 관망 국면"
        r1_title = "유가와 원자재 가격 변동성 확대"
        r1_desc = "국제 유가와 주요 원자재 가격이 요동치며 향후 물가에 대한 불확실성이 일부 투자자들의 관망세를 불렀습니다."

    return {
        "headline": headline,
        "focus_analysis": {
            "nvda": {
                "title": "AI 데이터센터 수요 견고하나 숨고르기 양상",
                "analysis": "엔비디아는 차세대 블랙웰 칩의 본격 공급을 앞두고 높은 기대를 받고 있으나, 시장 전반의 금리 및 인플레 경계감에 단기 매물 소화 과정을 거쳤습니다.",
                "key_point": "빅테크 기업들의 AI 캡엑스(설비투자) 유지 여부 및 차세대 칩 양산 속도가 핵심 관전 포인트입니다."
            },
            "amd": {
                "title": "가성비 AI 가속기와 서버 CPU 시장 점유율 확대",
                "analysis": "AMD는 엔비디아의 유일한 대안 가속기로 평가받는 MI300 시리즈의 고객사를 꾸준히 확보하고 있으며, 전통 서버 CPU 시장에서도 인텔 대비 우위를 넓혀가고 있습니다.",
                "key_point": "엔비디아 칩 공급 부족의 수혜를 얼마나 실적 매출로 전환하는지가 주가 반등의 열쇠입니다."
            }
        },
        "reasons": [
            {
                "title": r1_title,
                "content": r1_desc
            },
            {
                "title": "국채 금리 움직임과 통화 정책 주시",
                "content": "미국 10년물 국채 금리가 특정 레벨에서 등락을 거듭하며, 연준의 향후 기준금리 결정 방향에 시장의 관심이 집중되고 있습니다."
            },
            {
                "title": "빅테크 및 반도체 종목별 차별화 장세",
                "content": "지수 전반의 일방적인 움직임보다는 실적과 모멘텀에 따라 종목별로 주가가 엇갈리는 순환매 장세가 나타났습니다."
            }
        ],
        "macro_note": "10년물 금리 및 환율 안정권 유지 · 원자재 가격 흐름 주목",
        "m7_note": "빅테크 종목별 선별적 매수세 유입 · 단위: 달러",
        "checkpoints": [
            {
                "time": "이번 주 주요 일정",
                "event": "미 연준(Fed) FOMC 금리 결정 및 파월 의장 기자회견"
            },
            {
                "time": "경제지표 발표",
                "event": "미국 개인소비지출(PCE) 물가지수 및 3분기 GDP 속보치"
            },
            {
                "time": "빅테크 실적",
                "event": "주요 테크 기업 실적 발표 — AI 및 클라우드 투자 점검"
            }
        ]
    }
