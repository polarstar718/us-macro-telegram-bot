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
    텔레그램 메시지 본문용 마크다운 서식 캡션 생성 (1,024자 이내 안전 제한 및 검색 최적화)
    - 💡 오늘의 판단
    - 📌 핵심 3줄 요약 (지수 동향, 매크로/환율/공포탐욕, 국내 증시 영향)
    - 🎯 핵심 보유주 (NVDA & AMD) 한 줄 점검
    - 🏷️ 주요 해시태그/티커
    """
    date_str = market_data.get("date_str", "")
    headline = ai_data.get("headline", "")
    k_impact = ai_data.get("k_market_impact", "")
    fg = market_data.get("fear_greed", {})
    fg_text = f"{fg.get('score', '')}점({fg.get('rating_kr', '')})" if fg else ""

    # 1. 지수 요약
    indices = market_data.get("indices", [])
    sp500 = next((i for i in indices if "S&P" in i.get("name", "")), None)
    nasdaq = next((i for i in indices if "나스닥" in i.get("name", "")), None)
    sox = next((i for i in indices if "반도체" in i.get("name", "")), None)
    idx_parts = []
    if sp500: idx_parts.append(f"S&P500 {sp500['symbol']}{sp500['pct_str']}")
    if nasdaq: idx_parts.append(f"나스닥 {nasdaq['symbol']}{nasdaq['pct_str']}")
    if sox: idx_parts.append(f"필라반도체 {sox['symbol']}{sox['pct_str']}")
    idx_line = " · ".join(idx_parts) if idx_parts else "지수 데이터 수집 완료"

    # 2. 매크로 요약
    macro = market_data.get("macro", [])
    us10y = next((m for m in macro if "10년물" in m.get("name", "")), None)
    usdkrw = next((m for m in macro if "환율" in m.get("name", "")), None)
    macro_parts = []
    if us10y: macro_parts.append(f"10년물 {us10y['price']}({us10y['change_str']})")
    if usdkrw: macro_parts.append(f"환율 {usdkrw['price']}")
    if fg_text: macro_parts.append(f"공포·탐욕 {fg_text}")
    macro_line = " · ".join(macro_parts) if macro_parts else "매크로 지표 확인"

    # 3. 핵심 보유 종목(NVDA, AMD) 요약
    focus_stocks = market_data.get("focus_stocks", [])
    focus_parts = []
    for s in focus_stocks:
        ticker = s.get("ticker", "")
        price = s.get("price", "")
        pct = s.get("pct_str", "")
        symbol = s.get("symbol", "")
        icon = "🟢" if symbol == "▲" else "🔴" if symbol == "▼" else "⚪"
        focus_parts.append(f"{icon} *{ticker}* {price} ({symbol}{pct})")
    focus_line = "   ".join(focus_parts)

    # 4. 당일 어닝콜 & 분기 실적(YoY·QoQ) 요약 (존재할 경우에만 동적 표시)
    ec_lines = []
    for ec in (ai_data.get("earnings_calls") or [])[:2]:
        ec_lines.append(f"• *{ec.get('name', '')}*: {ec.get('metrics', '')}")
        for hl in (ec.get("highlights") or [])[:1]:
            ec_lines.append(f"  └ 🗣 {hl}")

    # 5. 해시태그 목록
    hashtags = "#미국증시 #NVDA #AMD #나스닥 #환율 #국내장영향 #SOX #어닝콜"

    # 캡션 조합
    caption_parts = [
        "📊 *미국 증시 데일리 매크로 브리핑*",
        f"🗓 *기준*: {date_str}\n",
        "💡 *오늘의 판단*",
        f"\"{headline}\"\n",
        "📌 *핵심 요약 3줄*",
        f"1️⃣ *지수 동향*: {idx_line}",
        f"2️⃣ *매크로/환율*: {macro_line}",
        f"3️⃣ *국내 증시 영향*: {k_impact}\n",
        "🎯 *핵심 보유주 집중 점검 (AI 반도체)*",
        f"{focus_line}\n"
    ]
    if ec_lines:
        caption_parts.append("🎙 *당일 어닝콜 & 실적(YoY·QoQ)*")
        caption_parts.append("\n".join(ec_lines) + "\n")
    caption_parts.append(hashtags)

    if has_heatmap:
        caption_parts.append("\n_※ 사진 1: 인포그래픽 카드 | 사진 2: S&P 500 일간 히트맵_")

    caption = "\n".join(caption_parts)

    # 텔레그램 캡션 길이 1,024자 제한 안전 처리
    if len(caption) > 1020:
        caption = caption[:1015] + "..."

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


if __name__ == "__main__":
    from data_collector import collect_all_market_data
    from ai_analyzer import get_fallback_analysis

    data = collect_all_market_data()
    ai = get_fallback_analysis(data)
    send_briefing_to_telegram("output/briefing.png", data, ai)
