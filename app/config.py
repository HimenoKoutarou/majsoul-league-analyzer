"""环境变量配置。"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'league.db'}")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))

# 会话有效期（秒），默认 7 天
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE", str(7 * 24 * 3600)))

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
