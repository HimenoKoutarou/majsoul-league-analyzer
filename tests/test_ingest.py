import json
from pathlib import Path

import pytest

from app.models import Game, GamePlayer, Kyoku, Player
from app.services.paipu.ingest import ingest_tenhou_game

SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "sample_paipu.json").read_text(encoding="utf-8"))


def test_ingest_sample(db):
    uuid = ingest_tenhou_game(db, SAMPLE, fetched_via="ninklang")
    assert uuid == SAMPLE["ref"]

    game = db.get(Game, uuid)
    assert game.start_time is not None
    assert game.mode["disp"] == "四麻 半庄"
    assert game.fetched_via == "ninklang"

    # 4 名玩家自动建档（昵称匹配占位）
    players = db.query(Player).order_by(Player.id).all()
    assert len(players) == 4
    assert all(p.account_id is None for p in players)  # ninklang 通道无 account_id

    gps = db.query(GamePlayer).order_by(GamePlayer.seat).all()
    assert [gp.final_score for gp in gps] == [24400, 24900, 24500, 26200]
    assert [gp.rank for gp in gps] == [4, 2, 3, 1]
    assert [gp.pt for gp in gps] == [-35.6, 4.9, -15.5, 46.2]

    # 座位3（姬野家の星奏，一位）：第2局荣和 2000，无立直；样本里它东一局立直过
    s3 = gps[3].stats
    assert s3["kyoku_played"] == 9
    assert s3["riichi"] == 0
    assert s3["win"] == 1
    assert s3["tsumo"] == 0
    assert s3["dealin"] == 0
    assert s3["win_score"] == 2000
    assert s3["yaku"]["役牌 白(1飜)"] == 1
    assert s3["yaku"]["ドラ(1飜)"] == 1

    # 座位0 放铳第2局
    s0 = gps[0].stats
    assert s0["dealin"] == 1
    assert s0["dealin_score"] == 2000

    # 座位3 在东一局碰过（"47p4747"）
    s3b = gps[3].stats
    assert s3b["callout_kyoku"] >= 1

    assert db.query(Kyoku).count() == 9


def test_ingest_idempotent(db):
    ingest_tenhou_game(db, SAMPLE)
    ingest_tenhou_game(db, SAMPLE)
    assert db.query(Game).count() == 1
    assert db.query(Player).count() == 4


def test_ingest_with_account_ids(db):
    data = json.loads(json.dumps(SAMPLE))  # 深拷贝
    data["ref"] = "260915-test-uuid-0001"
    ids = {0: 111, 1: 222, 2: 333, 3: 444}
    ingest_tenhou_game(db, data, fetched_via="dhs", account_ids=ids)
    players = db.query(Player).filter(Player.account_id.isnot(None)).all()
    assert {p.account_id for p in players} == {111, 222, 333, 444}
    assert all(p.contest_registered for p in players)  # DHS 通道玩家视为已注册


def test_ingest_invalid(db):
    with pytest.raises(ValueError):
        ingest_tenhou_game(db, {"name": ["a"], "log": []})