"""管理 API（cookie session 鉴权，Bearer token 兼容）。"""
import secrets
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

import app.config as config
from app.api.auth import current_user
from app.db import get_db
from app.models import (Bounty, BountyClaim, Captain, Game, GamePlayer, Kyoku, League,
                        Lineup, Player, ScheduleDay, SyncRun, Team)
from app.services.ninklang import fetch_tenhou
from app.services.paipu.ingest import ingest_tenhou_game
from app.services.schedule import schedule_view
from app.services.uploads import read_image_upload

router = APIRouter()

UPLOAD_DIR = Path(__file__).resolve().parents[2] / "web" / "uploads"

def require_admin(user: str = Depends(current_user)):
    """鉴权：cookie session 优先，Bearer token 兼容 API/CI。"""
    return user


def _league(db: Session) -> League:
    row = db.query(League).first()
    if not row:
        row = League(name="麻将联赛")
        db.add(row)
        db.commit()
    return row


async def _save_logo(file: UploadFile) -> str:
    ext, data = await read_image_upload(file)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"logo_{secrets.token_hex(6)}{ext}"
    (UPLOAD_DIR / name).write_bytes(data)
    return f"/static/uploads/{name}"


def _captain_username(db: Session, team_id: int) -> str:
    base = f"team_{team_id}"
    username = base
    suffix = 2
    while db.query(Captain).filter(Captain.username == username).first():
        username = f"{base}_{suffix}"
        suffix += 1
    return username


def _new_captain_credentials(db: Session, team: Team) -> tuple[str, str]:
    from app.api.captain import _hash_password

    username = _captain_username(db, team.id)
    password = secrets.token_urlsafe(9)
    db.add(Captain(team_id=team.id, username=username,
                   password_hash=_hash_password(username, password),
                   password_plaintext=password))
    return username, password


@router.put("/league", dependencies=[Depends(require_admin)])
def update_league(body: dict, db: Session = Depends(get_db)):
    league = _league(db)
    for key in ("name", "organizer", "season", "description", "contact"):
        if key in body and body[key] is not None:
            setattr(league, key, body[key])
    from datetime import date
    for key in ("start_date", "end_date"):
        if key in body:
            value = body[key]
            if value in (None, ""):
                setattr(league, key, None)
            else:
                try:
                    setattr(league, key, date.fromisoformat(str(value)))
                except ValueError:
                    raise HTTPException(422, f"{key} 必须是 YYYY-MM-DD")
    if "contest_id" in body:
        value = body["contest_id"]
        if value in (None, ""):
            league.contest_id = None
        else:
            try:
                league.contest_id = int(value)
            except (TypeError, ValueError):
                raise HTTPException(422, "赛事场ID必须是数字")
    db.commit()
    return {"ok": True}


@router.get("/sync-credentials", dependencies=[Depends(require_admin)])
def get_sync_credentials(db: Session = Depends(get_db)):
    league = _league(db)
    return {"username": league.sync_username or config.DHS_USERNAME,
            "password": league.sync_password or config.DHS_PASSWORD,
            "persistent": bool(league.sync_username or league.sync_password)}


@router.put("/sync-credentials", dependencies=[Depends(require_admin)])
def update_sync_credentials(body: dict, db: Session = Depends(get_db)):
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not username or not password:
        raise HTTPException(422, "赛事场账号和密码不能为空")
    league = _league(db)
    league.sync_username = username
    league.sync_password = password
    db.commit()
    return {"ok": True, "username": username, "persistent": True}


@router.get("/lobby-credentials", dependencies=[Depends(require_admin)])
def get_lobby_credentials(db: Session = Depends(get_db)):
    league = _league(db)
    return {
        "username": league.lobby_username or config.MS_USERNAME,
        "password": league.lobby_password or config.MS_PASSWORD,
        "access_token": league.lobby_access_token or config.MS_ACCESS_TOKEN,
        "persistent": bool(league.lobby_username or league.lobby_password or league.lobby_access_token),
    }


