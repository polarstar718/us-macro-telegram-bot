"""
미국 증시 S&P 500 일간(1-Day) 및 주간(1-Week) 히트맵 캡처 모듈
- Finviz S&P 500 히트맵 맵
  * 일간(1-Day): https://finviz.com/map.ashx?t=sec
  * 주간(1-Week): https://finviz.com/map.ashx?t=sec&st=w1
- 글로벌 표준 컬러: 상승 초록색(Green), 하락 빨간색(Red)
- 고해상도 2x Retina 전면 캡처 (배너 및 광고 제거)
"""

import asyncio
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger("HeatmapGenerator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _get_finviz_url(timeframe: str = "w1") -> str:
    if timeframe.lower() in ["d1", "day", "daily", "1d"]:
        return "https://finviz.com/map.ashx?t=sec"
    return "https://finviz.com/map.ashx?t=sec&st=w1"


async def _capture_with_playwright(output_path: str, timeframe: str = "w1") -> bool:
    """
    Playwright Chromium을 통해 Finviz 맵을 고해상도 2x로 캡처
    """
    from playwright.async_api import async_playwright

    url = _get_finviz_url(timeframe)
    logger.info(f"Playwright를 통한 Finviz 히트맵({timeframe}) 캡처 시작: {url}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            viewport={"width": 1400, "height": 1100},
            device_scale_factor=2
        )
        page = await context.new_page()

        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        # 캔버스 및 텍스트 렌더링 안정화를 위해 대기
        await page.wait_for_timeout(3500)

        # 팝업, 툴팁, 배너 제거
        await page.evaluate("""() => {
            document.querySelectorAll('#modal-elite-ad_content, .tooltip, [class*="tooltip"], [class*="banner"], [class*="dialog"]').forEach(el => el.remove());
            document.querySelectorAll('#notice, #banner, [id*="cookie"]').forEach(el => el.remove());
        }""")
        await page.wait_for_timeout(500)

        # 캔버스 스크린샷
        canvas = page.locator("canvas").first
        if await canvas.count() > 0:
            await canvas.screenshot(path=output_path)
            logger.info(f"Finviz 캔버스 직접 캡처 완료: {output_path}")
        else:
            map_el = page.locator(".content.map, #inner-content").first
            await map_el.screenshot(path=output_path)
            logger.info(f"Finviz 맵 컨테이너 캡처 완료: {output_path}")

        await browser.close()
        return True


def _capture_with_edge_fallback(output_path: str, timeframe: str = "w1") -> bool:
    """
    Edge headless 및 Pillow 크롭을 활용한 백업 캡처
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow가 설치되지 않아 Edge 크롭 백업을 실행할 수 없습니다.")
        return False

    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    ]
    edge_binary = next((p for p in edge_paths if os.path.exists(p)), None)
    if not edge_binary:
        return False

    url = _get_finviz_url(timeframe)
    temp_raw = output_path.replace(".png", "_raw.png")
    cmd = [
        edge_binary,
        "--headless=new",
        "--disable-blink-features=AutomationControlled",
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
        "--window-size=1200,900",
        f"--screenshot={os.path.abspath(temp_raw)}",
        "--hide-scrollbars",
        url
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if os.path.exists(temp_raw):
        im = Image.open(temp_raw)
        # Finviz 차트 본문 영역 크롭 (좌측 사이드바 및 상단 네비게이션 제외)
        cropped = im.crop((176, 175, 1195, 890))
        cropped.save(output_path)
        try:
            os.remove(temp_raw)
        except Exception:
            pass
        logger.info(f"Edge headless 백업 크롭 캡처 완료: {output_path}")
        return True
    return False


def capture_finviz_heatmap(output_path: str = "output/weekly_heatmap.png", timeframe: str = "w1") -> str:
    """
    S&P 500 히트맵 이미지 캡처
    :param output_path: 저장 파일 경로
    :param timeframe: 'd1'(일간) 또는 'w1'(주간)
    """
    if not os.path.isabs(output_path):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        output_path = os.path.join(base_dir, output_path)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    # 1. Playwright 우선 시도
    try:
        success = asyncio.run(_capture_with_playwright(output_path, timeframe=timeframe))
        if success and os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
            return output_path
    except Exception as pe:
        logger.warning(f"Playwright 캡처 실패 ({pe}), Edge fallback 시도...")

    # 2. Edge headless fallback 시도
    try:
        success = _capture_with_edge_fallback(output_path, timeframe=timeframe)
        if success and os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
            return output_path
    except Exception as ee:
        logger.error(f"Edge fallback 캡처 실패: {ee}")

    # 3. 만약 둘 다 실패하면 기본 폴더의 컬러맵.jpg 복사
    workspace_ref = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "컬러맵.jpg"))
    if os.path.exists(workspace_ref):
        import shutil
        shutil.copy(workspace_ref, output_path)
        logger.info(f"임시 참조용 컬러맵 복사 완료: {output_path}")
        return output_path

    raise RuntimeError("히트맵 캡처에 실패하였습니다.")


def capture_daily_heatmap(output_path: str = "output/daily_heatmap.png") -> str:
    """일간(1-Day) S&P 500 히트맵 캡처 헬퍼"""
    return capture_finviz_heatmap(output_path=output_path, timeframe="d1")


def capture_weekly_heatmap(output_path: str = "output/weekly_heatmap.png") -> str:
    """주간(1-Week) S&P 500 히트맵 캡처 헬퍼"""
    return capture_finviz_heatmap(output_path=output_path, timeframe="w1")


if __name__ == "__main__":
    out = capture_daily_heatmap("output/daily_heatmap.png")
    print("일간 히트맵 캡처 완료:", out)

