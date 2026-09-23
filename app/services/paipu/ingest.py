"""天凤格式牌谱 JSON → 数据库（games/game_players/kyokus + 每场技术统计）。"""
from collections import Counter
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Game, GamePlayer, Kyoku, Player
from app.services.paipu.kyoku import analyze_kyoku
from app.services.stats import _is_mangan_or_above, is_countable_yaku


def _find_or_create_player(db: Session, nickname: str, account_id: int | None) -> Player:
    if account_id is not None:
        player = db.query(Player).filter(Player.account_id == account_id).first()
        if player:
            if player.nickname != nickname:
                player.nickname = nickname
            return player
    player = db.query(Player).filter(Player.nickname == nickname).order_by(Player.id).first()
    if player:
        if account_id is not None and player.account_id is None:
            player.account_id = account_id
        return player
    player = Player(nickname=nickname, account_id=account_id, contest_registered=account_id is not None)
    db.add(player)
    db.flush()
    return player


def _seat_stats(analyses: list[dict]) -> list[dict]:
    stats = [Counter() for _ in range(4)]
    yaku = [Counter() for _ in range(4)]
    for a in analyses:
        for seat in range(4):
            stats[seat]["kyoku_played"] += 1
            s = a["seats"][seat]
            if s["riichi"]:
                stats[seat]["riichi"] += 1
            if s["callouts"] > 0:
                stats[seat]["callout_kyoku"] += 1
            else:
                stats[seat].setdefault("callout_kyoku", 0)
            stats[seat].setdefault("riichi", 0)
        if a["end"] in ("ryukyoku", "nagashi"):
            for seat in range(4):
                stats[seat]["ryukyoku"] += 1
        for ag in a["agari"]:
            stats[ag["winner"]]["win"] += 1
            if ag["tsumo"]:
                stats[ag["winner"]]["tsumo"] += 1
                for seat in range(4):
                    if seat == ag["winner"]:
                        continue
                    stats[seat]["tsumo_loss_count"] += 1
                    if _is_mangan_or_above(ag):
                        stats[seat]["bust_count"] += 1
                        stats[seat]["bust_score_total"] += ag["score"]
            if not a["seats"][ag["winner"]]["riichi"]:
                stats[ag["winner"]]["silent_wins"] += 1
            stats[ag["winner"]]["win_score"] += ag["score"]
            for name in ag["yaku"]:
                if is_countable_yaku(name):
                    yaku[ag["winner"]][name] += 1
            if not ag["tsumo"]:
                stats[ag["loser"]]["dealin"] += 1
                stats[ag["loser"]]["dealin_score"] += ag["score"]
    return [{**dict(s), "yaku": dict(y)} for s, y in zip(stats, yaku)]


def ingest_tenhou_game(db: Session, data: dict, fetched_via: str = "ninklang",
                       account_ids: dict[int, int] | None = None,
                       *, commit: bool = True) -> str:
    """入库一场天凤格式牌谱。幂等（按 uuid 去重）。返回 uuid。

    account_ids: 座位 → 雀魂 account_id（DHS 通道提供；ninklang 通道为 None 走昵称匹配）。
    """
    names = data.get("name")
    log = data.get("log")
    if not isinstance(names, list) or len(names) != 4 or not isinstance(log, list) or not log:
        raise ValueError("牌谱数据不完整（需要 name[4] 与非空 log）")
    uuid = str(data.get("ref") or "").strip()
    if not uuid:
        raise ValueError("牌谱缺少 ref（uuid）")

    if db.get(Game, uuid):
        return uuid

    account_ids = account_ids or {}
    sc = data.get("sc") or []
    scores = [int(sc[2 * i]) if len(sc) > 2 * i else 0 for i in range(4)]
    pts = [float(sc[2 * i + 1]) if len(sc) > 2 * i + 1 else 0.0 for i in range(4)]
    order = sorted(range(4), key=lambda s: (-scores[s], s))
    ranks = [0] * 4
    for pos, seat in enumerate(order):
        ranks[seat] = pos + 1

    start_time = None
    try:
        start_time = datetime.strptime(str(data.get("title", ["", ""])[1]), "%Y-%m-%d %H:%M:%S")
    except (IndexError, ValueError):
        pass

    rule = data.get("rule") or {}
    raw_head = {k: data.get(k) for k in ("name", "dan", "rate", "sx", "title", "rule", "sc") if k in data}

    analyses = [analyze_kyoku(k) for k in log]
    seat_stats = _seat_stats(analyses)

    db.add(Game(uuid=uuid, contest_id=None, start_time=start_time, mode=rule,
                raw_head=raw_head, fetched_via=fetched_via))
    for seat in range(4):
        player = _find_or_create_player(db, str(names[seat]), account_ids.get(seat))
        db.add(GamePlayer(game_uuid=uuid, seat=seat, player_id=player.id,
                          nickname=str(names[seat]), final_score=scores[seat],
                          rank=ranks[seat], pt=pts[seat], stats=seat_stats[seat]))
    for i, (k, a) in enumerate(zip(log, analyses)):
        db.add(Kyoku(game_uuid=uuid, index=i, round_data=a["round"], data=k, summary=a))
    if commit:
        db.commit()
    return uuid
