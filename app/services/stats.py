"""聚合统计（查询层 sum/divide）。"""
from collections import Counter

from sqlalchemy.orm import Session

from app.models import GamePlayer, Player, Team


def aggregate_games(stats_list: list[dict]) -> dict:
    """把多场 game stats 聚合为总量+比率。"""
    total = Counter()
    yaku = Counter()
    rank_counts = [0, 0, 0, 0]
    raw_points = 0
    pt = 0.0
    for s in stats_list:
        for k, v in s.items():
            if k == "yaku":
                yaku.update(v)
            else:
                total[k] += v
        raw_points += s.get("raw_points", 0)
        pt += s.get("pt", 0.0)
        rank_counts[s["rank"] - 1] += 1
    games = len(stats_list)
    kp = total["kyoku_played"] or 1
    return {
        "games": games,
        "rank_counts": rank_counts,
        "avg_rank": round(sum((i + 1) * c for i, c in enumerate(rank_counts)) / games, 3) if games else 0,
        "kyoku_played": total["kyoku_played"],
        "win": total["win"], "tsumo": total["tsumo"], "dealin": total["dealin"],
        "riichi": total["riichi"], "callout_kyoku": total["callout_kyoku"],
        "ryukyoku": total["ryukyoku"],
        "win_score": total["win_score"], "dealin_score": total["dealin_score"],
        "raw_points_sum": raw_points, "pt_sum": pt,
        "win_rate": round(total["win"] / kp, 4),
        "tsumo_share": round(total["tsumo"] / total["win"], 4) if total["win"] else 0,
        "dealin_rate": round(total["dealin"] / kp, 4),
        "riichi_rate": round(total["riichi"] / kp, 4),
        "callout_rate": round(total["callout_kyoku"] / kp, 4),
        "avg_win_score": round(total["win_score"] / total["win"]) if total["win"] else 0,
        "avg_dealin_score": round(total["dealin_score"] / total["dealin"]) if total["dealin"] else 0,
    }


def _rows_for(db: Session, by: str, target_id: int | None):
    q = (db.query(GamePlayer, Player, Team)
         .join(Player, GamePlayer.player_id == Player.id, isouter=True)
         .join(Team, Player.team_id == Team.id, isouter=True))
    if by == "team":
        if target_id is not None:
            q = q.filter(Player.team_id == target_id)
    elif target_id is not None:
        q = q.filter(GamePlayer.player_id == target_id)
    return q.all()


def _player_rows(db: Session, player_id: int | None):
    q = db.query(GamePlayer)
    if player_id is not None:
        q = q.filter(GamePlayer.player_id == player_id)
    return q.order_by(GamePlayer.game_uuid).all()


def player_stats(db: Session, player_id: int | None) -> dict:
    rows = _player_rows(db, player_id)
    enriched = [dict(gp.stats, rank=gp.rank, raw_points=gp.final_score, pt=gp.pt) for gp in rows]
    return aggregate_games(enriched)


def team_stats(db: Session, team_id: int) -> dict:
    rows = _rows_for(db, "team", team_id)
    enriched = [dict(gp.stats, rank=gp.rank, raw_points=gp.final_score, pt=gp.pt)
                for gp, _p, _t in rows]
    return aggregate_games(enriched)


def yaku_stats(db: Session, by: str, target_id: int | None = None) -> dict:
    counter = Counter()
    for gp, _player, _team in _rows_for(db, by, target_id):
        counter.update(gp.stats.get("yaku") or {})
    return dict(counter.most_common())