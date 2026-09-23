"""积分榜计算（顺位分表，查询时现算）。"""
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models import GamePlayer, League, Player, Team


RAW_POINT_BASE = 25000


def raw_point_delta(final_score: int | float | None) -> float:
    """Return one hanchan's raw score in 1000-point units."""
    return round((float(final_score or 0) - RAW_POINT_BASE) / 1000, 3)


def _rule(db: Session) -> dict:
    league = db.query(League).first()
    return (league.score_rule if league else None) or {
        "rank_points": [90, 45, 0, -45],
        "allow_negative": True,
        "tiebreak": "raw_points",
    }


def _sort_key(row):
    return (row["points"], row["_tiebreak_value"])


def compute_standings(db: Session, by: str) -> dict:
    rule = _rule(db)
    rp = rule.get("rank_points", [90, 45, 0, -45])
    allow_negative = bool(rule.get("allow_negative", True))
    tiebreak = rule.get("tiebreak", "raw_points")
    if tiebreak not in ("raw_points", "pt"):
        tiebreak = "raw_points"

    q = (db.query(GamePlayer, Player, Team)
         .join(Player, GamePlayer.player_id == Player.id, isouter=True)
         .join(Team, Player.team_id == Team.id, isouter=True))

    groups: dict = defaultdict(lambda: {"games": 0, "rank_counts": [0, 0, 0, 0],
                                        "points": 0.0, "raw_points": 0.0, "pt": 0.0})
    meta: dict = {}
    for gp, player, team in q:
        if by == "team":
            key = team.id if team else 0
            meta[key] = {"team_id": team.id if team else None,
                         "name": team.name if team else "未分组",
                         "color": team.color if team else "#8b90a3"}
        else:
            key = player.id if player else ("deleted", gp.nickname)
            meta[key] = {"player_id": player.id if player else None,
                         "nickname": player.nickname if player else gp.nickname,
                         "team_id": team.id if team else None,
                         "team_name": team.name if team else None,
                         "team_color": team.color if team else None}
        g = groups[key]
        g["games"] += 1
        g["rank_counts"][gp.rank - 1] += 1
        g["points"] += rp[gp.rank - 1] if 1 <= gp.rank <= 4 else 0
        g["raw_points"] += raw_point_delta(gp.final_score)
        g["pt"] += gp.pt

    rows = []
    for key, g in groups.items():
        row = dict(meta[key], **g)
        row["raw_points"] = round(row["raw_points"], 3)
        if not allow_negative:
            row["points"] = max(0, row["points"])
        row["_tiebreak_value"] = row["pt"] if tiebreak == "pt" else row["raw_points"]
        row["avg_rank"] = round(
            sum((i + 1) * c for i, c in enumerate(g["rank_counts"])) / g["games"], 3)
        rows.append(row)
    rows.sort(key=_sort_key, reverse=True)
    for row in rows:
        row.pop("_tiebreak_value", None)
    return {"by": by, "rule": rule, "rows": rows}
