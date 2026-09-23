"""环境变量配置。"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'league.db'}")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
# 机器人只读事件 API 的独立凭证；不复用管理员 token。
BOT_API_TOKEN = os.environ.get("BOT_API_TOKEN", "").strip()

# 会话有效期（秒），默认 7 天
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE", str(7 * 24 * 3600)))

# 赛事场自动检测（每天定点增量同步）
AUTO_SYNC_ENABLED = os.environ.get("AUTO_SYNC_ENABLED", "").lower() in ("1", "true", "yes", "on")
# 每天触发时刻（HH:MM），默认 22:00
AUTO_SYNC_TIME = os.environ.get("AUTO_SYNC_TIME", "22:00").strip() or "22:00"
# 赛事场组织者账号密码（自动检测需要；仅从环境变量读取，不落盘）
DHS_USERNAME = os.environ.get("DHS_USERNAME", "").strip()
DHS_PASSWORD = os.environ.get("DHS_PASSWORD", "").strip()
# 赛事场 WebSocket 地址；网络环境受限时可通过环境变量切换网关或代理入口。
DHS_WS = os.environ.get("DHS_WS", "wss://common-v2.maj-soul.com/contest_ws_gateway").strip()
# 当前赛事后台 HTTP API 地址。
DHS_API = os.environ.get("DHS_API", "https://contest-gate-202411.maj-soul.com").strip().rstrip("/")

# 普通雀魂大厅账号，与赛事场组织者账号分开配置。
MS_USERNAME = os.environ.get("MS_USERNAME", "").strip()
MS_PASSWORD = os.environ.get("MS_PASSWORD", "")
MS_ACCESS_TOKEN = os.environ.get("MS_ACCESS_TOKEN", "").strip()
MS_OAUTH_TYPE = int(os.environ.get("MS_OAUTH_TYPE", "0"))
# 大厅实时抓牌谱配置。
LIVE_SYNC_ENABLED = os.environ.get("LIVE_SYNC_ENABLED", "").lower() in ("1", "true", "yes", "on")
LIVE_SYNC_INTERVAL = max(10, int(os.environ.get("LIVE_SYNC_INTERVAL", "60")))
LIVE_SYNC_FILTER_IDS = tuple(
    int(item.strip()) for item in os.environ.get(
        "LIVE_SYNC_FILTER_IDS", "216,215,225,226,224,223,212,211,208,209,221,222"
    ).split(",") if item.strip().isdigit()
)

_admin_token_cache: str | None = None
_admin_username_cache: str | None = None
_admin_password_cache: str | None = None
_session_secret_cache: str | None = None


def _persist(name: str, value: str) -> str:
    """写到 data/ 下持久化（首次启动自动生成场景）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / name).write_text(value, encoding="utf-8")
    return value


def _load_or_init(name: str, env_key: str, generator) -> str:
    """env 优先 → 已持久化文件 → 生成并写入。"""
    cache_attr = {
        ".admin_token": "_admin_token_cache",
        ".admin_user": "_admin_username_cache",
        ".admin_pass": "_admin_password_cache",
        ".session_secret": "_session_secret_cache",
    }[name]
    g = globals()
    if g[cache_attr]:
        return g[cache_attr]
    env_val = os.environ.get(env_key, "").strip()
    if env_val:
        g[cache_attr] = env_val
        return env_val
    fp = DATA_DIR / name
    if fp.is_file():
        v = fp.read_text(encoding="utf-8").strip()
        g[cache_attr] = v
        return v
    v = generator()
    _persist(name, v)
    g[cache_attr] = v
    return v


def ensure_admin_token() -> str:
    """ADMIN_TOKEN 未设置时生成一次并持久化到 data/.admin_token（API/CI 调用兼容）。"""
    return _load_or_init(".admin_token", "ADMIN_TOKEN", lambda: secrets.token_urlsafe(24))


def ensure_admin_username() -> str:
    """管理员用户名，默认 admin。"""
    return _load_or_init(".admin_user", "ADMIN_USERNAME", lambda: "admin")


def ensure_admin_password() -> str:
    """管理员密码，未设置时生成一次并持久化到 data/.admin_pass（启动时打印）。"""
    return _load_or_init(
        ".admin_pass", "ADMIN_PASSWORD", lambda: secrets.token_urlsafe(12)
    )


def ensure_session_secret() -> str:
    """会话签名密钥，未设置时生成并持久化到 data/.session_secret。"""
    return _load_or_init(
        ".session_secret", "SESSION_SECRET", lambda: secrets.token_urlsafe(32)
    )


def print_admin_credentials() -> None:
    """启动时打印一次管理员账号密码（仅本地调试用）。"""
    user = ensure_admin_username()
    pwd = ensure_admin_password()
    print(f"管理账号: {user}")
    print(f"管理密码: {pwd}")
