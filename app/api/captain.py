"""队长 API：登录 + 本队出战名单管理（比赛日每天两场，18:00 截止锁定）。"""
import hashlib
import hmac
import secrets
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

import app.config as config
from app.db import get_db
from app.models import Captain, Lineup, Player, Team

router = APIRouter(prefix="/captain")

COOKIE = "cpt_session"
CUTOFF = dtime(18, 0)          # 比赛日 18:00 截止
MATCH_WEEKDAYS = (3, 5, 7)     # 周三 / 周五 / 周日
UPLOAD_DIR = Path(__file__).resolve().parents[2] / "web" / "uploads"
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".svg"}


def _hash_password(username: str, password: str) -> str:
    secret = (config.ensure_session_secret() + ":captain").encode()
    return hmac.new(secret, f"{username}:{password}".encode(), hashlib.sha256).hexdigest()


def validate_password(password: str) -> str:
    password = str(password or "")
    if len(password) < 6:
        raise HTTPException(422, "密码至少 6 个字符")
    return password


def _sign(username: str, team_id: int, exp: int) -> str:
    secret = config.ensure_session_secret().encode()
    msg = f"captain:{username}:{team_id}:{exp}".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return f"{username}:{team_id}:{exp}:{sig}"


def current_captain(
    cpt_session: str | None = Cookie(default=None, alias=COOKIE),
    db: Session = Depends(get_db),
) -> tuple[Captain, Team]:
    """依赖：校验队长 cookie，返回 (Captain, Team)，失败抛 401。"""
    if not cpt_session:
        raise HTTPException(401, "队长未登录或会话已过期")
    parts = cpt_session.split(":")
    if len(parts) != 4:
        raise HTTPException(401, "队长未登录或会话已过期")
    user, team_s, exp_s, sig = parts
    try:
        team_id, exp = int(team_s), int(exp_s)
    except ValueError:
        raise HTTPException(401, "队长未登录或会话已过期")
    if exp < time.time() or not hmac.compare_digest(_sign(user, team_id, exp), cpt_session):
        raise HTTPException(401, "队长未登录或会话已过期")
    captain = db.query(Captain).filter(Captain.username == user).first()
    if not captain or captain.team_id != team_id:
        raise HTTPException(401, "队长未登录或会话已过期")
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(401, "队伍不存在")
    return captain, team


def next_matchdays(count: int = 6) -> list[date]:
    """从今天起（含今天）往后数 count 个比赛日。"""
    out: list[date] = []
    d = date.today()
    while len(out) < count:
        if d.isoweekday() in MATCH_WEEKDAYS:
            out.append(d)
        d += timedelta(days=1)
    return out


def is_locked(match_date: date) -> bool:
    """比赛日 18:00 后或已过期的名单不可再修改。"""
    today = date.today()
    if match_date < today:
        return True
    if match_date == today and datetime.now().time() >= CUTOFF:
        return True
    return False


def _players_by_ids(db: Session, ids: list[int]) -> list[dict]:
    out = []
    for pid in (ids or []):
        p = db.get(Player, pid)
        out.append({"id": pid, "nickname": p.nickname if p else "?"})
    return out


def _serialize_lineup(db: Session, lu: Lineup | None, slot: int, locked: bool) -> dict:
    if not lu:
        return {"slot": slot, "submitted": False, "locked": locked,
                "players": [], "updated_at": None, "submitted_by": ""}
    return {
        "slot": slot, "submitted": True, "locked": locked,
        "players": _players_by_ids(db, lu.player_ids),
        "updated_at": lu.updated_at.isoformat() if lu.updated_at else None,
        "submitted_by": lu.submitted_by,
    }


