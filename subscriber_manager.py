"""
텔레그램 봇 구독자 관리 및 자동 보고 모듈
- subscribers.json 에 등록된 구독자 목록 유지
- 봇 getUpdates API를 확인하여 /start 또는 메시지를 보낸 신규 구독자 자동 등록
- 신규 구독자 발견 시:
  1) 신규 구독자에게 환영 및 발송 스케줄 안내 메시지 자동 발송
  2) 관리자(8688277883)에게 신규 구독자 등록 리포트(이름·ID·총 구독자 명단) 즉시 발송
"""

import datetime
import json
import logging
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SUBSCRIBERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subscribers.json")

# 초기 등록 구독자 목록
INITIAL_SUBSCRIBERS = [
    {"chat_id": 8688277883, "name": "최광민 (관리자)", "added_at": "2026-10-24"},
    {"chat_id": 8634503326, "name": "Neal", "added_at": "2026-10-24"},
    {"chat_id": 8713293013, "name": "Louis", "added_at": "2026-10-24"},
    {"chat_id": 8152904343, "name": "구독자", "added_at": "2026-10-24"}
]


def get_admin_chat_id():
    env_admin = os.getenv("TELEGRAM_CHAT_ID", "8688277883")
    try:
        return int(env_admin)
    except (ValueError, TypeError):
        return 8688277883


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


def _send_telegram_text(token, chat_id, text):
    """단문 텔레그램 메시지 발송 헬퍼"""
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        res = requests.post(url, json=payload, timeout=10)
        return res.status_code == 200
    except Exception as e:
        logger.debug(f"메시지 전송 실패 ({chat_id}): {e}")
        return False


def _send_welcome_to_subscriber(token, chat_id, full_name):
    """신규 구독자에게 환영 인사 및 브리핑 발송 일정 안내"""
    welcome_msg = (
        f"👋 안녕하세요, *{full_name}*님!\n"
        f"*미국주식공부방 · 매크로 브리핑 봇(KM_macro_bot)* 구독이 자동으로 완료되었습니다.\n\n"
        f"📅 *정기 리포트 발송 일정 (한국 시간)*\n"
        f"• *화~토 오전 06:40* : 미국 증시 데일리 매크로 브리핑 & 일간 히트맵\n"
        f"• *월~금 오후 16:10* : 한국 증시 마감 브리핑 (삼성전자·SK하이닉스 집중 점검)\n"
        f"• *일요일 저녁 19:30* : 주간 특징주 결산 & 차주 위클리 캘린더\n\n"
        f"다음 정기 발송 시간부터 고화질 브리핑 카드가 자동으로 전달됩니다! 📈"
    )
    if _send_telegram_text(token, chat_id, welcome_msg):
        logger.info(f"💌 신규 구독자({full_name}, {chat_id})에게 환영 메시지 발송 완료")


def _notify_admin_new_subscriber(token, admin_id, new_sub, all_subscribers):
    """관리자에게 신규 구독자 등록 현황 즉시 보고"""
    names_summary = ", ".join([f"{s.get('name', '구독자')}" for s in all_subscribers])
    report_msg = (
        f"🔔 *[신규 구독자 자동 등록 보고]*\n\n"
        f"• *신규 구독자*: *{new_sub['name']}* (`{new_sub['chat_id']}`)\n"
        f"• *등록 일시*: {new_sub['added_at']}\n"
        f"• *현재 총 구독자*: *{len(all_subscribers)}명*\n"
        f"• *전체 명단*: {names_summary}\n\n"
        f"_※ 다음 정기 브리핑부터 해당 구독자에게도 자동으로 리포트가 발송됩니다._"
    )
    if _send_telegram_text(token, admin_id, report_msg):
        logger.info(f"📢 관리자({admin_id})에게 신규 구독자({new_sub['name']}) 등록 보고 완료")


def sync_new_subscribers(bot_token=None):
    """
    텔레그램 봇의 getUpdates를 호출하여 새로운 /start 구독자 자동 추가,
    환영 메시지 발송 및 관리자에게 즉시 알림 보고 수행
    """
    token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return 0

    data = load_subscribers_data()
    existing_ids = {int(s["chat_id"]) for s in data.get("subscribers", []) if s.get("chat_id")}
    admin_id = get_admin_chat_id()

    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        res = requests.get(url, timeout=8)
        if res.status_code == 200:
            updates = res.json().get("result", [])
            new_subs = []
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

            for u in updates:
                msg = u.get("message") or u.get("my_chat_member") or {}
                chat = msg.get("chat", {})
                chat_id = chat.get("id")
                if not chat_id:
                    continue
                try:
                    chat_id = int(chat_id)
                except (ValueError, TypeError):
                    continue

                first_name = chat.get("first_name", "")
                last_name = chat.get("last_name", "")
                username = chat.get("username", "")
                full_name = f"{last_name}{first_name}".strip() or first_name or username or "구독자"

                if chat_id not in existing_ids:
                    existing_ids.add(chat_id)
                    new_entry = {
                        "chat_id": chat_id,
                        "name": full_name,
                        "added_at": now_str
                    }
                    data.setdefault("subscribers", []).append(new_entry)
                    new_subs.append(new_entry)
                    logger.info(f"✨ 신규 구독자 자동 감지 및 등록: {full_name} ({chat_id})")

            if new_subs:
                save_subscribers_data(data)
                all_subs = data.get("subscribers", [])
                for sub in new_subs:
                    _send_welcome_to_subscriber(token, sub["chat_id"], sub["name"])
                    _notify_admin_new_subscriber(token, admin_id, sub, all_subs)
                return len(new_subs)
    except Exception as e:
        logger.debug(f"getUpdates 구독자 동기화 실패 (오프라인 모드 등): {e}")
    return 0


def get_subscribers(broadcast=None, sync=True):
    """
    발송 대상 chat_id 목록 반환 (int)
    - sync=True 이면 테스트/정기 모드 관계없이 항상 신규 구독자(getUpdates) 먼저 동기화 및 관리자 보고 수행!
    - broadcast=True 또는 명령행 인자에 '--broadcast'가 있으면: 전체 등록 구독자에게 발송 (정기 스케줄러 전용)
    - 기본값(수동/테스트 실행): 관리자 본인(8688277883)에게만 1인 안전 발송!
    """
    admin_id = get_admin_chat_id()

    # 1. 언제 실행되든 신규 구독자 자동 감지·등록·보고는 항상 먼저 수행
    if sync:
        sync_new_subscribers()

    # 2. 브로드캐스트 모드 판별
    is_broadcast = False
    if broadcast is True:
        is_broadcast = True
    elif broadcast is None:
        if "--broadcast" in sys.argv or os.getenv("BROADCAST_ALL", "").lower() in ["1", "true", "yes"]:
            is_broadcast = True

    # 수동 테스트 모드: 브리핑은 관리자 본인에게만 안전 발송
    if not is_broadcast:
        logger.info(f"🔒 [관리자 단독 테스트 모드] 안전을 위해 관리자 본인({admin_id})에게만 발송합니다. (전체 발송은 --broadcast 옵션 필요)")
        return [admin_id]

    # 전체 발송 모드 (정기 스케줄러)
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
    if "--sync-only" in sys.argv:
        added = sync_new_subscribers()
        data = load_subscribers_data()
        total = len(data.get("subscribers", []))
        logger.info(f"✅ 구독자 동기화 완료 (신규 추가: {added}명 / 현재 총 {total}명)")
    else:
        subs = get_subscribers()
        print("현재 발송 대상 목록:", subs)
