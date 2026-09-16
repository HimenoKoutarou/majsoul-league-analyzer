"""管理 API（cookie session 鉴权，Bearer token 兼容）。"""
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

import app.config as config
from app.api.auth import current_user
from app.db import get_db
from app.models import Game, League, Player, SyncRun, Team
from app.services.ninklang import fetch_tenhou
from app.services.paipu.ingest import ingest_tenhou_game

router = APIRouter()

UPLOAD_DIR = Path(__file__).resolve().parents[2] / "web" / "uploads"
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".svg"}


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
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(422, f"不支持的图片格式 {ext}")
    data = await file.read()
    if len(data) > 2 * 1024 * 1024:
        raise HTTPException(422, "图片超过 2MB")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"logo_{secrets.token_hex(6)}{ext}"
    (UPLOAD_DIR / name).write_bytes(data)
    return f"/static/uploads/{name}"


@router.put("/league", dependencies=[Depends(require_admin)])
def update_league(body: dict, db: Session = Depends(get_db)):
    league = _league(db)
    for key in ("name", "description", "contest_id"):
        if key in body and body[key] is not None:
            setattr(league, key, body[key])
    db.commit()
    return {"ok": True}


@router.put("/league/logo", dependencies=[Depends(require_admin)])
async def update_league_logo(file: UploadFile, db: Session = Depends(get_db)):
    league = _league(db)
    league.logo_path = await _save_logo(file)
    db.commit()
    return {"logo_path": league.logo_path}


@router.put("/score-rule", dependencies=[Depends(require_admin)])
def update_score_rule(body: dict, db: Session = Depends(get_db)):
    rp = body.get("rank_points")
    if (not isinstance(rp, list) or len(rp) != 4
            or not all(isinstance(x, (int, float)) for x in rp)):
        raise HTTPException(422, "rank_points 必须是4个数字")
    league = _league(db)
    league.score_rule = {
        "rank_points": rp,
        "allow_negative": bool(body.get("allow_negative", True)),
        "tiebreak": body.get("tiebreak", "raw_points"),
    }
    db.commit()
    return {"score_rule": league.score_rule}


@router.post("/teams", dependencies=[Depends(require_admin)])
def create_team(body: dict, db: Session = Depends(get_db)):
    if not body.get("name"):
        raise HTTPException(422, "队伍名称不能为空")
    if db.query(Team).filter(Team.name == body["name"]).first():
        raise HTTPException(422, "队伍已存在")
    team = Team(name=body["name"], short_name=body.get("short_name", ""),
                color=body.get("color", "#5b8cff"))
    db.add(team)
    db.commit()
    return {"id": team.id}


@router.put("/teams/{team_id}", dependencies=[Depends(require_admin)])
def update_team(team_id: int, body: dict, db: Session = Depends(get_db)):
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(404)
    for key in ("name", "short_name", "color", "sort_order"):
        if key in body and body[key] is not None:
            setattr(team, key, body[key])
    db.commit()
    return {"ok": True}


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
    for key in ("nickname", "account_id", "team_id"):
        if key in body:
            setattr(player, key, body[key])
    db.commit()
    return {"ok": True}


@router.delete("/players/{player_id}", dependencies=[Depends(require_admin)])
def delete_player(player_id: int, db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(404)
    db.delete(player)
    db.commit()
    return {"ok": True}


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
    from app.services.majsoul.sync import init_from_contest as _init

    try:
        return _init(db, contest_id, username, password)
    except Exception as exc:
        raise HTTPException(502, f"赛事场初始化失败：{exc}")


@router.post("/sync", dependencies=[Depends(require_admin)])
def trigger_sync(body: dict, db: Session = Depends(get_db)):
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    league = _league(db)
    if not league.contest_id:
        raise HTTPException(422, "联赛未绑定赛事场ID，请先初始化")
    if not username or not password:
        raise HTTPException(422, "需要 username/password")
    from app.db import SessionLocal
    from app.services.majsoul.sync import start_sync_thread

    ok = start_sync_thread(SessionLocal, league.contest_id, username, password)
    if not ok:
        raise HTTPException(409, "同步正在进行中")
    return {"started": True}


@router.get("/sync/status", dependencies=[Depends(require_admin)])
def sync_status_endpoint():
    from app.services.majsoul.sync import sync_status

    return sync_status()


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