@router.post("/login")
def login(body: dict, response: Response, db: Session = Depends(get_db)):
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    captain = db.query(Captain).filter(Captain.username == username).first()
    if not captain or not hmac.compare_digest(
            _hash_password(username, password), captain.password_hash):
        raise HTTPException(401, "账号或密码错误")
    exp = int(time.time()) + config.SESSION_MAX_AGE
    value = _sign(username, captain.team_id, exp)
    response.set_cookie(COOKIE, value, max_age=config.SESSION_MAX_AGE,
                        httponly=True, samesite="lax", path="/")
    return {"username": username, "team_id": captain.team_id}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.put("/password")
def change_password(body: dict, captain: tuple = Depends(current_captain),
                   db: Session = Depends(get_db)):
    """队长修改队伍管理账号密码。"""
    cap, _ = captain
    password = validate_password(body.get("password"))
    cap.password_hash = _hash_password(cap.username, password)
    db.commit()
    return {"ok": True}


@router.get("/me")
def me(captain: tuple = Depends(current_captain), db: Session = Depends(get_db)):
    cap, team = captain
    return {"username": cap.username, "team_id": team.id, "team_name": team.name}


@router.get("/players")
def my_players(captain: tuple = Depends(current_captain), db: Session = Depends(get_db)):
    _, team = captain
    rows = (db.query(Player).filter(Player.team_id == team.id)
            .order_by(Player.nickname).all())
    return [{"id": p.id, "nickname": p.nickname, "account_id": p.account_id} for p in rows]


@router.get("/matchdays")
def list_matchdays(count: int = 6):
    return [{"date": d.isoformat(),
             "weekday": "周" + "一二三四五六日"[d.weekday()],
             "locked": is_locked(d)}
            for d in next_matchdays(count)]


@router.get("/lineups")
def my_lineups(date: str, captain: tuple = Depends(current_captain),
               db: Session = Depends(get_db)):
    _, team = captain
    try:
        match_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(422, "日期无效（需 YYYY-MM-DD）")
    locked = is_locked(match_date)
    rows = {lu.slot: lu for lu in db.query(Lineup).filter(
        Lineup.team_id == team.id, Lineup.match_date == match_date).all()}
    slots = [_serialize_lineup(db, rows.get(s), s, locked) for s in (1, 2)]
    return {"date": match_date.isoformat(), "match_day": match_date.isoweekday() in MATCH_WEEKDAYS,
            "weekday": "周" + "一二三四五六日"[match_date.weekday()],
            "locked": locked, "slots": slots}


@router.get("/public/lineups")
def public_lineups(date_str: str | None = None, db: Session = Depends(get_db)):
    """公开只读：某比赛日各队两场出战名单。18:00 截止前不公开，截止后可见。"""
    if date_str:
        try:
            match_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(422, "日期无效（需 YYYY-MM-DD）")
    else:
        match_date = next_matchdays(1)[0]
    visible = is_locked(match_date)  # 18:00 截止后（含已过日期）才公开
    teams = db.query(Team).order_by(Team.sort_order, Team.id).all()
    lus = {(lu.team_id, lu.slot): lu for lu in db.query(Lineup).filter(
        Lineup.match_date == match_date).all()}
    out = []
    for t in teams:
        for slot in (1, 2):
            lu = lus.get((t.id, slot))
            players = _players_by_ids(db, lu.player_ids) if (lu and visible) else []
            out.append({
                "team_id": t.id, "team_name": t.name, "color": t.color,
                "slot": slot, "submitted": bool(lu),
                "public": visible,
                "players": players,
                "updated_at": lu.updated_at.isoformat() if lu and lu.updated_at else None,
            })
    return {"date": match_date.isoformat(),
            "weekday": "周" + "一二三四五六日"[match_date.weekday()],
            "locked": visible, "rows": out}


