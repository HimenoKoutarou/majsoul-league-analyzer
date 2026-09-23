"""雀魂大厅实时牌谱发现、延迟重试和入库。"""
import asyncio
import threading
import time
import traceback
from datetime import datetime, timedelta

from sqlalchemy import or_

import app.config as config
from app.models import Game, MajsoulFetchTask, SyncRun
from app.services.events import enqueue_game_created
from app.services.paipu.ingest import ingest_tenhou_game

_lock = threading.Lock()
_stop = threading.Event()
_thread = None
_state = {
    "enabled": False,
    "running": False,
    "interval": config.LIVE_SYNC_INTERVAL,
    "last_run_at": None,
    "last_status": None,
    "last_added": 0,
    "pending": 0,
    "last_error": None,
}


def live_sync_state() -> dict:
    with _lock:
        return dict(_state)


def _retry_at(attempts: int) -> datetime:
    delay = min(60 * 60, 15 * (2 ** max(0, attempts - 1)))
    return datetime.now() + timedelta(seconds=delay)


def _task_for_head(db, head, filter_id: int):
    uuid = str(getattr(head, "uuid", "") or "").strip()
    if not uuid:
        return None
    task = db.get(MajsoulFetchTask, uuid)
    if task is None:
        from app.services.majsoul.clients import LobbyClient

        task = MajsoulFetchTask(
            uuid=uuid,
            source="live",
            filter_id=filter_id,
            status="pending",
            raw_head=LobbyClient.live_head_to_dict(head),
            next_attempt_at=datetime.now(),
        )
        db.add(task)
    elif task.status != "completed" and not task.raw_head:
        from app.services.majsoul.clients import LobbyClient

        task.raw_head = LobbyClient.live_head_to_dict(head)
    return task


async def _default_lobby(username: str, password: str, access_token: str = ""):
    from app.services.majsoul.clients import LobbyClient

    lobby = LobbyClient()
    await lobby.connect_login(username, password, access_token=access_token or None)
    return lobby


def run_live_sync_once(db, username: str, password: str, *, access_token: str = "",
                       filter_ids=None, make_lobby=None, max_tasks: int = 100) -> dict:
    """执行一轮实时发现和待抓任务处理，供后台线程和测试复用。"""
    run = SyncRun(channel="lobby", status="running", started_at=datetime.now())
    db.add(run)
    db.commit()
    filter_ids = tuple(filter_ids or config.LIVE_SYNC_FILTER_IDS)

    async def _main():
        if not (username or access_token):
            raise ValueError("需要普通雀魂大厅账号，不能使用赛事场账号代替")
        lobby = await (make_lobby(username, password) if make_lobby else
                       _default_lobby(username, password, access_token))
        discovered = 0
        added = 0
        errors = []
        try:
            for filter_id in filter_ids:
                response = await lobby.fetch_game_live_list(filter_id)
                for head in getattr(response, "live_list", response or []):
                    if _task_for_head(db, head, filter_id):
                        discovered += 1
            db.commit()

            now = datetime.now()
            stale_cutoff = now - timedelta(minutes=10)
            (db.query(MajsoulFetchTask)
             .filter(MajsoulFetchTask.status == "fetching")
             .filter(MajsoulFetchTask.last_attempt_at < stale_cutoff)
             .update({"status": "retry", "next_attempt_at": now},
                     synchronize_session=False))
            db.commit()
            tasks = (db.query(MajsoulFetchTask)
                     .filter(MajsoulFetchTask.status.in_(("pending", "retry")))
                     .filter(or_(MajsoulFetchTask.next_attempt_at.is_(None),
                                 MajsoulFetchTask.next_attempt_at <= now))
                     .order_by(MajsoulFetchTask.first_seen_at)
                     .limit(max_tasks).all())
            for task in tasks:
                task.status = "fetching"
                task.attempts += 1
                task.last_attempt_at = datetime.now()
                db.commit()
                try:
                    data = await lobby.fetch_record(task.uuid, task.raw_head)
                    if db.get(Game, task.uuid):
                        task.status = "completed"
                        task.completed_at = datetime.now()
                    else:
                        ingest_tenhou_game(
                            db, data, fetched_via="live",
                            account_ids=data.get("account_ids"), commit=False,
                        )
                        enqueue_game_created(db, task.uuid)
                        task.status = "completed"
                        task.completed_at = datetime.now()
                        added += 1
                    task.last_error = ""
                    task.next_attempt_at = None
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    task = db.get(MajsoulFetchTask, task.uuid)
                    task.status = "retry"
                    task.last_error = f"{type(exc).__name__}: {exc}"
                    task.next_attempt_at = _retry_at(task.attempts)
                    db.commit()
                    errors.append({"uuid": task.uuid, "message": task.last_error})
            return {"games_added": added, "errors": errors,
                    "discovered": discovered, "processed": len(tasks)}
        finally:
            await lobby.close()

    try:
        result = asyncio.run(_main())
        run.status = "success" if not result["errors"] else "partial"
        run.games_added = result["games_added"]
        run.errors = result["errors"]
        run.detail = {k: result[k] for k in ("discovered", "processed")}
    except Exception as exc:
        db.rollback()
        run.status = "failed"
        run.errors = [{"message": f"{type(exc).__name__}: {exc}",
                       "trace": traceback.format_exc()[-2000:]}]
    finally:
        run.finished_at = datetime.now()
        db.commit()
        db.refresh(run)
        with _lock:
            _state["last_run_at"] = run.started_at.isoformat()
            _state["last_status"] = run.status
            _state["last_added"] = run.games_added
            _state["last_error"] = run.errors[0].get("message") if run.errors else None
            try:
                _state["pending"] = (db.query(MajsoulFetchTask)
                                      .filter(MajsoulFetchTask.status.in_(
                                          ("pending", "retry", "fetching")))
                                      .count())
            except Exception:
                pass
    return {"games_added": run.games_added, "errors": run.errors,
            "status": run.status}


def _refresh_pending(session_factory):
    try:
        with session_factory() as db:
            count = (db.query(MajsoulFetchTask)
                     .filter(MajsoulFetchTask.status.in_(("pending", "retry", "fetching")))
                     .count())
        with _lock:
            _state["pending"] = count
    except Exception:
        pass


def start_live_sync(session_factory, username: str, password: str, *,
                    access_token: str = "", interval: int | None = None,
                    filter_ids=None) -> bool:
    """启动常驻大厅监听线程；已启动时不重复创建。"""
    global _thread
    interval = max(10, int(interval or config.LIVE_SYNC_INTERVAL))
    with _lock:
        if _thread and _thread.is_alive():
            return False
        _state.update(enabled=True, interval=interval, last_error=None)
    _stop.clear()

    def _worker():
        while not _stop.is_set():
            with _lock:
                _state["running"] = True
            try:
                with session_factory() as db:
                    run_live_sync_once(
                        db, username, password, access_token=access_token,
                        filter_ids=filter_ids,
                    )
            except Exception as exc:
                with _lock:
                    _state["last_status"] = "failed"
                    _state["last_error"] = f"{type(exc).__name__}: {exc}"
            finally:
                with _lock:
                    _state["running"] = False
                _refresh_pending(session_factory)
            _stop.wait(interval)
        with _lock:
            _state["enabled"] = False

    _thread = threading.Thread(target=_worker, name="majsoul-live-sync", daemon=True)
    _thread.start()
    return True


def stop_live_sync() -> bool:
    _stop.set()
    with _lock:
        _state["enabled"] = False
    return True
