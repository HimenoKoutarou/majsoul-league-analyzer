"""公开只读 API。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Game, GamePlayer, Kyoku, League, Player, Team
from app.services.score import compute_standings, raw_point_delta
from app.services.stats import (aggregate_games, enriched_game_player_stats,
                                 player_stats, profile_stats, team_stats,
                                 yaku_stats)

router = APIRouter()


def _serialize_gp(gp: GamePlayer, player: Player | None, team: Team | None) -> dict:
    return {
        "seat": gp.seat, "nickname": gp.nickname,
        "player_id": gp.player_id,
        "team_id": team.id if team else None,
        "team_name": team.name if team else None,
        "team_color": team.color if team else None,
        "final_score": gp.final_score, "rank": gp.rank, "pt": gp.pt,
    }


@router.get("/league")
def league_info(db: Session = Depends(get_db)):
    row = db.query(League).first()
    if not row:
        raise HTTPException(404, "联赛未初始化")
    return {
        "name": row.name, "organizer": row.organizer, "season": row.season,
        "logo_path": row.logo_path, "description": row.description,
        "start_date": row.start_date.isoformat() if row.start_date else None,
        "end_date": row.end_date.isoformat() if row.end_date else None,
        "contact": row.contact,
        "contest_id": row.contest_id, "score_rule": row.score_rule,
        "contest_rule_raw": row.contest_rule_raw,
        "game_count": db.query(Game).count(),
        "player_count": db.query(Player).count(),
        "team_count": db.query(Team).count(),
    }


@router.get("/teams")
def teams(db: Session = Depends(get_db)):
    out = []
    for team in db.query(Team).order_by(Team.sort_order, Team.id).all():
        players = (db.query(Player).filter(Player.team_id == team.id)
                   .order_by(Player.id).all())
        out.append({
            "id": team.id, "name": team.name, "short_name": team.short_name,
            "team_number": team.team_number,
            "color": team.color, "logo_path": team.logo_path,
            "players": [{"id": p.id, "nickname": p.nickname, "account_id": p.account_id,
                         "contest_registered": p.contest_registered} for p in players],
        })
    return out


@router.get("/standings")
def standings(by: str = Query("team"), db: Session = Depends(get_db)):
    if by not in ("team", "player"):
        raise HTTPException(400, "by 必须是 team 或 player")
    return compute_standings(db, by=by)


@router.get("/games")
def games_list(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
               db: Session = Depends(get_db)):
    total = db.query(Game).count()
    page_games = (db.query(Game)
                  .order_by(Game.start_time.desc().nullslast(), Game.uuid)
                  .limit(size).offset((page - 1) * size).all())
    game_ids = [game.uuid for game in page_games]
    items: dict[str, dict] = {
        game.uuid: {
            "uuid": game.uuid,
            "start_time": game.start_time.isoformat() if game.start_time else None,
            "rule": game.mode.get("disp", "") if game.mode else "",
            "fetched_via": game.fetched_via, "players": [],
        }
        for game in page_games
    }
    if not game_ids:
        return {"total": total, "items": []}
    q = (db.query(Game, GamePlayer, Player, Team)
         .join(GamePlayer, GamePlayer.game_uuid == Game.uuid)
         .join(Player, GamePlayer.player_id == Player.id, isouter=True)
         .join(Team, Player.team_id == Team.id, isouter=True)
         .filter(Game.uuid.in_(game_ids))
         .order_by(Game.start_time.desc().nullslast(), Game.uuid, GamePlayer.seat))
    rows = q.all()
    for game, gp, player, team in rows:
        items[game.uuid]["players"].append(_serialize_gp(gp, player, team))
    for item in items.values():
        item["players"].sort(key=lambda p: p["seat"])
    return {
        "total": total,
        "items": [items[game.uuid] for game in page_games],
    }


@router.get("/stats/games")
def stats_games(by: str = Query("player"), id: int | None = Query(None),
                db: Session = Depends(get_db)):
    """返回指定个人或队伍关联的全部对局，按时间倒序排列。"""
    if by not in ("player", "team"):
        raise HTTPException(400, "by 必须是 player 或 team")
    if id is None:
        return {"by": by, "id": None, "total": 0, "items": []}

    related = db.query(Game.uuid).join(
        GamePlayer, GamePlayer.game_uuid == Game.uuid
    ).join(
        Player, GamePlayer.player_id == Player.id, isouter=True
    )
    related = related.filter(
        Player.team_id == id if by == "team" else GamePlayer.player_id == id
    ).distinct()
    game_ids = [game_uuid for (game_uuid,) in related.all()]
    if not game_ids:
        return {"by": by, "id": id, "total": 0, "items": []}

    q = (db.query(Game, GamePlayer, Player, Team)
         .join(GamePlayer, GamePlayer.game_uuid == Game.uuid)
         .join(Player, GamePlayer.player_id == Player.id, isouter=True)
         .join(Team, Player.team_id == Team.id, isouter=True)
         .filter(Game.uuid.in_(game_ids))
         .order_by(Game.start_time.desc().nullslast(), Game.uuid.desc()))
    items: dict[str, dict] = {}
    for game, gp, player, team in q.all():
        item = items.setdefault(game.uuid, {
            "uuid": game.uuid,
            "start_time": game.start_time.isoformat() if game.start_time else None,
            "rule": game.mode.get("disp", "") if game.mode else "",
            "fetched_via": game.fetched_via,
            "players": [],
        })
        item["players"].append(_serialize_gp(gp, player, team))
    for item in items.values():
        item["players"].sort(key=lambda player: player["seat"])
    return {"by": by, "id": id, "total": len(items), "items": list(items.values())}


@router.get("/games/{uuid}")
def game_detail(uuid: str, db: Session = Depends(get_db)):
    game = db.get(Game, uuid)
    if not game:
        raise HTTPException(404, "牌谱不存在")
    q = (db.query(GamePlayer, Player, Team)
         .join(Player, GamePlayer.player_id == Player.id, isouter=True)
         .join(Team, Player.team_id == Team.id, isouter=True)
         .filter(GamePlayer.game_uuid == uuid))
    players = [_serialize_gp(gp, p, t) for gp, p, t in q.order_by(GamePlayer.seat).all()]
    kyokus = [{"index": k.index, "summary": k.summary, "data": k.data}
              for k in db.query(Kyoku).filter(Kyoku.game_uuid == uuid)
              .order_by(Kyoku.index).all()]
    return {
        "uuid": uuid, "start_time": game.start_time.isoformat() if game.start_time else None,
        "rule": game.mode.get("disp", "") if game.mode else "",
        "fetched_via": game.fetched_via, "head": game.raw_head,
        "players": players, "kyokus": kyokus,
    }


@router.get("/stats")
def stats(by: str = Query("player"), id: int | None = Query(None),
          db: Session = Depends(get_db)):
    if by not in ("player", "team"):
        raise HTTPException(400, "by 必须是 player 或 team")
    if id is not None:
        return team_stats(db, id) if by == "team" else player_stats(db, id)

    q = (db.query(GamePlayer, Player, Team)
         .join(Player, GamePlayer.player_id == Player.id, isouter=True)
         .join(Team, Player.team_id == Team.id, isouter=True))
    buckets: dict[object, list] = {}
    meta = {}
    query_rows = q.all()
    game_players = [gp for gp, _player, _team in query_rows]
    stats_by_gp = {
        (gp.game_uuid, gp.seat): stats for gp, stats in zip(
            game_players, enriched_game_player_stats(db, game_players))
    }
    for gp, player, team in query_rows:
        if by == "team":
            key = team.id if team else 0
            meta[key] = {"team_id": key, "name": team.name if team else "未分组"}
        else:
            key = player.id if player else ("deleted", gp.nickname)
            meta[key] = {"player_id": player.id if player else None,
                         "nickname": player.nickname if player else gp.nickname,
                         "team_name": team.name if team else None}
        buckets.setdefault(key, []).append(
            dict(stats_by_gp[(gp.game_uuid, gp.seat)], rank=gp.rank,
                 raw_points=raw_point_delta(gp.final_score),
                 final_score=gp.final_score, pt=gp.pt))

    rows = []
    for key, items in buckets.items():
        rows.append(dict(meta[key], **aggregate_games(items)))
    rows.sort(key=lambda r: -r["games"])
    return {"by": by, "rows": rows}


@router.get("/stats/yaku")
def stats_yaku(by: str = Query("player"), id: int | None = Query(None),
               db: Session = Depends(get_db)):
    if by not in ("player", "team"):
        raise HTTPException(400, "by 必须是 player 或 team")
    return yaku_stats(db, by=by, target_id=id)


@router.get("/stats/profile")
def stats_profile(by: str = Query("player"), id: int | None = Query(None),
                  db: Session = Depends(get_db)):
    if by not in ("player", "team"):
        raise HTTPException(400, "by 必须是 player 或 team")
    return profile_stats(db, by=by, target_id=id)


@router.get("/stats/trend")
def stats_trend(by: str = Query("player"), id: int | None = Query(None),
                db: Session = Depends(get_db)):
    """累计积分/素点推移（按对局时间升序）。"""
    q = (db.query(GamePlayer, Game, Player, Team)
         .join(Game, Game.uuid == GamePlayer.game_uuid)
         .join(Player, GamePlayer.player_id == Player.id, isouter=True)
         .join(Team, Player.team_id == Team.id, isouter=True)
         .order_by(Game.start_time.nullslast(), Game.uuid))
    if id is not None:
        q = q.filter(Player.team_id == id if by == "team" else GamePlayer.player_id == id)
    league = db.query(League).first()
    rule = (league.score_rule if league else None) or {
        "rank_points": [90, 45, 0, -45], "allow_negative": True,
        "tiebreak": "raw_points",
    }
    rp = rule.get("rank_points", [90, 45, 0, -45])
    allow_negative = bool(rule.get("allow_negative", True))

    acc: dict = {}
    for gp, game, player, team in q.all():
        if by == "team":
            key = team.id if team else 0
            label = team.name if team else "未分组"
        else:
            key = player.id if player else -gp.seat - 1000
            label = player.nickname if player else gp.nickname
        entry = acc.setdefault(key, {
            ("team_id" if by == "team" else "player_id"): key, "name": label,
            "games": [], "ranks": [], "points": [], "raw_points": []})
        prev_p = entry["points"][-1] if entry["points"] else 0
        prev_r = entry["raw_points"][-1] if entry["raw_points"] else 0
        entry["games"].append(game.start_time.isoformat() if game.start_time else game.uuid)
        entry["ranks"].append(gp.rank)
        points = prev_p + (rp[gp.rank - 1] if 1 <= gp.rank <= 4 else 0)
        entry["points"].append(points if allow_negative else max(0, points))
        entry["raw_points"].append(round(prev_r + raw_point_delta(gp.final_score), 3))
    return {"by": by, "rows": list(acc.values())}
