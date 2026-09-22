"""赛事场自动检测：每天定点轮询 DHS 增量同步。

通过 AUTO_SYNC_ENABLED 开启，AUTO_SYNC_TIME 指定每天触发时刻（HH:MM，默认 22:00）。
需要 DHS_USERNAME / DHS_PASSWORD 环境变量提供赛事场组织者账号密码，
以及联赛已绑定赛事场ID（league.contest_id）。
"""
import threading
import time
from datetime import datetime, timedelta

from app import config

_lock = threading.Lock()
_started = False
_state = {
    "enabled": False,
    "schedule": "",        # 每天触发时刻 "HH:MM"
    "running": False,      # 自动发起的一轮同步是否进行中
    "last_run_at": None,   # 上一轮发起时间（ISO）
    "next_run_at": None,   # 下一轮计划时间（ISO）
    "last_added": 0,       # 上一轮新增对局数
    "last_status": None,   # success / partial / skipped / failed
    "last_error": None,
}


def auto_sync_state() -> dict:
    with _lock:
        return dict(_state)


def set_runtime_enabled(enabled: bool, session_factory) -> bool:
    """立即切换自动检测，并启动调度线程（如尚未启动）。"""
    config.AUTO_SYNC_ENABLED = bool(enabled)
    if enabled:
        return start_auto_sync(session_factory)
    with _lock:
        _state["enabled"] = False
        _state["next_run_at"] = None
    return True


def _parse_time(value: str) -> tuple[int, int] | None:
    """解析 HH:MM，非法返回 None。"""
    try:
        hh, mm = value.split(":")
        hh, mm = int(hh), int(mm)
        if 0 <= hh < 24 and 0 <= mm < 60:
            return hh, mm
    except (ValueError, AttributeError):
        pass
    return None


def _next_run_after(now: datetime) -> datetime:
    parsed = _parse_time(config.AUTO_SYNC_TIME)
    hh, mm = parsed if parsed else (22, 0)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _on_done(result):
    with _lock:
        _state["running"] = False
        if result is None:
            _state["last_status"] = "failed"
            return
        _state["last_added"] = result.get("games_added", 0)
        _state["last_status"] = result.get("status", "success")
        errors = result.get("errors") or []
        _state["last_error"] = f"{len(errors)} 场失败" if errors else None


def _tick(session_factory):
    """执行一轮自动检测。返回 None 表示本轮未启动同步。"""
    from app.models import League
    from app.services.majsoul.sync import start_sync_thread

    with _lock:
        if _state["running"]:
            return None
        _state["running"] = True
        _state["last_run_at"] = datetime.now().isoformat()

    try:
        with session_factory() as session:
            league = session.query(League).first()
            contest_id = league.contest_id if league else None
            username = (getattr(league, "sync_username", "") if league else "") or config.DHS_USERNAME
            password = (getattr(league, "sync_password", "") if league else "") or config.DHS_PASSWORD
    except Exception as exc:
        with _lock:
            _state["running"] = False
            _state["last_status"] = "failed"
            _state["last_error"] = f"{type(exc).__name__}: {exc}"
        return None

    if not contest_id:
        with _lock:
            _state["running"] = False
            _state["last_status"] = "skipped"
            _state["last_error"] = "联赛未绑定赛事场ID（请先在管理后台初始化）"
        return None

    if not (username and password):
        with _lock:
            _state["running"] = False
            _state["last_status"] = "skipped"
            _state["last_error"] = "未配置 DHS_USERNAME / DHS_PASSWORD"
        return None

    ok = start_sync_thread(session_factory, contest_id,
                           username, password,
                           triggered_by="auto", callback=_on_done)
    if not ok:
        with _lock:
            _state["running"] = False
            _state["last_status"] = "skipped"
            _state["last_error"] = "已有同步在进行中"
    return ok


def start_auto_sync(session_factory) -> bool:
    """启动自动检测调度线程。返回是否启动。"""
    global _started
    if not config.AUTO_SYNC_ENABLED:
        return False
    with _lock:
        _state["enabled"] = True
        _state["schedule"] = config.AUTO_SYNC_TIME
        if _started:
            return False
        _started = True

    def _loop():
        while True:
            if not config.AUTO_SYNC_ENABLED:
                with _lock:
                    _state["enabled"] = False
                    _state["next_run_at"] = None
                time.sleep(5)
                continue
            next_at = _next_run_after(datetime.now())
            with _lock:
                _state["next_run_at"] = next_at.isoformat()
            wait = (next_at - datetime.now()).total_seconds()
            if wait > 0:
                time.sleep(wait)
            try:
                _tick(session_factory)
            except Exception:
                pass

    threading.Thread(target=_loop, daemon=True).start()
    return True
