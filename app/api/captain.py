"""队长 API：登录 + 本队出战名单管理（比赛日每天两场，18:00 截止锁定）。"""
import hashlib
import hmac
import asyncio
import secrets
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

import app.config as config
from app.db import get_db
from app.models import Captain, League, Lineup, Player, Team
from app.services.schedule import (fallback_matchday, has_schedule, scheduled_day,
                                    scheduled_matchdays, schedule_view)
from app.services.uploads import read_image_upload

router = APIRouter(prefix="/captain")

COOKIE = "cpt_session"
CUTOFF = dtime(18, 0)          # 比赛日 18:00 截止
MATCH_WEEKDAYS = (3, 5, 7)     # 周三 / 周五 / 周日
UPLOAD_DIR = Path(__file__).resolve().parents[2] / "web" / "uploads"


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


def _schedule_for_date(db: Session, match_date: date) -> dict:
    """Return schedule metadata, preserving the old weekday fallback when empty."""
    configured = has_schedule(db)
    row = scheduled_day(db, match_date) if configured else None
    if configured:
        view = schedule_view(db, row)
        view["scheduled"] = row is not None
        view["match_day"] = row is not None
        return view
    teams = db.query(Team).order_by(Team.sort_order, Team.id).all()
    active = teams if fallback_matchday(match_date) else []
    return {
        "date": match_date.isoformat(),
        "team_numbers": [t.team_number for t in active if t.team_number is not None],
        "active_teams": [{"id": t.id, "team_number": t.team_number,
                          "name": t.name, "color": t.color} for t in active],
        "bye_teams": [{"id": t.id, "team_number": t.team_number,
                       "name": t.name, "color": t.color}
                      for t in teams if t not in active],
        "note": "",
        "scheduled": False,
        "match_day": bool(active),
    }


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
    cap.password_plaintext = password
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


@router.get("/verify-player")
def verify_player(account_id: int, captain: tuple = Depends(current_captain),
                  db: Session = Depends(get_db)):
    """通过服务器配置的赛事管理账号验证雀魂账号 ID，不接触队长的雀魂密码。"""
    if account_id <= 0:
        raise HTTPException(422, "雀魂ID必须是正整数")
    league = db.query(League).first()
    username = (getattr(league, "sync_username", "") if league else "") or config.DHS_USERNAME
    password = (getattr(league, "sync_password", "") if league else "") or config.DHS_PASSWORD
    if not (username and password):
        raise HTTPException(503, "服务器未配置雀魂验证服务（DHS_USERNAME/DHS_PASSWORD）")

    from app.services.majsoul.clients import DHSClient

    async def _search():
        client = DHSClient()
        try:
            await client.channel.connect()
            from app.services.majsoul.clients import majsoul_password_hash
            await client.channel.call(
                "loginContestManager",
                account=username,
                password=majsoul_password_hash(password),
                type=0,
            )
            return await client.search_by_account_id(account_id)
        finally:
            await client.close()

    try:
        results = asyncio.run(_search())
    except Exception as exc:
        raise HTTPException(502, f"雀魂账号验证失败：{exc}")
    match = next((item for item in results if item["account_id"] == account_id), None)
    if not match:
        return {"exists": False, "account_id": account_id, "nickname": None}
    return {"exists": True, **match}


@router.get("/matchdays")
def list_matchdays(count: int = 6, captain: tuple = Depends(current_captain),
                   db: Session = Depends(get_db)):
    count = max(1, min(int(count), 30))
    _, team = captain
    if has_schedule(db):
        # 管理员上传的赛程可能包含本队轮空日，队长只需要管理本队出战日期。
        rows = scheduled_matchdays(db, count=30)
        days = [
            row.match_date for row in rows
            if team.team_number in {int(number) for number in (row.team_numbers or [])}
        ][:count]
    else:
        days = next_matchdays(count)
    return [{"date": d.isoformat(),
             "weekday": "周" + "一二三四五六日"[d.weekday()],
             "locked": is_locked(d),
             **_schedule_for_date(db, d)}
            for d in days]


@router.get("/lineups")
def my_lineups(date: str, captain: tuple = Depends(current_captain),
               db: Session = Depends(get_db)):
    _, team = captain
    try:
        match_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(422, "日期无效（需 YYYY-MM-DD）")
    schedule = _schedule_for_date(db, match_date)
    locked = is_locked(match_date)
    rows = {lu.slot: lu for lu in db.query(Lineup).filter(
        Lineup.team_id == team.id, Lineup.match_date == match_date).all()}
    slots = [_serialize_lineup(db, rows.get(s), s, locked) for s in (1, 2)]
    return {"date": match_date.isoformat(), "match_day": schedule["match_day"],
            "weekday": "周" + "一二三四五六日"[match_date.weekday()],
            "locked": locked, "scheduled": schedule["scheduled"],
            "team_numbers": schedule["team_numbers"],
            "active_teams": schedule["active_teams"],
            "bye_teams": schedule["bye_teams"], "note": schedule["note"],
            "team_active": team.id in {item["id"] for item in schedule["active_teams"]},
            "slots": slots}


@router.get("/public/lineups")
def public_lineups(date_str: str | None = None, db: Session = Depends(get_db)):
    """公开只读：某比赛日各队两场出战名单。18:00 截止前不公开，截止后可见。"""
    if date_str:
        try:
            match_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(422, "日期无效（需 YYYY-MM-DD）")
    else:
        if has_schedule(db):
            rows = scheduled_matchdays(db, 2)
            if not rows:
                raise HTTPException(404, "暂无已上传赛程")
            match_date = rows[0].match_date
        else:
            match_date = next_matchdays(1)[0]
        # 当天比赛日过了 18:00 后，默认展示下一个比赛日。
        # 这里直接判断当前时间，避免测试或调用方替换 is_locked 影响日期选择。
        if match_date == date.today() and datetime.now().time() >= CUTOFF:
            if has_schedule(db):
                rows = scheduled_matchdays(db, 2)
                if len(rows) > 1:
                    match_date = rows[1].match_date
            else:
                match_date = next_matchdays(2)[1]
    visible = is_locked(match_date)  # 18:00 截止后（含已过日期）才公开
    schedule = _schedule_for_date(db, match_date)
    active_ids = {team["id"] for team in schedule["active_teams"]}
    teams = [team for team in db.query(Team).order_by(Team.sort_order, Team.id).all()
             if team.id in active_ids]
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
            "locked": visible, "scheduled": schedule["scheduled"],
            "match_day": schedule["match_day"],
            "team_numbers": schedule["team_numbers"],
            "active_teams": schedule["active_teams"],
            "bye_teams": schedule["bye_teams"], "note": schedule["note"],
            "rows": out}


@router.put("/lineups")
def upsert_lineup(body: dict, captain: tuple = Depends(current_captain),
                  db: Session = Depends(get_db)):
    _, team = captain
    try:
        match_date = datetime.strptime(str(body.get("date") or ""), "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(422, "日期无效（需 YYYY-MM-DD）")
    schedule = _schedule_for_date(db, match_date)
    if not schedule["match_day"]:
        raise HTTPException(422, "该日期不是已安排的比赛日")
    if has_schedule(db) and team.id not in {t["id"] for t in schedule["active_teams"]}:
        raise HTTPException(422, "本队当天轮空，不能提交出战名单")
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
    ext, data = await read_image_upload(file)
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
