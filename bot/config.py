"""机器人进程配置，全部通过环境变量注入。"""
import os
from datetime import datetime
from pathlib import Path


def _parse_account_ids(value: str) -> frozenset[int]:
    account_ids: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item.isdigit() and int(item) > 0:
            account_ids.add(int(item))
    return frozenset(account_ids)


API_BASE_URL = os.environ.get("BOT_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
API_TOKEN = os.environ.get("BOT_API_TOKEN", "").strip()
POLL_INTERVAL = max(1, int(os.environ.get("BOT_POLL_INTERVAL", "2")))
GROUP_IDS = tuple(item.strip() for item in os.environ.get("BOT_GROUP_IDS", "").split(",")
                  if item.strip())
WATCH_ACCOUNT_IDS = _parse_account_ids(
    os.environ.get("BOT_WATCH_ACCOUNT_IDS", "")
)
STATE_PATH = Path(os.environ.get("BOT_STATE_PATH", "bot_data/state.json"))
DRY_RUN = os.environ.get("BOT_DRY_RUN", "").lower() in ("1", "true", "yes", "on")
ONEBOT_WS_URL = os.environ.get("ONEBOT_WS_URL", "ws://127.0.0.1:6700/onebot/v11/ws")
ONEBOT_ACCESS_TOKEN = os.environ.get("ONEBOT_ACCESS_TOKEN", "").strip()
JIUGUANDAO_ENABLED = os.environ.get("JIUGUANDAO_ENABLED", "").lower() in (
    "1", "true", "yes", "on"
)
JIUGUANDAO_BASE_URL = os.environ.get(
    "JIUGUANDAO_BASE_URL", "https://u3670369.nyat.app:24205"
).rstrip("/")
JIUGUANDAO_TOKEN = os.environ.get("JIUGUANDAO_TOKEN", "").strip()
JIUGUANDAO_REPLY_PREFIX = os.environ.get("JIUGUANDAO_REPLY_PREFIX", "").strip()
DIGEST_ENABLED = os.environ.get("BOT_DIGEST_ENABLED", "").lower() in (
    "1", "true", "yes", "on"
)
DIGEST_TIME = os.environ.get("BOT_DIGEST_TIME", "23:00").strip()
try:
    DIGEST_TIME = datetime.strptime(DIGEST_TIME, "%H:%M").strftime("%H:%M")
except ValueError:
    DIGEST_TIME = "23:00"
DIGEST_POLL_INTERVAL = max(10, int(os.environ.get("BOT_DIGEST_POLL_INTERVAL", "30")))
