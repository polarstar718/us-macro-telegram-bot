"""
미국 증시 데일리 매크로 브리핑 메인 실행 파이프라인
1. 미국 증시 및 매크로 지표 수집 (yfinance)
2. Gemini 기반 초보자 친화적 시장 분석 생성
3. HTML/CSS 템플릿을 고해상도 인포그래픽 카드(PNG)로 렌더링
4. 텔레그램 봇으로 이미지 및 마크다운 캡션 발송
"""

import logging
import os
import sys
from dotenv import load_dotenv

from data_collector import collect_all_market_data
from ai_analyzer import generate_market_analysis
from renderer import render_briefing_image
from notifier import send_briefing_to_telegram
from weekly_heatmap import capture_daily_heatmap

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("DailyBriefing")


def main():
    # 스크립트 위치 기준으로 작업 디렉터리 고정
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

    load_dotenv()
    logger.info("==========================================")
    logger.info("🚀 미국 증시 매크로 브리핑 파이프라인 시작")
    logger.info("==========================================")

    # 1. 데이터 수집
    logger.info("[1/5] 최신 미국 증시 및 매크로 데이터 수집 중...")
    market_data = collect_all_market_data("config.json")

    # 2. AI 분석 생성
    logger.info("[2/5] Gemini AI 시장 분석 및 해설 생성 중...")
    ai_data = generate_market_analysis(market_data)

    # 3. 고해상도 카드뉴스 이미지 렌더링
    logger.info("[3/5] 인포그래픽 카드뉴스 렌더링 (briefing.png)...")
    output_png = "output/briefing.png"
    render_briefing_image(market_data, ai_data, output_png)

    # 4. S&P 500 일간 히트맵 캡처
    logger.info("[4/5] Finviz S&P 500 일간(1-Day) 히트맵 캡처 중...")
    heatmap_png = "output/daily_heatmap.png"
    try:
        capture_daily_heatmap(heatmap_png)
    except Exception as he:
        logger.warning(f"일간 히트맵 캡처 중 오류 발생 (단일 브리핑 카드로 발송 계속): {he}")

    # 5. 텔레그램 발송 (브리핑 카드 + 일간 히트맵 2장 앨범)
    logger.info("[5/5] 텔레그램 봇 알림 발송 확인...")
    sent = send_briefing_to_telegram(output_png, market_data, ai_data, heatmap_path=heatmap_png)
    if sent:
        logger.info("✅ 텔레그램 발송이 완료되었습니다!")
    else:
        logger.info("ℹ️ 텔레그램 설정이 없거나 전송되지 않았습니다. 생성된 이미지는 'output/' 디렉터리에서 확인하실 수 있습니다.")

    logger.info("==========================================")
    logger.info(f"🎉 모든 작업 완료! 결과물: {os.path.abspath(output_png)}")
    logger.info("==========================================")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception(f"실행 중 치명적인 오류 발생: {e}")
        sys.exit(1)