@router.put("/lobby-credentials", dependencies=[Depends(require_admin)])
def update_lobby_credentials(body: dict, db: Session = Depends(get_db)):
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    access_token = str(body.get("access_token") or "").strip()
    if not ((username and password) or access_token):
        raise HTTPException(422, "需要普通雀魂大厅账号密码，或 access_token")
    league = _league(db)
    league.lobby_username = username
    league.lobby_password = password
    league.lobby_access_token = access_token
    db.commit()
    return {"ok": True, "username": username, "persistent": True}


@router.put("/league/logo", dependencies=[Depends(require_admin)])
async def update_league_logo(file: UploadFile, db: Session = Depends(get_db)):
    league = _league(db)
    league.logo_path = await _save_logo(file)
    db.commit()
    return {"logo_path": league.logo_path}


@router.post("/teams", dependencies=[Depends(require_admin)])
def create_team(body: dict, db: Session = Depends(get_db)):
    if not body.get("name"):
        raise HTTPException(422, "队伍名称不能为空")
    if db.query(Team).filter(Team.name == body["name"]).first():
        raise HTTPException(422, "队伍已存在")
    number = body.get("team_number")
    if number in (None, ""):
        number = (db.query(Team).order_by(Team.team_number.desc()).first().team_number
                  if db.query(Team).count() else 0) + 1
    try:
        number = int(number)
    except (TypeError, ValueError):
        raise HTTPException(422, "队伍编号必须是数字")
    if number <= 0:
        raise HTTPException(422, "队伍编号必须是正整数")
    if db.query(Team).filter(Team.team_number == number).first():
        raise HTTPException(422, "队伍编号已存在")
    team = Team(name=body["name"], short_name=body.get("short_name", ""),
                color=body.get("color", "#5b8cff"), team_number=number)
    db.add(team)
    db.flush()
    username, password = _new_captain_credentials(db, team)
    db.commit()
    return {"id": team.id, "team_number": number,
            "captain": {"username": username, "password": password}}


@router.put("/teams/{team_id}", dependencies=[Depends(require_admin)])
def update_team(team_id: int, body: dict, db: Session = Depends(get_db)):
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(404)
    if "team_number" in body and body["team_number"] is not None:
        try:
            number = int(body["team_number"])
        except (TypeError, ValueError):
            raise HTTPException(422, "队伍编号必须是数字")
        if number <= 0:
            raise HTTPException(422, "队伍编号必须是正整数")
        duplicate = db.query(Team).filter(
            Team.team_number == number, Team.id != team_id).first()
        if duplicate:
            raise HTTPException(422, "队伍编号已存在")
        team.team_number = number
    for key in ("name", "short_name", "color", "sort_order"):
        if key in body and body[key] is not None:
            setattr(team, key, body[key])
    db.commit()
    return {"ok": True}


@router.get("/schedule", dependencies=[Depends(require_admin)])
def list_schedule(db: Session = Depends(get_db)):
    rows = db.query(ScheduleDay).order_by(ScheduleDay.match_date).all()
    return [{"id": row.id, "date": row.match_date.isoformat(),
             "team_numbers": row.team_numbers or [], "note": row.note or "",
             **schedule_view(db, row)}
            for row in rows]


