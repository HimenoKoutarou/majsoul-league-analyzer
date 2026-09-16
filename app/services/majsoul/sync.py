"""两阶段同步编排：DHS 拉对局列表 → 大厅逐场取详情入库。"""
import asyncio
import threading
import traceback
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Game, Player, SyncRun
from app.services.paipu.ingest import ingest_tenhou_game

_state_lock = threading.Lock()
_sync_state: dict = {"running": False, "phase": "", "progress": "", "error": None}


def sync_status() -> dict:
    with _state_lock:
        return dict(_sync_state)


async def _default_make_lobby(username: str, password: str):
    from app.services.majsoul.clients import LobbyClient

    client = LobbyClient()
    await client.connect_login(username, password)
    return client


def run_dhs_sync(db: Session, contest_id: int, username: str, password: str,
                 make_dhs=None, make_lobby=None, triggered_by: str = "manual") -> dict:
    """阻塞执行两阶段同步（管理端在线调用或后台线程调用）。"""
    run = SyncRun(channel="dhs", status="running", started_at=datetime.now())
    db.add(run)
    db.commit()

    async def _main():
        make_lobby_ = make_lobby or _default_make_lobby
        errors: list[dict] = []
        added = 0
        # 阶段1：DHS 登录 + 名单 + 对局列表
        if make_dhs is not None:
            dhs = await make_dhs(username, password)
        else:
            from app.services.majsoul.clients import DHSClient

            dhs = DHSClient()
            await dhs.connect_login(contest_id, username, password)
        try:
            for info in await dhs.fetch_players():
                player = db.query(Player).filter(
                    Player.account_id == info["account_id"]).first()
                if player:
                    player.nickname = info["nickname"]
                    player.contest_registered = True
                else:
                    by_nick = db.query(Player).filter(
                        Player.nickname == info["nickname"]).first()
                    if by_nick:
                        by_nick.account_id = info["account_id"]
                        by_nick.contest_registered = True
                    else:
                        db.add(Player(nickname=info["nickname"],
                                      account_id=info["account_id"],
                                      contest_registered=True))
            db.commit()
            records = await dhs.fetch_record_list()
        finally:
            try:
                await dhs.close()
            except Exception:
                pass

        # 阶段2：大厅逐场取详情
        lobby = await make_lobby_(username, password)
        try:
            for i, rg in enumerate(records):
                if db.get(Game, rg.uuid):
                    continue
                try:
                    data = await lobby.fetch_record(rg.uuid)
                    ingest_tenhou_game(db, data, fetched_via="dhs",
                                       account_ids=data.get("account_ids"))
                    added += 1
                except Exception as exc:  # 单场失败不中断
                    errors.append({"uuid": rg.uuid,
                                   "message": f"{type(exc).__name__}: {exc}"})
                    db.rollback()
        finally:
            try:
                await lobby.close()
            except Exception:
                pass
        return {"games_added": added, "errors": errors, "total_listed": len(records)}

    try:
        result = asyncio.run(_main())
        run.status = "success" if not result["errors"] else "partial"
        run.games_added = result["games_added"]
        run.errors = result["errors"]
        run.detail = {"total_listed": result["total_listed"], "triggered_by": triggered_by}
    except Exception as exc:
        db.rollback()
        run.status = "failed"
        run.errors = [{"message": f"{type(exc).__name__}: {exc}",
                       "trace": traceback.format_exc()[-2000:]}]
    finally:
        run.finished_at = datetime.now()
        db.commit()
        db.refresh(run)
    return {"games_added": run.games_added, "errors": run.errors,
            "status": run.status}


def start_sync_thread(session_factory, contest_id, username, password,
                      triggered_by="manual", callback=None):
    """后台线程执行同步（管理 API 触发）。可选 callback(result) 在完成后回调。"""
    with _state_lock:
        if _sync_state["running"]:
            return False
        _sync_state.update(running=True, phase="", progress="", error=None)

    def _worker():
        result = None
        try:
            with session_factory() as session:
                result = run_dhs_sync(session, contest_id, username, password,
                                      triggered_by=triggered_by)
            with _state_lock:
                _sync_state.update(running=False, phase="done")
        except Exception as exc:
            with _state_lock:
                _sync_state.update(running=False, phase="failed", error=str(exc))
        if callback:
            try:
                callback(result)
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True).start()
    return True


def init_from_contest(db: Session, contest_id: int, username: str, password: str) -> dict:
    """用 DHS 信息初始化联赛（名称）与参赛名单。"""
    from app.models import League

    async def _main():
        from app.services.majsoul.clients import DHSClient

        client = DHSClient()
        await client.connect_login(contest_id, username, password)
        try:
            contest = await client.fetch_contest_info()
            players = await client.fetch_players()
            return contest.contest_name, players
        finally:
            await client.close()

    name, players = asyncio.run(_main())
    league = db.query(League).first()
    if not league:
        league = League(name=name)
        db.add(league)
    league.name = name
    league.contest_id = contest_id
    for info in players:
        player = db.query(Player).filter(Player.account_id == info["account_id"]).first()
        if not player:
            player = db.query(Player).filter(Player.nickname == info["nickname"]).first()
        if player:
            player.nickname = info["nickname"]
            player.account_id = info["account_id"]
            player.contest_registered = True
        else:
            db.add(Player(nickname=info["nickname"], account_id=info["account_id"],
                          contest_registered=True))
    db.commit()
    return {"name": name, "players": len(players)}