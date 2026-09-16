"""积分榜计算（顺位分表，查询时现算）。"""
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models import GamePlayer, League, Player, Team


def _rule(db: Session) -> dict:
    league = db.query(League).first()
    return (league.score_rule if league else None) or {"rank_points": [90, 45, 0, -45]}


def _sort_key(row):
    return (row["points"], row["raw_points"])


def compute_standings(db: Session, by: str) -> dict:
    rule = _rule(db)
    rp = rule.get("rank_points", [90, 45, 0, -45])

    q = (db.query(GamePlayer, Player, Team)
         .join(Player, GamePlayer.player_id == Player.id, isouter=True)
         .join(Team, Player.team_id == Team.id, isouter=True))

    groups: dict = defaultdict(lambda: {"games": 0, "rank_counts": [0, 0, 0, 0],
                                        "points": 0.0, "raw_points": 0, "pt": 0.0})
    meta: dict = {}
    for gp, player, team in q:
        if by == "team":
            key = team.id if team else 0
            meta[key] = {"name": team.name if team else "未分组",
                         "color": team.color if team else "#8b90a3"}
        else:
            key = player.id
            meta[key] = {"nickname": player.nickname,
                         "team_name": team.name if team else None,
                         "team_color": team.color if team else None}
        g = groups[key]
        g["games"] += 1
        g["rank_counts"][gp.rank - 1] += 1
        g["points"] += rp[gp.rank - 1] if 1 <= gp.rank <= 4 else 0
        g["raw_points"] += gp.final_score
        g["pt"] += gp.pt

    rows = []
    for key, g in groups.items():
        row = dict(meta[key], **g)
        row["avg_rank"] = round(
            sum((i + 1) * c for i, c in enumerate(g["rank_counts"])) / g["games"], 3)
        rows.append(row)
    rows.sort(key=_sort_key, reverse=True)
    return {"by": by, "rule": rule, "rows": rows}