@router.put("/lineups")
def upsert_lineup(body: dict, captain: tuple = Depends(current_captain),
                  db: Session = Depends(get_db)):
    _, team = captain
    try:
        match_date = datetime.strptime(str(body.get("date") or ""), "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(422, "日期无效（需 YYYY-MM-DD）")
    if match_date.isoweekday() not in MATCH_WEEKDAYS:
        raise HTTPException(422, "仅周三/周五/周日比赛日可提交名单")
    if is_locked(match_date):
        raise HTTPException(403, "该比赛日名单已截止（18:00 后锁定）")
    slot = int(body.get("slot") or 0)
    if slot not in (1, 2):
        raise HTTPException(422, "场次必须为 1 或 2")
    ids = body.get("player_ids")
    if not isinstance(ids, list) or len(set(ids)) != 1:
        raise HTTPException(422, "每场每队需提交 1 名出战选手")
    for pid in ids:
        p = db.get(Player, pid)
        if not p or p.team_id != team.id:
            raise HTTPException(422, f"选手 {pid} 不属于本队")
    lu = db.query(Lineup).filter(Lineup.team_id == team.id,
                                 Lineup.match_date == match_date,
                                 Lineup.slot == slot).first()
    if not lu:
        lu = Lineup(team_id=team.id, match_date=match_date, slot=slot)
        db.add(lu)
    lu.player_ids = [int(pid) for pid in ids]
    lu.submitted_by = captain[0].username
    lu.updated_at = datetime.now()
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(409, "名单保存冲突，请重试")
    return _serialize_lineup(db, lu, slot, is_locked(match_date))


@router.get("/team")
def my_team(captain: tuple = Depends(current_captain), db: Session = Depends(get_db)):
    """队长查看本队信息（含成员与 logo）。"""
    cap, team = captain
    members = (db.query(Player).filter(Player.team_id == team.id)
               .order_by(Player.nickname).all())
    return {
        "team": {"id": team.id, "name": team.name, "short_name": team.short_name,
                 "color": team.color, "logo_path": team.logo_path},
        "members": [{"id": p.id, "nickname": p.nickname,
                     "account_id": p.account_id} for p in members],
    }


@router.put("/team")
def update_team(body: dict, captain: tuple = Depends(current_captain),
                db: Session = Depends(get_db)):
    """队长修改本队名称/简称。"""
    _, team = captain
    for key in ("name", "short_name"):
        if key in body and body[key] is not None:
            v = str(body[key]).strip()
            if key == "name" and not v:
                raise HTTPException(422, "队名不能为空")
            setattr(team, key, v)
    db.commit()
    return {"ok": True}


@router.post("/logo")
async def upload_logo(file: UploadFile, captain: tuple = Depends(current_captain),
                      db: Session = Depends(get_db)):
    """队长上传本队 logo。"""
    _, team = captain
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(422, f"不支持的图片格式 {ext}")
    data = await file.read()
    if len(data) > 2 * 1024 * 1024:
        raise HTTPException(422, "图片超过 2MB")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"team_{team.id}_{secrets.token_hex(6)}{ext}"
    (UPLOAD_DIR / name).write_bytes(data)
    team.logo_path = f"/static/uploads/{name}"
    db.commit()
    return {"logo_path": team.logo_path}


@router.post("/players")
def add_player(body: dict, captain: tuple = Depends(current_captain),
               db: Session = Depends(get_db)):
    """队长添加本队选手。"""
    _, team = captain
    nickname = str(body.get("nickname") or "").strip()
    if not nickname:
        raise HTTPException(422, "昵称不能为空")
    try:
        account_id = int(body.get("account_id"))
    except (TypeError, ValueError):
        account_id = 0
    if account_id <= 0:
        raise HTTPException(422, "雀魂ID不能为空")
    player = Player(nickname=nickname, account_id=account_id, team_id=team.id)
    try:
        db.add(player)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(422, "创建失败（account_id 已被占用？）")
    return {"id": player.id}


@router.delete("/players/{player_id}")
def remove_player(player_id: int, captain: tuple = Depends(current_captain),
                  db: Session = Depends(get_db)):
    """队长移除本队选手。"""
    _, team = captain
    player = db.get(Player, player_id)
    if not player or player.team_id != team.id:
        raise HTTPException(404, "选手不存在或不属于本队")
    db.delete(player)
    db.commit()
    return {"ok": True}
