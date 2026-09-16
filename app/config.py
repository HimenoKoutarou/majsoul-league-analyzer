"""环境变量配置。"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'league.db'}")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))

_admin_token_cache: str | None = None


def ensure_admin_token() -> str:
    """ADMIN_TOKEN 未设置时生成一次并持久化到 data/.admin_token。"""
    global _admin_token_cache
    if _admin_token_cache:
        return _admin_token_cache
    env_token = os.environ.get("ADMIN_TOKEN", "").strip()
    if env_token:
        _admin_token_cache = env_token
        return env_token
    token_file = DATA_DIR / ".admin_token"
    if token_file.is_file():
        _admin_token_cache = token_file.read_text(encoding="utf-8").strip()
        return _admin_token_cache
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _admin_token_cache = secrets.token_urlsafe(24)
    token_file.write_text(_admin_token_cache, encoding="utf-8")
    return _admin_token_cache
