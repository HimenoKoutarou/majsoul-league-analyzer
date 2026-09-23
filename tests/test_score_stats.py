import json
from pathlib import Path

from app.models import Game, GamePlayer, League, Player, Team
from app.services.paipu.ingest import ingest_tenhou_game
from app.services.score import compute_standings
from app.services.score import raw_point_delta
from app.services.stats import (
    _riichi_states,
    _tile_counts,
    _wait_shape,
    _waits,
    _derived_kyoku_metrics,
    aggregate_games,
    profile_stats,
    team_stats,
    yaku_stats,
)

SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "sample_paipu.json").read_text(encoding="utf-8"))


def test_raw_point_delta_uses_25000_base():
    assert raw_point_delta(24400) == -0.6
    assert raw_point_delta(26200) == 1.2


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
    assert rows["B队"]["team_id"] == 2
    assert rows["A队"]["rank_counts"] == [0, 0, 1, 1]
    assert rows["B队"]["raw_points"] == 1.1
    assert res["rows"][0]["name"] == "B队"  # 按积分排序


def test_compute_standings_by_player(db):
    _setup(db)
    res = compute_standings(db, by="player")
    top = res["rows"][0]
    assert top["nickname"] == SAMPLE["name"][3]  # 一位
    assert top["player_id"] is not None
    assert top["team_name"] == "B队"
    assert top["points"] == 90


def test_aggregate_and_team_stats(db):
    _setup(db)
    agg = aggregate_games([dict(gp.stats, rank=gp.rank,
                                raw_points=(gp.final_score - 25000) / 1000,
                                final_score=gp.final_score, pt=gp.pt)
                           for gp in db.query(GamePlayer).filter_by(seat=3)])
    assert agg["games"] == 1
    assert agg["win"] == 2
    assert agg["riichi"] == 0
    assert agg["win_rate"] > 0
    assert agg["highest_score"] == 26200
    assert agg["raw_points_sum"] == 1.2
    assert agg["avg_final_score"] == 26200

    ts = team_stats(db, team_id=2)
    assert ts["games"] == 2  # 两名队员各1场
    assert ts["rank_counts"] == [1, 1, 0, 0]
    assert ts["kyoku_played"] == 18


def test_yaku_stats(db):
    _setup(db)
    ys = yaku_stats(db, by="player")
    assert "役牌 白" in ys
    assert "裏ドラ(0飜)" not in ys


def test_ura_hit_rate():
    agg = aggregate_games([{
        "rank": 1, "kyoku_played": 4, "riichi_wins": 2, "ura_hits": 1,
    }])
    assert agg["ura_hit_rate"] == 0.5


def test_bust_rate_counts_mangan_or_above_tsumo_losses():
    summary = {
        "seats": [{}, {}, {}, {}],
        "agari": [
            {
                "winner": 0,
                "loser": 0,
                "tsumo": True,
                "score": 8000,
                "fu_han": "満貫8000点",
                "yaku": [],
            },
            {
                "winner": 2,
                "loser": 2,
                "tsumo": True,
                "score": 4000,
                "fu_han": "30符2飜4000点",
                "yaku": [],
            },
        ],
    }

    metrics = _derived_kyoku_metrics(summary, 1)

    assert metrics["tsumo_loss_count"] == 2
    assert metrics["bust_count"] == 1
    assert metrics["bust_score_total"] == 8000
    agg = aggregate_games([{
        "rank": 1,
        "raw_points": 25000,
        "kyoku_played": 1,
        **metrics,
    }])
    assert agg["bust_rate"] == 0.5
    assert agg["avg_bust_score"] == 8000
    assert agg["negative_rate"] == 0


def test_profile_stats_includes_riichi_distribution_and_tablemates(db):
    _setup(db)
    player = db.query(Player).filter(Player.nickname == SAMPLE["name"][3]).one()
    profile = profile_stats(db, by="player", target_id=player.id)

    assert set(profile) >= {
        "basic", "riichi", "more", "distribution", "lineage",
        "recent_large", "tablemates",
    }
    assert profile["basic"]["games"] == 1
    assert set(profile["distribution"]["win"]) == {"riichi", "callout", "silent"}
    assert profile["tablemates"]
    assert profile["tablemates"][0]["games"] == 1
    assert list(profile["tablemates"][0]) == [
        "player_id", "nickname", "games", "share", "win_rate",
        "self_avg_pt", "opponent_avg_pt",
    ]
    tablemates = {item["nickname"]: item for item in profile["tablemates"]}
    assert all(item["win_rate"] in (0, 1) for item in tablemates.values())
    assert all(item["self_avg_pt"] == 46.2 for item in tablemates.values())
    assert tablemates[SAMPLE["name"][1]]["opponent_avg_pt"] == 4.9
    assert 0 <= profile["riichi"]["ippatsu_rate"] <= 1
    assert 0 <= profile["riichi"]["furiten_rate"] <= 1
    assert 0 <= profile["riichi"]["multi_wait_rate"] <= 1
    assert 0 <= profile["riichi"]["good_wait_rate"] <= 1
    assert profile["more"]["max_consecutive_dealer"] >= 0
    assert profile["more"]["round_balance"] == (26200 - 25000) / 9
    assert len(profile["recent_large"]) <= 1
    assert len(profile["recent_losses"]) <= 1
    if profile["recent_large"]:
        assert profile["recent_large"][0]["hand_data"]
        assert profile["recent_large"][0]["winner_seat"] is not None
    if profile["recent_losses"]:
        assert profile["recent_losses"][0]["hand_data"]
        assert profile["recent_losses"][0]["winner_seat"] is not None
        assert "fu_han" in profile["recent_losses"][0]
        assert "yaku" in profile["recent_losses"][0]


def test_riichi_wait_shape_distinguishes_good_waits():
    samples = {
        # 23m waiting 1m/4m: two-sided.
        "ryanmen": [12, 13, 24, 25, 26, 37, 38, 39, 41, 41, 41, 45, 45],
        # 24m waiting 3m: closed wait.
        "kanchan": [12, 14, 24, 25, 26, 37, 38, 39, 41, 41, 41, 45, 45],
        # 12m waiting 3m: edge wait.
        "penchan": [11, 12, 24, 25, 26, 37, 38, 39, 41, 41, 41, 45, 45],
        # Pair of 1m and pair of 2m: double-pair wait, not good shape.
        "shanpon": [11, 11, 12, 12, 23, 24, 25, 36, 37, 38, 39, 39, 39],
        # 23456m waiting 1m/4m/7m: three-sided.
        "three_sided": [12, 13, 14, 15, 16, 27, 28, 29, 41, 41, 41, 45, 45],
    }
    expected = {
        "ryanmen": ([0, 3], (False, True)),
        "kanchan": ([2], (False, False)),
        "penchan": ([2], (False, False)),
        "shanpon": ([0, 1], (False, False)),
        "three_sided": ([0, 3, 6], (True, True)),
    }
    for name, codes in samples.items():
        counts = _tile_counts(codes)
        waits = _waits(counts)
        assert sorted(waits) == expected[name][0]
        assert _wait_shape(counts, waits) == expected[name][1]


def test_riichi_state_removes_integer_tsumogiri():
    data = SAMPLE["log"][0]
    _start, states, fixed_melds, _discarded = _riichi_states(data, 2)
    assert fixed_melds == 0
    assert len(states) == 1
    hand, waits, fixed_at_riichi = states[0]
    assert len(hand) == 13
    assert fixed_at_riichi == 0
    assert waits == {15}


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