def _parse_schedule_rows(body: dict, db: Session) -> list[tuple[date, list[int], str]]:
    rows = body.get("rows")
    if not isinstance(rows, list):
        raise HTTPException(422, "赛程必须是数组")
    parsed = []
    seen = set()
    known_numbers = {
        number for number, in db.query(Team.team_number)
        if number is not None and number > 0
    }
    for row in rows:
        if not isinstance(row, dict):
            raise HTTPException(422, "赛程行格式无效")
        try:
            match_date = date.fromisoformat(str(row.get("date") or ""))
        except ValueError:
            raise HTTPException(422, "赛程日期必须是 YYYY-MM-DD")
        if match_date in seen:
            raise HTTPException(422, "赛程日期不能重复")
        seen.add(match_date)
        numbers = row.get("team_numbers", [])
        if not isinstance(numbers, list) or len(numbers) != 4:
            raise HTTPException(422, "每天必须填写四个出战队伍编号")
        try:
            numbers = [int(number) for number in numbers]
        except (TypeError, ValueError):
            raise HTTPException(422, "队伍编号必须是数字")
        if any(number <= 0 for number in numbers) or len(set(numbers)) != 4:
            raise HTTPException(422, "每天四个出战队伍编号必须为四个不同的正整数")
        missing = sorted(set(numbers) - known_numbers)
        if missing:
            raise HTTPException(422, f"队伍编号不存在：{missing}")
        parsed.append((match_date, numbers, str(row.get("note") or "")[:255]))
    return parsed


@router.put("/schedule", dependencies=[Depends(require_admin)])
def replace_schedule(body: dict, db: Session = Depends(get_db)):
    parsed = _parse_schedule_rows(body, db)
    db.query(ScheduleDay).delete()
    db.add_all([ScheduleDay(match_date=match_date, team_numbers=numbers, note=note)
                for match_date, numbers, note in parsed])
    db.commit()
    return {"ok": True, "count": len(parsed)}


@router.post("/schedule/append", dependencies=[Depends(require_admin)])
def append_schedule(body: dict, db: Session = Depends(get_db)):
    """追加未存在日期的赛程，不会覆盖已有赛程。"""
    parsed = _parse_schedule_rows(body, db)
    existing = {row.match_date for row in db.query(ScheduleDay).all()}
    duplicate = sorted(match_date.isoformat() for match_date, _, _ in parsed
                       if match_date in existing)
    if duplicate:
        raise HTTPException(409, f"赛程日期已存在：{duplicate}")
    db.add_all([ScheduleDay(match_date=match_date, team_numbers=numbers, note=note)
                for match_date, numbers, note in parsed])
    db.commit()
    return {"ok": True, "added": len(parsed),
            "total": db.query(ScheduleDay).count()}


@router.delete("/teams/{team_id}", dependencies=[Depends(require_admin)])
def delete_team(team_id: int, db: Session = Depends(get_db)):
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(404)
    db.delete(team)
    db.commit()
    return {"ok": True}


@router.get("/players", dependencies=[Depends(require_admin)])
def list_players(db: Session = Depends(get_db)):
    rows = (db.query(Player, Team)
            .join(Team, Player.team_id == Team.id, isouter=True)
            .order_by(Team.sort_order, Team.id, Player.nickname, Player.id).all())
    return [{"id": p.id, "nickname": p.nickname, "account_id": p.account_id,
             "team_id": p.team_id, "team_name": t.name if t else None,
             "contest_registered": p.contest_registered} for p, t in rows]


@router.post("/players", dependencies=[Depends(require_admin)])
def create_player(body: dict, db: Session = Depends(get_db)):
    if not body.get("nickname"):
        raise HTTPException(422, "昵称不能为空")
    try:
        account_id = int(body.get("account_id"))
    except (TypeError, ValueError):
        account_id = 0
    if account_id <= 0:
        raise HTTPException(422, "雀魂ID不能为空")
    player = Player(nickname=body["nickname"], account_id=account_id,
                    team_id=body.get("team_id"))
    try:
        db.add(player)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(422, "创建失败（account_id 重复？）")
    return {"id": player.id}


