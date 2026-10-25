"""
미국 증시 차주(다음 주) 위클리 캘린더 생성 및 발송 모듈
- 기준: 차주 월~금 미국 매크로 일정, 지표 발표, 연준 발언, 기업 실적
- 5대 고정 컬러 범례: 주요이벤트(연분홍), 정책/통화(그레이), 지표(에메랄드), 실적(스카이블루), 발언(옐로우)
- 중요도 별표(★~★★★) 차등 부여
- 엔비디아(NVDA) 및 AMD 등 핵심 관심주 실적 및 콘퍼런스 집중 체크
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
from jinja2 import Environment, FileSystemLoader

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("WeeklyCalendar")


def get_next_week_dates():
    """
    현재 시점을 기준으로 '다음 주' 월~일 날짜 계산
    """
    now = datetime.datetime.now()
    # 일요일(weekday=6)이면 바로 내일이 다음 주 월요일
    # 토요일(weekday=5)이면 이틀 뒤가 다음 주 월요일
    days_to_next_monday = (7 - now.weekday()) % 7
    if days_to_next_monday == 0:
        days_to_next_monday = 7

    next_mon = now + datetime.timedelta(days=days_to_next_monday)
    next_tue = next_mon + datetime.timedelta(days=1)
    next_wed = next_mon + datetime.timedelta(days=2)
    next_thu = next_mon + datetime.timedelta(days=3)
    next_fri = next_mon + datetime.timedelta(days=4)
    next_sun = next_mon + datetime.timedelta(days=6)

    date_range = f"{next_mon.strftime('%m/%d')}(월) ~ {next_sun.strftime('%m/%d')}(일)"

    return {
        "range_str": date_range,
        "mon": next_mon,
        "tue": next_tue,
        "wed": next_wed,
        "thu": next_thu,
        "fri": next_fri
    }


def generate_weekly_calendar_ai(dates):
    """
    Gemini 3.5 Flash를 이용해 차주 실질적인 매크로 일정 및 기업 실적 캘린더 생성
    """
    api_key = os.getenv("GEMINI_API_KEY")
    mon_s = dates["mon"].strftime("%m월 %d일")
    tue_s = dates["tue"].strftime("%m월 %d일")
    wed_s = dates["wed"].strftime("%m월 %d일")
    thu_s = dates["thu"].strftime("%m월 %d일")
    fri_s = dates["fri"].strftime("%m월 %d일")

    fallback = get_default_calendar_data(dates)

    if not api_key:
        logger.warning("GEMINI_API_KEY 미설정, 기본 스마트 캘린더 데이터 사용")
        return fallback

    prompt = f"""
당신은 월가와 미국 금융 시장의 주간 일정을 가장 정확하고 체계적으로 정리하는 수석 시장 분석가입니다.
아래 지정된 **다음 주(차주)** 날짜에 맞춰, 미국 증시에 큰 영향을 미치는 주요 일정과 실적 발표를 정리한 JSON을 작성해 주세요.

[차주 날짜 정보]
- 월요일: {mon_s}
- 화요일: {tue_s}
- 수요일: {wed_s}
- 목요일: {thu_s}
- 금요일: {fri_s}

[핵심 작성 지침 - 풍성한 고밀도 인포그래픽 포맷]
1. **요일별 이벤트 수**: 월요일부터 목요일까지 **각 요일마다 반드시 2~3개의 핵심 일정**, 금요일도 **2~3개의 핵심 일정**을 고르게 배치하여 카드에 빈 여백 없이 꽉 찬 고밀도 구성을 완성할 것. (절대로 특정 요일에 1개만 넣지 말 것)
2. **5대 카테고리 고정 및 다채로운 배분**: 아래 5개 카테고리 태그를 요일별로 다양하게 섞어서 배치할 것:
   - "event" : "주요이벤트" (대형 테크 콘퍼런스, 정상회담, 지정학 이벤트 등)
   - "policy" : "정책/통화" (FOMC, 금리 결정, 국채 발행 계획 등)
   - "indicator" : "지표" (CPI, PPI, PCE, 비농업 고용, GDP, 소매판매, PMI 등)
   - "earnings" : "실적" (기업 실적 발표)
   - "speech" : "발언" (파월 의장 연설, 연준 위원 발언 등)
