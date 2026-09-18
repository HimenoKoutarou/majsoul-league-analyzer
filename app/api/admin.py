"""管理 API（cookie session 鉴权，Bearer token 兼容）。"""
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

import app.config as config
from app.api.auth import current_user
from app.db import get_db
from app.models import Bounty, BountyClaim, Captain, Game, League, Lineup, Player, SyncRun, Team
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


@router.get("/sync/auto-status", dependencies=[Depends(require_admin)])
def auto_sync_status_endpoint():
    from app.services.majsoul.auto_sync import auto_sync_state

    return auto_sync_state()


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
            setattr(bounty, key, str(body[key]).strip())
    if "target_date" in body:
        try:
            bounty.target_date = datetime.strptime(
                str(body["target_date"]), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass
    if "max_claims" in body and isinstance(body["max_claims"], int) and body["max_claims"] >= 1:
        bounty.max_claims = body["max_claims"]
    if "status" in body and body["status"] in ("pending", "approved", "rejected", "closed"):
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
    """列出各队队长账号（含未创建的队伍）。"""
    caps = {c.team_id: c for c in db.query(Captain).all()}
    rows = (db.query(Team).order_by(Team.sort_order, Team.id).all())
    return [{"team_id": t.id, "team_name": t.name, "color": t.color,
             "username": caps[t.id].username if t.id in caps else None,
             "has_account": t.id in caps}
            for t in rows]


@router.put("/teams/{team_id}/captain", dependencies=[Depends(require_admin)])
def admin_upsert_captain(team_id: int, body: dict, db: Session = Depends(get_db)):
    """创建/重置某队队长账号密码。"""
    from app.api.captain import _hash_password

    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "队伍不存在")
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if len(username) < 3:
        raise HTTPException(422, "用户名至少 3 个字符")
    if len(password) < 6:
        raise HTTPException(422, "密码至少 6 个字符")
    captain = db.query(Captain).filter(Captain.team_id == team.id).first()
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
    db.commit()
    return {"ok": True, "team_id": team.id, "username": username}


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