@router.put("/players/{player_id}", dependencies=[Depends(require_admin)])
def update_player(player_id: int, body: dict, db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(404)
    if "nickname" in body and body["nickname"] is not None:
        nickname = str(body["nickname"]).strip()
        if not nickname:
            raise HTTPException(422, "昵称不能为空")
        player.nickname = nickname
    if "account_id" in body and body["account_id"] is not None:
        try:
            player.account_id = int(body["account_id"])
        except (TypeError, ValueError):
            raise HTTPException(422, "雀魂ID必须是数字")
    if "team_id" in body:
        value = body["team_id"]
        if value in (None, ""):
            player.team_id = None
        else:
            try:
                team_id = int(value)
            except (TypeError, ValueError):
                raise HTTPException(422, "队伍ID必须是数字")
            if not db.get(Team, team_id):
                raise HTTPException(422, "目标队伍不存在")
            player.team_id = team_id
    db.commit()
    return {"ok": True, "player_id": player.id, "team_id": player.team_id}


@router.delete("/players/{player_id}", dependencies=[Depends(require_admin)])
def delete_player(player_id: int, db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(404)
    if db.query(GamePlayer).filter(GamePlayer.player_id == player_id).first():
        raise HTTPException(409, "该选手已有历史对局，不能删除；请将其移出队伍")
    db.delete(player)
    db.commit()
    return {"ok": True}


@router.delete("/games/{uuid}", dependencies=[Depends(require_admin)])
def delete_game(uuid: str, db: Session = Depends(get_db)):
    """删除一场牌谱及其局记录，保留选手和队伍。"""
    game = db.get(Game, uuid)
    if not game:
        raise HTTPException(404, "牌谱不存在")
    db.query(Kyoku).filter(Kyoku.game_uuid == uuid).delete(synchronize_session=False)
    db.query(GamePlayer).filter(GamePlayer.game_uuid == uuid).delete(
        synchronize_session=False)
    db.delete(game)
    db.commit()
    return {"ok": True, "uuid": uuid}


@router.post("/games/ingest", dependencies=[Depends(require_admin)])
def ingest_share_link(body: dict, db: Session = Depends(get_db)):
    share_url = str(body.get("share_url") or "").strip()
    if not share_url:
        raise HTTPException(422, "share_url 不能为空")
    data = fetch_tenhou(share_url)
    uuid = ingest_tenhou_game(db, data, fetched_via="ninklang")
    return {"uuid": uuid}


@router.get("/sync-runs", dependencies=[Depends(require_admin)])
def list_sync_runs(db: Session = Depends(get_db)):
    runs = db.query(SyncRun).order_by(SyncRun.id.desc()).limit(30).all()
    return [{"id": r.id, "channel": r.channel, "status": r.status,
             "games_added": r.games_added, "errors": r.errors, "detail": r.detail,
             "started_at": r.started_at.isoformat() if r.started_at else None,
             "finished_at": r.finished_at.isoformat() if r.finished_at else None}
            for r in runs]


@router.post("/league/init-from-contest", dependencies=[Depends(require_admin)])
def init_from_contest(body: dict, db: Session = Depends(get_db)):
    contest_id = body.get("contest_id")
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not (isinstance(contest_id, int) and username and password):
        raise HTTPException(422, "需要 contest_id/username/password")
    # 先保存场号，网络初始化失败时也不能丢失管理员刚填写的配置。
    league = _league(db)
    league.contest_id = contest_id
    league.sync_username = username
    league.sync_password = password
    db.commit()
    from app.services.majsoul.sync import init_from_contest as _init

    try:
        result = _init(db, contest_id, username, password)
        league.sync_username = username
        league.sync_password = password
        db.commit()
        return result
    except Exception as exc:
        message = str(exc)
        if ("opening handshake" in message.lower() or "timed out" in message.lower()
                or "connecterror" in message.lower() or "connect error" in message.lower()):
            message = ("无法连接雀魂赛事 API，请检查服务器网络、代理或 DHS_API 配置；"
                       f"当前 API：{config.DHS_API}")
        raise HTTPException(502, f"赛事场初始化失败：{message}")


@router.post("/sync", dependencies=[Depends(require_admin)])
def trigger_sync(body: dict, db: Session = Depends(get_db)):
    league = _league(db)
    if not league.contest_id:
        raise HTTPException(422, "联赛未绑定赛事场ID，请先初始化")
    username = str(body.get("username") or league.sync_username or config.DHS_USERNAME).strip()
    password = str(body.get("password") or league.sync_password or config.DHS_PASSWORD)
    start_date = None
    if body.get("start_date"):
        try:
            start_date = date.fromisoformat(str(body["start_date"]))
        except ValueError:
            raise HTTPException(422, "start_date 必须是 YYYY-MM-DD")
    if not username or not password:
        raise HTTPException(422, "需要 username/password，或先保存赛事场账号")
    if body.get("username") or body.get("password"):
        league.sync_username = username
        league.sync_password = password
        db.commit()
    from app.db import SessionLocal
    from app.services.majsoul.sync import start_sync_thread

    ok = start_sync_thread(SessionLocal, league.contest_id, username, password,
                           start_date=start_date)
    if not ok:
        raise HTTPException(409, "同步正在进行中")
    return {"started": True}


@router.get("/sync/status", dependencies=[Depends(require_admin)])
def sync_status_endpoint():
    from app.services.majsoul.sync import sync_status

    return sync_status()


@router.get("/sync/auto-status", dependencies=[Depends(require_admin)])
def auto_sync_status_endpoint(db: Session = Depends(get_db)):
    from app.services.majsoul.auto_sync import auto_sync_state

    state = auto_sync_state()
    league = _league(db)
    if league.auto_sync_enabled is not None:
        state["enabled"] = bool(league.auto_sync_enabled)
    return state


@router.put("/sync/auto-status", dependencies=[Depends(require_admin)])
def update_auto_sync_status(body: dict, db: Session = Depends(get_db)):
    if not isinstance(body.get("enabled"), bool):
        raise HTTPException(422, "enabled 必须是布尔值")
    from app.db import SessionLocal
    from app.services.majsoul.auto_sync import set_runtime_enabled

    league = _league(db)
    league.auto_sync_enabled = body["enabled"]
    db.commit()
    set_runtime_enabled(body["enabled"], SessionLocal)
    return auto_sync_status_endpoint(db)


@router.get("/sync/live-status", dependencies=[Depends(require_admin)])
def live_sync_status_endpoint(db: Session = Depends(get_db)):
    from app.services.majsoul.live_sync import live_sync_state

    state = live_sync_state()
    league = _league(db)
    if league.live_sync_enabled is not None:
        state["enabled"] = bool(league.live_sync_enabled)
    state["interval"] = league.live_sync_interval or config.LIVE_SYNC_INTERVAL
    return state


@router.put("/sync/live-status", dependencies=[Depends(require_admin)])
def update_live_sync_status(body: dict, db: Session = Depends(get_db)):
    if not isinstance(body.get("enabled"), bool):
        raise HTTPException(422, "enabled 必须是布尔值")
    from app.db import SessionLocal
    from app.services.majsoul.live_sync import start_live_sync, stop_live_sync

    league = _league(db)
    username = league.lobby_username or config.MS_USERNAME
    password = league.lobby_password or config.MS_PASSWORD
    access_token = league.lobby_access_token or config.MS_ACCESS_TOKEN
    if body["enabled"] and not ((username and password) or access_token):
        raise HTTPException(422, "请先配置普通雀魂大厅账号，不能使用赛事场账号")

    league.live_sync_enabled = body["enabled"]
    if body.get("interval") is not None:
        try:
            league.live_sync_interval = max(10, int(body["interval"]))
        except (TypeError, ValueError):
            raise HTTPException(422, "interval 必须是数字")
    db.commit()
    if not body["enabled"]:
        stop_live_sync()
        return live_sync_status_endpoint(db)
    started = start_live_sync(
        SessionLocal, username, password, access_token=access_token,
        interval=league.live_sync_interval,
    )
    if not started:
        raise HTTPException(409, "大厅实时同步已经在运行")
    return live_sync_status_endpoint(db)


@router.post("/search-player", dependencies=[Depends(require_admin)])
def search_player(body: dict):
    nickname = str(body.get("nickname") or "").strip()
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not (nickname and username and password):
        raise HTTPException(422, "需要 nickname/username/password")
    import asyncio
    import hashlib
    import hmac as _hmac

    from app.services.majsoul.clients import DHSClient

    async def _main():
        client = DHSClient()
        await client.channel.connect()
        pwd_hash = _hmac.new(b"lailai", password.encode(), hashlib.sha256).hexdigest()
        await client.channel.call("loginContestManager", account=username,
                                  password=pwd_hash, type=0)
        try:
            return await client.search_by_nickname([nickname])
        finally:
            await client.close()

    try:
        return {"results": asyncio.run(_main())}
    except Exception as exc:
        raise HTTPException(502, f"查询失败：{exc}")


@router.get("/bounties", dependencies=[Depends(require_admin)])
def admin_list_bounties(db: Session = Depends(get_db)):
    rows = db.query(Bounty).order_by(Bounty.created_at.desc()).all()
    out = []
    for b in rows:
        count = (db.query(BountyClaim)
                 .filter(BountyClaim.bounty_id == b.id,
                         BountyClaim.status == "approved").count())
        out.append({
            "id": b.id, "title": b.title, "description": b.description,
            "reward": b.reward,
            "target_date": b.target_date.isoformat() if b.target_date else None,
            "max_claims": b.max_claims, "status": b.status,
            "claimed_count": count,
            "submitter_nickname": b.submitter_nickname,
            "submitter_account_id": b.submitter_account_id,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        })
    return out


@router.put("/bounties/{bounty_id}", dependencies=[Depends(require_admin)])
def admin_update_bounty(bounty_id: int, body: dict, db: Session = Depends(get_db)):
    from datetime import datetime

    bounty = db.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(404, "悬赏不存在")
    for key in ("title", "description", "reward"):
        if key in body and body[key] is not None:
            value = str(body[key]).strip()
            if key in ("title", "description") and not value:
                raise HTTPException(422, f"{key} 不能为空")
            setattr(bounty, key, value)
    if "target_date" in body:
        try:
            bounty.target_date = datetime.strptime(
                str(body["target_date"]), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            raise HTTPException(422, "target_date 必须是 YYYY-MM-DD")
    if "max_claims" in body:
        if (isinstance(body["max_claims"], bool)
                or not isinstance(body["max_claims"], int)
                or body["max_claims"] < 1):
            raise HTTPException(422, "max_claims 必须是大于等于1的整数")
        bounty.max_claims = body["max_claims"]
    if "status" in body:
        if body["status"] not in ("pending", "approved", "rejected", "closed"):
            raise HTTPException(422, "status 无效")
        bounty.status = body["status"]
        if body["status"] in ("approved", "rejected"):
            bounty.reviewed_at = datetime.now()
    db.commit()
    if bounty.status == "approved":
        count = (db.query(BountyClaim)
                 .filter(BountyClaim.bounty_id == bounty.id,
                         BountyClaim.status == "approved").count())
        if count >= bounty.max_claims:
            bounty.status = "closed"
            db.commit()
    return {"ok": True}


@router.get("/claims", dependencies=[Depends(require_admin)])
def admin_list_claims(db: Session = Depends(get_db)):
    rows = (db.query(BountyClaim, Bounty)
            .join(Bounty, BountyClaim.bounty_id == Bounty.id)
            .order_by(BountyClaim.created_at.desc()).all())
    return [{
        "id": c.id, "bounty_id": c.bounty_id, "bounty_title": b.title,
        "share_url": c.share_url, "note": c.note,
        "submitter_nickname": c.submitter_nickname,
        "submitter_account_id": c.submitter_account_id,
        "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    } for c, b in rows]


@router.put("/claims/{claim_id}", dependencies=[Depends(require_admin)])
def admin_update_claim(claim_id: int, body: dict, db: Session = Depends(get_db)):
    from datetime import datetime

    claim = db.get(BountyClaim, claim_id)
    if not claim:
        raise HTTPException(404, "提交不存在")
    if "status" in body and body["status"] in ("pending", "approved", "rejected"):
        claim.status = body["status"]
        if body["status"] in ("approved", "rejected"):
            claim.reviewed_at = datetime.now()
    db.commit()
    if claim.status == "approved":
        bounty = db.get(Bounty, claim.bounty_id)
        if bounty and bounty.status == "approved":
            count = (db.query(BountyClaim)
                     .filter(BountyClaim.bounty_id == bounty.id,
                             BountyClaim.status == "approved").count())
            if count >= bounty.max_claims:
                bounty.status = "closed"
                db.commit()
    return {"ok": True}


@router.get("/captains", dependencies=[Depends(require_admin)])
def admin_list_captains(db: Session = Depends(get_db)):
    """列出各队队长账号，并为历史队伍补齐自动生成的账号。"""
    caps = {c.team_id: c for c in db.query(Captain).all()}
    rows = (db.query(Team).order_by(Team.sort_order, Team.id).all())
    out = []
    changed = False
    for team in rows:
        generated_password = None
        captain = caps.get(team.id)
        if not captain:
            username, generated_password = _new_captain_credentials(db, team)
            captain = db.query(Captain).filter(Captain.team_id == team.id).first()
            changed = True
        elif not captain.password_plaintext:
            # 历史账号没有可显示的明文密码，首次查看时自动生成新密码。
            generated_password = secrets.token_urlsafe(9)
            from app.api.captain import _hash_password
            captain.password_hash = _hash_password(captain.username, generated_password)
            captain.password_plaintext = generated_password
            changed = True
        out.append({
            "team_id": team.id, "team_name": team.name, "color": team.color,
            "username": captain.username, "has_account": True,
            "password": generated_password or captain.password_plaintext,
            "generated_password": generated_password,
        })
    if changed:
        db.commit()
    return out


@router.put("/teams/{team_id}/captain", dependencies=[Depends(require_admin)])
def admin_upsert_captain(team_id: int, body: dict, db: Session = Depends(get_db)):
    """自动生成或重新生成某队队长密码，管理员不能手动指定密码。"""
    from app.api.captain import _hash_password

    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "队伍不存在")
    username = str(body.get("username") or "").strip()
    captain = db.query(Captain).filter(Captain.team_id == team.id).first()
    if username and len(username) < 3:
        raise HTTPException(422, "用户名至少 3 个字符")
    if not username:
        username = captain.username if captain else _captain_username(db, team.id)
    password = secrets.token_urlsafe(9)
    if captain and captain.username != username:
        # 换用户名：释放旧用户名
        if db.query(Captain).filter(Captain.username == username).first():
            raise HTTPException(422, "该用户名已被使用")
    if not captain:
        if db.query(Captain).filter(Captain.username == username).first():
            raise HTTPException(422, "该用户名已被使用")
        captain = Captain(team_id=team.id, username=username)
        db.add(captain)
    captain.username = username
    captain.password_hash = _hash_password(username, password)
    captain.password_plaintext = password
    db.commit()
    return {"ok": True, "team_id": team.id, "username": username, "password": password}


@router.get("/lineups", dependencies=[Depends(require_admin)])
def admin_list_lineups(db: Session = Depends(get_db)):
    """管理端查看全部出战名单（按比赛日倒序）。"""
    from app.api.captain import _players_by_ids

    rows = db.query(Lineup).order_by(Lineup.match_date.desc(),
                                     Lineup.team_id, Lineup.slot).all()
    teams = {t.id: t for t in db.query(Team).all()}
    return [{
        "id": lu.id, "team_id": lu.team_id,
        "team_name": teams[lu.team_id].name if lu.team_id in teams else "?",
        "date": lu.match_date.isoformat(), "slot": lu.slot,
        "players": _players_by_ids(db, lu.player_ids),
        "submitted_by": lu.submitted_by,
        "updated_at": lu.updated_at.isoformat() if lu.updated_at else None,
    } for lu in rows]
