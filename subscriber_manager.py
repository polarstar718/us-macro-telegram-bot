"""
텔레그램 봇 구독자 관리 모듈
- subscribers.json 에 등록된 구독자 목록 유지
- 봇 getUpdates API를 주기적으로 확인하여 /start를 누른 신규 구독자 자동 등록
"""

import json
import logging
import os
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

SUBSCRIBERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subscribers.json")

# 초기 등록 구독자 목록
INITIAL_SUBSCRIBERS = [
    {"chat_id": 8688277883, "name": "최광민 (관리자)", "added_at": "2026-10-24"},
    {"chat_id": 8634503326, "name": "Neal", "added_at": "2026-10-24"},
    {"chat_id": 8713293013, "name": "Louis", "added_at": "2026-10-24"},
    {"chat_id": 8152904343, "name": "구독자", "added_at": "2026-10-24"}
]


def load_subscribers_data():
    if not os.path.exists(SUBSCRIBERS_FILE):
        data = {"subscribers": INITIAL_SUBSCRIBERS}
        save_subscribers_data(data)
        return data

    try:
        with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"subscribers.json 로드 실패 ({e}), 초기 목록 사용")
        return {"subscribers": INITIAL_SUBSCRIBERS}


def save_subscribers_data(data):
    try:
        with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"subscribers.json 저장 실패: {e}")


def sync_new_subscribers(bot_token=None):
    """
    텔레그램 봇의 getUpdates를 호출하여 새로운 /start 구독자 자동 추가
    """
    token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return

    data = load_subscribers_data()
    existing_ids = {s["chat_id"] for s in data.get("subscribers", [])}

    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            updates = res.json().get("result", [])
            added_count = 0
            for u in updates:
                msg = u.get("message", {})
                chat = msg.get("chat", {})
                chat_id = chat.get("id")
                first_name = chat.get("first_name", "")
                last_name = chat.get("last_name", "")
                full_name = f"{last_name}{first_name}".strip() or first_name or "구독자"

                if chat_id and chat_id not in existing_ids:
                    existing_ids.add(chat_id)
                    data.setdefault("subscribers", []).append({
                        "chat_id": chat_id,
                        "name": full_name,
                        "added_at": "자동등록"
                    })
                    added_count += 1
                    logger.info(f"✨ 신규 구독자 자동 등록: {full_name} ({chat_id})")

            if added_count > 0:
                save_subscribers_data(data)
    except Exception as e:
        logger.debug(f"getUpdates 구독자 동기화 실패 (오프라인 모드 등): {e}")


def get_subscribers(broadcast=None, sync=True):
    """
    발송 대상 chat_id 목록 반환 (int)
    - broadcast=True 또는 명령행 인자에 '--broadcast'가 있으면: 전체 등록 구독자에게 발송 (정기 스케줄러 전용)
    - 기본값(수동/테스트 실행): 관리자 본인(8688277883)에게만 1인 안전 발송!
    """
    import sys

    # 1. 관리자 ID 확인
    env_admin = os.getenv("TELEGRAM_CHAT_ID", "8688277883")
    try:
        admin_id = int(env_admin)
    except (ValueError, TypeError):
        admin_id = 8688277883

    # 2. 브로드캐스트 모드 판별
    is_broadcast = False
    if broadcast is True:
        is_broadcast = True
    elif broadcast is None:
        if "--broadcast" in sys.argv or os.getenv("BROADCAST_ALL", "").lower() in ["1", "true", "yes"]:
            is_broadcast = True

    # 수동 테스트 모드: 관리자 본인에게만 안전 발송
    if not is_broadcast:
        logger.info(f"🔒 [관리자 단독 테스트 모드] 안전을 위해 관리자 본인({admin_id})에게만 발송합니다. (전체 발송은 --broadcast 옵션 필요)")
        return [admin_id]

    # 전체 발송 모드 (정기 스케줄러)
    if sync:
        sync_new_subscribers()

    data = load_subscribers_data()
    sub_ids = []
    for s in data.get("subscribers", []):
        cid = s.get("chat_id")
        if cid and cid not in sub_ids:
            try:
                sub_ids.append(int(cid))
            except (ValueError, TypeError):
                pass

    if admin_id not in sub_ids:
        sub_ids.insert(0, admin_id)

    logger.info(f"📢 [정기 전체 발송 모드] 전체 등록 구독자 총 {len(sub_ids)}명에게 발송합니다: {sub_ids}")
    return sub_ids


if __name__ == "__main__":
    subs = get_subscribers()
    print("현재 구독자 목록:", subs)
