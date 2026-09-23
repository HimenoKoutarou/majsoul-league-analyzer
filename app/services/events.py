"""跨进程事件 outbox。"""
from sqlalchemy.orm import Session

from app.models import EventOutbox, Game, GamePlayer, Player, Team


def enqueue_game_created(db: Session, game_uuid: str) -> EventOutbox | None:
    """为一场已入库牌谱创建一次性事件，调用方负责最终 commit。"""
    if db.query(EventOutbox).filter(
        EventOutbox.event_type == "game.created",
        EventOutbox.aggregate_id == game_uuid,
    ).first():
        return None

    game = db.get(Game, game_uuid)
    if not game:
        raise ValueError(f"牌谱不存在：{game_uuid}")

    rows = (db.query(GamePlayer, Player, Team)
            .join(Player, GamePlayer.player_id == Player.id, isouter=True)
            .join(Team, Player.team_id == Team.id, isouter=True)
            .filter(GamePlayer.game_uuid == game_uuid)
            .order_by(GamePlayer.seat).all())
    payload = {
        "uuid": game.uuid,
        "start_time": game.start_time.isoformat() if game.start_time else None,
        "fetched_via": game.fetched_via,
        "players": [
            {
                "seat": gp.seat,
                "nickname": gp.nickname,
                "player_id": gp.player_id,
                "team_id": team.id if team else None,
                "team_name": team.name if team else None,
                "team_color": team.color if team else None,
                "final_score": gp.final_score,
                "rank": gp.rank,
                "pt": gp.pt,
            }
            for gp, _player, team in rows
        ],
    }
    event = EventOutbox(event_type="game.created", aggregate_id=game_uuid,
                        payload=payload)
    db.add(event)
    return event