3. **중요도 별표(stars)**: 시장 파급력에 따라 공정하게 배분:
   - "★★★" : 시장 전체를 뒤흔들 초특급 이벤트 (FOMC, 파월 연설, NVDA 실적, PCE 등)
   - "★★" : 빅테크 실적(AMD, MSFT, AAPL 등) 또는 핵심 경제지표(GDP 등)
   - "★" : 중요 기업 실적, 주요 제조업 지표, 연준 위원 발언
   - "" : 통상적인 지표
4. **상세 설명(detail)**: 각 이벤트마다 괄호 안에 들어갈 `detail` 부연 설명을 1~2줄로 구체적으로 작성하여 정보성을 극대화할 것.
5. **투자자 핵심 보유주 최우선**: **엔비디아(NVDA)** 와 **AMD** 관련 일정(실적, 콘퍼런스, AI 가속기 발표 등)을 최우선으로 반드시 포함할 것.
6. **주요 기업 실적 일정(earnings_schedule)**: AMD, 엔비디아(NVDA)를 포함하여 차주 또는 최근 핵심 빅테크 6개 종목을 날짜순으로 채울 것.
7. 순수 JSON 형식만 반환할 것.

[JSON 형식 예시]
{{
  "focus_items": [
    {{"title": "M7 빅테크 실적 대거 발표", "detail": "MSFT, AAPL, AMZN, META, AMD 등 AI 가속기 및 클라우드 실적 집중"}},
    {{"title": "미국 3분기 GDP & 9월 PCE 물가지수 발표", "detail": "연준의 금리 인하 속도를 결정지을 주요 성장률 및 선호 물가지표"}}
  ],
  "todo_items": [
    "AMD 3분기 실적 및 AI 칩(MI300) 매출 가이드라인 점검",
    "미국 3분기 GDP 성장률 속보치 발표 확인",
    "미 재무부 4분기 국채 발행 계획(QRA) 발표",
    "MSFT, META, AAPL, AMZN 빅테크 실적 발표 릴레이",
    "미국 9월 근원 PCE(개인소비지출) 물가지수 발표"
  ],
  "days": [
    {{
      "day_en": "Monday",
      "date_num": "{dates['mon'].strftime('%d')}",
      "events": [
        {{"category_cls": "indicator", "category_name": "지표", "title": "10월 댈러스 연은 제조업 지수", "stars": "★", "detail": "미국 지역 제조업 경기 흐름 파악"}},
        {{"category_cls": "event", "category_name": "주요이벤트", "title": "엔비디아(NVDA) AI 파트너십 & 기술 브리핑", "stars": "★★", "detail": "글로벌 클라우드 및 빅테크 협력 논의"}},
        {{"category_cls": "earnings", "category_name": "실적", "title": "NXP 반도체 실적 발표", "stars": "★", "detail": "차량용/산업용 반도체 수요 점검 (장 마감 후)"}}
      ]
    }},
    {{
      "day_en": "Tuesday",
      "date_num": "{dates['tue'].strftime('%d')}",
      "events": [
        {{"category_cls": "indicator", "category_name": "지표", "title": "10월 리치먼드 연은 제조업 및 소비자신뢰지수", "stars": "★", "detail": "소비자 심리 상태 점검"}},
        {{"category_cls": "earnings", "category_name": "실적", "title": "AMD 3분기 실적 발표", "stars": "★★★", "detail": "AMD(A) - AI 가속기(MI300) 매출 비중 공개"}},
        {{"category_cls": "earnings", "category_name": "실적", "title": "알파벳(구글) 3분기 실적 발표", "stars": "★★", "detail": "GOOGL(A) - 클라우드 성장률 및 검색 성과"}}
      ]
    }},
    {{
      "day_en": "Wednesday",
      "date_num": "{dates['wed'].strftime('%d')}",
      "events": [
        {{"category_cls": "indicator", "category_name": "지표", "title": "미국 3분기 GDP 속보치", "stars": "★★★", "detail": "미국 경제의 견조한 성장세 지속 여부 확인"}},
        {{"category_cls": "policy", "category_name": "정책/통화", "title": "미 재무부 4분기 국채 발행 계획(QRA)", "stars": "★★", "detail": "장기 국채 발행 규모 및 금리 변동성 영향"}},
        {{"category_cls": "earnings", "category_name": "실적", "title": "마이크로소프트 & 메타 실적 발표", "stars": "★★★", "detail": "애저 클라우드 성장률 및 AI CapEx 확대 규모"}}
      ]
    }},
    {{
      "day_en": "Thursday",
      "date_num": "{dates['thu'].strftime('%d')}",
      "events": [
        {{"category_cls": "indicator", "category_name": "지표", "title": "주간 신규 실업수당청구건수", "stars": "★", "detail": "미국 고용 시장 냉각 여부 확인"}},
        {{"category_cls": "earnings", "category_name": "실적", "title": "애플 & 아마존 실적 발표", "stars": "★★★", "detail": "아이폰 16 판매량 및 AWS 성장률 점검 (장 마감 후)"}},
        {{"category_cls": "earnings", "category_name": "실적", "title": "인텔 실적 발표", "stars": "★", "detail": "파운드리 부문 손실 및 구조조정 경과"}}
      ]
    }},
    {{
      "day_en": "Friday",
      "date_num": "{dates['fri'].strftime('%d')}",
      "events": [
        {{"category_cls": "indicator", "category_name": "지표", "title": "미국 9월 근원 PCE 개인소비지출 물가지수", "stars": "★★★", "detail": "연준(Fed)이 가장 중시하는 물가 지표 발표"}},
        {{"category_cls": "earnings", "category_name": "실적", "title": "엑슨모빌 & 셰브론 실적 발표", "stars": "★", "detail": "대형 에너지 기업 실적 및 유가 전망 (장 시작 전)"}}
      ]
    }}
  ],
  "earnings_schedule": [
    {{"name": "AMD", "icon": "⚡", "date_str": "10월 27일 (화)", "timing": "장 마감 후"}},
    {{"name": "메타", "icon": "💬", "date_str": "10월 28일 (수)", "timing": "장 마감 후"}},
    {{"name": "마이크로소프트", "icon": "💻", "date_str": "10월 28일 (수)", "timing": "장 마감 후"}},
    {{"name": "애플", "icon": "🍎", "date_str": "10월 29일 (목)", "timing": "장 마감 후"}},
    {{"name": "아마존", "icon": "📦", "date_str": "10월 29일 (목)", "timing": "장 마감 후"}},
    {{"name": "엔비디아", "icon": "🤖", "date_str": "11월 예정 (콘퍼런스: 10/26)", "timing": "장 마감 후"}}
  ]
}}
"""

    models_to_try = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4}
        }
        try:
            res = requests.post(url, json=payload, timeout=25)
            if res.status_code == 200:
                result = res.json()
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
                text = re.sub(r"```$", "", text.strip(), flags=re.MULTILINE)
                data = json.loads(text)
                logger.info(f"Gemini AI ({model_name}) 위클리 캘린더 생성 성공! 🎉")
                return data
        except Exception as ex:
            logger.warning(f"Gemini 위클리 캘린더 생성 중 오류: {ex}")

    return fallback


def get_default_calendar_data(dates):
    """
    기본 스마트 캘린더 폴백 데이터
    """
    mon_d = dates["mon"].strftime("%d")
    tue_d = dates["tue"].strftime("%d")
    wed_d = dates["wed"].strftime("%d")
    thu_d = dates["thu"].strftime("%d")
    fri_d = dates["fri"].strftime("%d")

    return {
        "focus_items": [
            {"title": "빅테크 및 AI 반도체 실적 발표 집중", "detail": "AMD, 메타, MSFT, 애플 등 실적 릴레이"},
            {"title": "미국 핵심 고용 및 PCE 물가지수 발표", "detail": "연준 기준금리 인하 경로 가늠할 분수령"}
        ],
        "todo_items": [
            "미 11월 FOMC 기준금리 결정",
            "미국 대통령 선거 및 의회 선거",
            "미 재무부 분기 국채발행계획(QRA) 발표",
            "엔비디아 실적 발표 및 AI 칩 수주 업데이트",
            "OpenAI 및 앤트로픽 차세대 AI 모델 공개"
        ],
        "days": [
            {
                "day_en": "Monday",
                "date_num": mon_d,
                "events": [
                    {"category_cls": "event", "category_name": "주요이벤트", "title": "글로벌 테크 인프라 콘퍼런스 개막", "stars": "", "detail": "클라우드 & 데이터센터 동향"},
                    {"category_cls": "earnings", "category_name": "실적", "title": "주요 중소형 테크주 실적 발표", "stars": "", "detail": "장 마감 후 발표"}
                ]
            },
            {
                "day_en": "Tuesday",
                "date_num": tue_d,
                "events": [
                    {"category_cls": "earnings", "category_name": "실적", "title": "AMD 실적 발표", "stars": "★★", "detail": "MI300 가속기 가이던스 확인 (A)"},
                    {"category_cls": "indicator", "category_name": "지표", "title": "미국 JOLTs 구인구직 보고서", "stars": "★", "detail": "노동시장 냉각 여부"},
                    {"category_cls": "earnings", "category_name": "실적", "title": "알파벳(구글) 3분기 실적", "stars": "★★", "detail": "클라우드 AI 매출 (A)"}
                ]
            },
            {
                "day_en": "Wednesday",
                "date_num": wed_d,
                "events": [
                    {"category_cls": "earnings", "category_name": "실적", "title": "마이크로소프트 & 메타 실적", "stars": "★★★", "detail": "AI 설비투자(CAPEX) 규모 점검 (A)"},
                    {"category_cls": "indicator", "category_name": "지표", "title": "미국 3분기 GDP 속보치", "stars": "★★", "detail": "미국 경제 성장률 확인"},
                    {"category_cls": "speech", "category_name": "발언", "title": "연준 주요 인사 공개 발언", "stars": "★", "detail": "인플레이션 및 통화정책 언급"}
                ]
            },
            {
                "day_en": "Thursday",
                "date_num": thu_d,
                "events": [
                    {"category_cls": "indicator", "category_name": "지표", "title": "미국 9월 PCE 물가지수 발표", "stars": "★★★", "detail": "연준 선호 물가지표"},
                    {"category_cls": "earnings", "category_name": "실적", "title": "애플 & 아마존 분기 실적 발표", "stars": "★★★", "detail": "아이폰16 판매 및 AWS 클라우드 성장률 (A)"},
                    {"category_cls": "indicator", "category_name": "지표", "title": "신규 실업수당 청구건수", "stars": "★", "detail": "주간 고용 지표"}
                ]
            },
            {
                "day_en": "Friday",
                "date_num": fri_d,
                "events": [
                    {"category_cls": "indicator", "category_name": "지표", "title": "미국 10월 비농업 고용보고서(NFP)", "stars": "★★★", "detail": "실업률 및 신규 일자리 수"},
                    {"category_cls": "indicator", "category_name": "지표", "title": "ISM 제조업 PMI 발표", "stars": "★", "detail": "제조업 경기 확장 여부"},
                    {"category_cls": "earnings", "category_name": "실적", "title": "엑손모빌, 셰브론 정유 대장주 실적", "stars": "★", "detail": "에너지 섹터 마감 (B)"}
                ]
            }
        ],
        "earnings_schedule": [
            {"name": "AMD", "icon": "⚡", "date_str": f"{mon_d[:2]}~{tue_d} (화)", "timing": "장 마감 후"},
            {"name": "알파벳 (GOOGL)", "icon": "🔍", "date_str": f"{mon_d[:2]}~{tue_d} (화)", "timing": "장 마감 후"},
            {"name": "마이크로소프트 (MSFT)", "icon": "💻", "date_str": f"{mon_d[:2]}~{wed_d} (수)", "timing": "장 마감 후"},
            {"name": "메타 (META)", "icon": "💬", "date_str": f"{mon_d[:2]}~{wed_d} (수)", "timing": "장 마감 후"},
            {"name": "애플 (AAPL)", "icon": "🍎", "date_str": f"{mon_d[:2]}~{thu_d} (목)", "timing": "장 마감 후"},
            {"name": "아마존 (AMZN)", "icon": "📦", "date_str": f"{mon_d[:2]}~{thu_d} (목)", "timing": "장 마감 후"},
            {"name": "엔비디아 (NVDA)", "icon": "🤖", "date_str": "차기 실적 주간 (수)", "timing": "장 마감 후"}
        ]
    }


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

    caption = f"""🗓 *[차주 프리뷰] 미국 증시 위클리 캘린더*
📅 *적용 주간*: {dates['range_str']}

📌 *차주 핵심 포커스*
{chr(10).join(focus_list)}

💡 *5대 고정 컬러 범례*
• 🌸 분홍: 주요이벤트
• 🏛 검정: 정책/통화
• 📊 초록: 핵심지표
• 💼 파랑: 기업실적 (NVDA & AMD 집중)
• 🎤 노랑: 연준발언

_※ 요일별 세부 발표 시간 및 중요도(★~★★★)는 첨부된 인포그래픽 캘린더를 확인하세요!_"""

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
    logger.info("📅 미국 증시 차주 위클리 캘린더 파이프라인 시작")
    logger.info("==========================================")

    # 1. 차주 날짜 계산
    dates = get_next_week_dates()

    # 2. Gemini AI 차주 일정 분석
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
