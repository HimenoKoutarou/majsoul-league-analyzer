import json
from pathlib import Path

from app.models import Game, GamePlayer, League, Player, Team
from app.services.paipu.ingest import ingest_tenhou_game
from app.services.score import compute_standings
from app.services.stats import aggregate_games, team_stats, yaku_stats

SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "sample_paipu.json").read_text(encoding="utf-8"))


def _setup(db):
    db.add(Team(id=1, name="A队", color="#f00"))
    db.add(Team(id=2, name="B队", color="#0f0"))
    db.flush()
    ingest_tenhou_game(db, SAMPLE)
    for nick, team_id in [(SAMPLE["name"][0], 1), (SAMPLE["name"][1], 2),
                          (SAMPLE["name"][2], 1), (SAMPLE["name"][3], 2)]:
        db.query(Player).filter(Player.nickname == nick).update({"team_id": team_id})
    db.commit()


def test_compute_standings_by_team(db):
    _setup(db)
    res = compute_standings(db, by="team")
    rows = {r["name"]: r for r in res["rows"]}
    # A队：座位0四位(-45)+座位2三位(0)=-45；B队：座位1二位(45)+座位3一位(90)=135
    assert rows["B队"]["points"] == 135
    assert rows["A队"]["points"] == -45
    assert rows["B队"]["rank_counts"] == [1, 1, 0, 0]
    assert rows["A队"]["rank_counts"] == [0, 0, 1, 1]
    assert rows["B队"]["raw_points"] == 24900 + 26200
    assert res["rows"][0]["name"] == "B队"  # 按积分排序


def test_compute_standings_by_player(db):
    _setup(db)
    res = compute_standings(db, by="player")
    top = res["rows"][0]
    assert top["nickname"] == SAMPLE["name"][3]  # 一位
    assert top["team_name"] == "B队"
    assert top["points"] == 90


def test_aggregate_and_team_stats(db):
    _setup(db)
    agg = aggregate_games([dict(gp.stats, rank=gp.rank, raw_points=gp.final_score, pt=gp.pt)
                           for gp in db.query(GamePlayer).filter_by(seat=3)])
    assert agg["games"] == 1
    assert agg["win"] == 2
    assert agg["riichi"] == 0
    assert agg["win_rate"] > 0

    ts = team_stats(db, team_id=2)
    assert ts["games"] == 2  # 两名队员各1场
    assert ts["rank_counts"] == [1, 1, 0, 0]
    assert ts["kyoku_played"] == 18


def test_yaku_stats(db):
    _setup(db)
    ys = yaku_stats(db, by="player")
    assert "役牌 白(1飜)" in ys


def test_standings_apply_negative_rule_and_tiebreak(db):
    db.add(League(score_rule={
        "rank_points": [90, 45, 0, -45],
        "allow_negative": False,
        "tiebreak": "pt",
    }))
    db.add_all([
        Team(id=1, name="A队"),
        Player(id=1, nickname="低素点", account_id=1, team_id=1),
        Player(id=2, nickname="高素点", account_id=2, team_id=1),
        Game(uuid="g1"),
        Game(uuid="g2"),
    ])
    db.flush()
    db.add_all([
        GamePlayer(game_uuid="g1", seat=0, player_id=1, nickname="低素点",
                   rank=4, final_score=30000, pt=1),
        GamePlayer(game_uuid="g2", seat=0, player_id=2, nickname="高素点",
                   rank=4, final_score=10000, pt=2),
    ])
    db.commit()
    rows = compute_standings(db, by="player")["rows"]
    assert [row["nickname"] for row in rows] == ["高素点", "低素点"]
    assert all(row["points"] == 0 for row in rows)


def test_standings_keep_deleted_player_history_readable(db):
    db.add(League())
    db.add(Game(uuid="orphan-game"))
    db.add(GamePlayer(game_uuid="orphan-game", seat=0, player_id=None,
                      nickname="已删除选手", rank=1, final_score=25000))
    db.commit()
    rows = compute_standings(db, by="player")["rows"]
    assert rows[0]["nickname"] == "已删除选手"
