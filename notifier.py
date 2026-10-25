"""
텔레그램 봇 알림 발송 모듈
- 생성된 인포그래픽 이미지(briefing.png)와 함께 요약 캡션 메시지 발송
"""

import json
import logging
import os
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def build_telegram_caption(market_data, ai_data, has_heatmap=True):
    """
    텔레그램 메시지 본문용 마크다운 서식 캡션 생성
    """
    date_str = market_data.get("date_str", "")
    headline = ai_data.get("headline", "")

    # 주요 지수 등락 요약
    indices = market_data.get("indices", [])
    indices_summary = []
    for item in indices[:4]:
        name = item.get("name")
        price = item.get("price")
        pct = item.get("pct_str")
        symbol = item.get("symbol", "")
        indices_summary.append(f"• *{name}*: {price} ({symbol} {pct})")

    # 3대 분석 포인트 제목 요약
    reasons = ai_data.get("reasons", [])
    reasons_summary = []
    emojis = ["1️⃣", "2️⃣", "3️⃣"]
    for i, r in enumerate(reasons[:3]):
        reasons_summary.append(f"{emojis[i]} *{r.get('title')}*")

    # 핵심 보유 종목(NVDA, AMD) 요약
    focus_stocks = market_data.get("focus_stocks", [])
    focus_analysis = ai_data.get("focus_analysis", {})
    focus_summary = []
    for s in focus_stocks:
        ticker = s.get("ticker", "")
        name = s.get("name", "")
        price = s.get("price", "")
        pct = s.get("pct_str", "")
        symbol = s.get("symbol", "")
        key = "nvda" if ticker == "NVDA" else "amd"
        icon = "🟢" if ticker == "NVDA" else "🔴"
        fa = focus_analysis.get(key, {})
        title = fa.get("title", "")
        focus_summary.append(f"{icon} *{name} ({ticker})*: {price} ({symbol} {pct})\n   ↳ _{title}_")

    heatmap_guide = "\n🗺 *첨부 2*: S&P 500 일간 등락 히트맵 (Finviz 1-Day)" if has_heatmap else ""

    caption = f"""📊 *미국 증시 데일리 매크로 브리핑*
🗓 *기준*: {date_str}

💡 *오늘의 판단*
"{headline}"

🎯 *핵심 보유주 집중 점검 (AI 반도체)*
{chr(10).join(focus_summary)}

📈 *주요 지수 동향*
{chr(10).join(indices_summary)}

🔍 *왜 이렇게 판단했나 (핵심 3대 포인트)*
{chr(10).join(reasons_summary)}

_※ 상세 인포그래픽 수치 및 심층 분석은 위 카드를 확인하세요!_{heatmap_guide}"""

    return caption


from subscriber_manager import get_subscribers


def send_briefing_to_telegram(image_path="output/briefing.png", market_data=None, ai_data=None, heatmap_path="output/daily_heatmap.png"):
    """
    텔레그램 봇으로 이미지 및 캡션 전송 (히트맵 존재 시 2장 묶음 앨범 전송, 전체 구독자 발송)
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN이 설정되지 않았습니다.")
        return False

    subscribers = get_subscribers()
    if not subscribers:
        logger.warning("발송할 구독자 목록이 비어 있습니다.")
        return False

    base_dir = os.path.dirname(os.path.abspath(__file__))
    if image_path and not os.path.isabs(image_path):
        image_path = os.path.join(base_dir, image_path)
    if heatmap_path and not os.path.isabs(heatmap_path):
        heatmap_path = os.path.join(base_dir, heatmap_path)

    if not os.path.exists(image_path):
        logger.error(f"발송할 이미지 파일이 존재하지 않습니다: {image_path}")
        return False

    has_heatmap = bool(heatmap_path and os.path.exists(heatmap_path) and os.path.getsize(heatmap_path) > 1000)
    caption = build_telegram_caption(market_data or {}, ai_data or {}, has_heatmap=has_heatmap)

    with open(image_path, "rb") as f:
        card_bytes = f.read()

    heatmap_bytes = None
    if has_heatmap:
        with open(heatmap_path, "rb") as f:
            heatmap_bytes = f.read()

    media_url = f"https://api.telegram.org/bot{bot_token}/sendMediaGroup"
    photo_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"

    logger.info(f"📢 데일리 브리핑 발송 대상: 총 {len(subscribers)}명 ({subscribers})")
    success_count = 0

    for target_chat_id in subscribers:
        sent = False
        # 1. 일간 히트맵이 존재하면 2장 앨범(sendMediaGroup)으로 전송
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
                    "photo1": ("briefing.png", card_bytes, "image/png"),
                    "photo2": ("daily_heatmap.png", heatmap_bytes, "image/png")
                }
                payload = {
                    "chat_id": target_chat_id,
                    "media": json.dumps(media_group)
                }
                response = requests.post(media_url, data=payload, files=files, timeout=40)
                if response.status_code == 200:
                    logger.info(f"✅ 구독자({target_chat_id}) 데일리 2장 앨범 발송 성공!")
                    sent = True
                    success_count += 1
                else:
                    logger.warning(f"구독자({target_chat_id}) 앨범 발송 실패 ({response.status_code}), 단일 사진 fallback 시도...")
            except Exception as me:
                logger.warning(f"구독자({target_chat_id}) 앨범 발송 중 예외 ({me}), 단일 사진 fallback 시도...")

        # 2. 히트맵이 없거나 sendMediaGroup 실패 시 단일 사진(sendPhoto) 전송
        if not sent:
            try:
                payload = {
                    "chat_id": target_chat_id,
                    "caption": caption,
                    "parse_mode": "Markdown"
                }
                files = {"photo": ("briefing.png", card_bytes, "image/png")}
                response = requests.post(photo_url, data=payload, files=files, timeout=30)
                if response.status_code == 200:
                    logger.info(f"✅ 구독자({target_chat_id}) 데일리 단일 사진 발송 성공!")
                    success_count += 1
                else:
                    logger.error(f"❌ 구독자({target_chat_id}) 발송 실패 ({response.status_code}): {response.text}")
            except Exception as e:
                logger.error(f"❌ 구독자({target_chat_id}) 발송 중 예외 발생: {e}")

    logger.info(f"🎉 데일리 브리핑: 총 {len(subscribers)}명 중 {success_count}명 발송 완료")
    return success_count > 0
        return False


if __name__ == "__main__":
    from data_collector import collect_all_market_data
    from ai_analyzer import get_fallback_analysis

    data = collect_all_market_data()
    ai = get_fallback_analysis(data)
    send_briefing_to_telegram("output/briefing.png", data, ai)
