"""
HTML 템플릿을 고해상도(Retina 2x) PNG 이미지로 렌더링하는 모듈
- 기본: Playwright (GitHub Actions 및 표준 클라우드 환경)
- 로컬 백업: Windows 내장 Edge/Chrome headless 캡처 지원
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)


def render_html(market_data, ai_data, output_html_path="output/briefing.html"):
    """
    Jinja2를 이용해 briefing.html 템플릿에 데이터 바인딩 후 HTML 파일 생성
    """
    os.makedirs(os.path.dirname(output_html_path), exist_ok=True)

    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("briefing.html")

    config = market_data.get("config", {})
    indices = market_data.get("indices", [])
    primary_indices = [i for i in indices if i.get("primary", False)]
    secondary_indices = [i for i in indices if not i.get("primary", False)]

    rendered_content = template.render(
        header=config.get("header", {}),
        branding=config.get("branding", {}),
        date_str=market_data.get("date_str", ""),
        primary_indices=primary_indices,
        secondary_indices=secondary_indices,
        macro=market_data.get("macro", []),
        fear_greed=market_data.get("fear_greed", {}),
        m7=market_data.get("m7", []),
        focus_stocks=market_data.get("focus_stocks", []),
        after_market=market_data.get("after_market"),
        ai=ai_data
    )

    with open(output_html_path, "w", encoding="utf-8") as f:
        f.write(rendered_content)

    logger.info(f"HTML 템플릿 생성 완료: {output_html_path}")
    return os.path.abspath(output_html_path)


def capture_with_playwright(html_path, output_png_path):
    """
    Playwright를 사용한 고화질 2x 캡처
    """
    from playwright.sync_api import sync_playwright

    logger.info("Playwright를 통한 스크린샷 렌더링 시작...")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # Retina 2x 해상도를 위해 device_scale_factor=2 설정
        page = browser.new_page(
            viewport={"width": 860, "height": 1900},
            device_scale_factor=2
        )
        file_url = Path(html_path).as_uri()
        page.goto(file_url, wait_until="networkidle")
        # 폰트 및 스타일 렌더링 안정화를 위해 잠시 대기
        page.wait_for_timeout(500)

        # body 엘리먼트 기준으로 전체 캡처
        body = page.locator("body")
        body.screenshot(path=output_png_path)
        browser.close()

    logger.info(f"Playwright 렌더링 완료: {output_png_path}")
    return output_png_path


def capture_with_edge(html_path, output_png_path):
    """
    Windows 환경에서 Playwright 브라우저 미설치 시 내장 Edge를 활용한 즉시 캡처 백업
    """
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    ]
    edge_binary = next((p for p in edge_paths if os.path.exists(p)), None)
    if not edge_binary:
        raise FileNotFoundError("Microsoft Edge 브라우저를 찾을 수 없습니다.")

    logger.info("시스템 Microsoft Edge headless 모드로 캡처 진행...")
    file_url = Path(html_path).as_uri()
    cmd = [
        edge_binary,
        "--headless",
        "--disable-gpu",
        f"--window-size=860,1950",
        f"--screenshot={os.path.abspath(output_png_path)}",
        "--hide-scrollbars",
        file_url
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info(f"Edge 렌더링 완료: {output_png_path}")
    return output_png_path


def render_briefing_image(market_data, ai_data, output_png_path="output/briefing.png"):
    """
    전체 HTML 생성 및 이미지 렌더링 파이프라인 실행
    """
    os.makedirs(os.path.dirname(output_png_path), exist_ok=True)
    html_file = render_html(market_data, ai_data, "output/briefing.html")

    try:
        return capture_with_playwright(html_file, output_png_path)
    except Exception as e:
        logger.warning(f"Playwright 캡처 실패 ({e}). 시스템 내장 Edge로 대체 캡처를 시도합니다.")
        return capture_with_edge(html_file, output_png_path)


if __name__ == "__main__":
    from data_collector import collect_all_market_data
    from ai_analyzer import get_fallback_analysis

    data = collect_all_market_data()
    ai = get_fallback_analysis(data)
    img = render_briefing_image(data, ai)
    print(f"생성된 이미지: {img}")
