import json
import sys
from pathlib import Path

import liqi_combined_pb2 as pb  # noqa: F401  (sys.path 已在 conftest 注入)

from app.models import Game, League, Player, SyncRun
from app.services.majsoul import sync as sync_mod
from app.services.paipu.ingest import ingest_tenhou_game

SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "sample_paipu.json").read_text(encoding="utf-8"))


async def _ok(obj):
    return obj


class FakeDHS:
    def __init__(self):
        self.logged_contest = None
        self.contest_rule = {
            "shunweima_2": 30,
            "shunweima_3": 0,
            "shunweima_4": -30,
        }

    async def fetch_contest_info(self):
        class C:
            contest_name = "2026测试联赛"
        return C()

    async def fetch_players(self):
        return [{"account_id": i + 1, "nickname": n} for i, n in enumerate(SAMPLE["name"])]

    async def fetch_record_list(self):
        # 返回一个与样本同 uuid 的 RecordGame
        rg = pb.RecordGame()
        rg.uuid = SAMPLE["ref"]
        rg.start_time = 1757929449
        rg.end_time = 1757933000
        for seat, (nick, aid) in enumerate(zip(SAMPLE["name"], range(1, 5))):
            acc = rg.accounts.add()
            acc.seat, acc.nickname, acc.account_id = seat, nick, aid
        for seat, (score, pt) in enumerate(zip([24400, 24900, 24500, 26200],
                                               [-356, 49, -155, 462])):
            p = rg.result.players.add()
            p.seat, p.part_point_1, p.total_point = seat, score, pt
        return [rg]


class FakeLobby:
    def __init__(self):
        self.fetched = []

    async def fetch_record(self, uuid):
        self.fetched.append(uuid)
        # 直接返回天凤字段（record_head_to_game 的输出）+ 样本 log
        from app.services.majsoul.parse import record_head_to_game
        head_rg = pb.RecordGame()
        head_rg.uuid = uuid
        for seat, (nick, aid) in enumerate(zip(SAMPLE["name"], range(1, 5))):
            acc = head_rg.accounts.add()
            acc.seat, acc.nickname, acc.account_id = seat, nick, aid
        for seat, (score, pt) in enumerate(zip([24400, 24900, 24500, 26200],
                                               [-356, 49, -155, 462])):
            p = head_rg.result.players.add()
            p.seat, p.part_point_1, p.total_point = seat, score, pt
        data = record_head_to_game(head_rg)
        data["log"] = SAMPLE["log"]
        return data


def test_run_dhs_sync(db):
    result = sync_mod.run_dhs_sync(
        db, contest_id=123, username="u", password="p",
        make_dhs=lambda u, p: _ok(FakeDHS()), make_lobby=lambda u, p: _ok(FakeLobby()))
    assert result["games_added"] == 1
    assert result["errors"] == []

    # 玩家已建档且带 account_id
    players = db.query(Player).all()
    assert len(players) == 4
    assert all(p.account_id for p in players)
    assert all(p.contest_registered for p in players)

    # 对局入库
    game = db.get(Game, SAMPLE["ref"])
    assert game is not None
    assert game.fetched_via == "dhs"
    # GamePlayer 关联到带 account_id 的玩家
    from app.models import GamePlayer
    gp = db.query(GamePlayer).filter_by(seat=3).one()
    player = db.get(Player, gp.player_id)
    assert player is not None and player.account_id == 4

    # 同步记录
    run = db.query(SyncRun).one()
    assert run.status == "success"
    assert db.query(League).first().score_rule["rank_points"] == [0, 30, 0, -30]

    # 幂等：再跑一次不重复入库
    result2 = sync_mod.run_dhs_sync(
        db, contest_id=123, username="u", password="p",
        make_dhs=lambda u, p: _ok(FakeDHS()), make_lobby=lambda u, p: _ok(FakeLobby()))
    assert result2["games_added"] == 0
    assert db.query(League).first().score_rule["rank_points"] == [0, 30, 0, -30]


def test_sync_partial_on_error(db):
    class BrokenLobby(FakeLobby):
        async def fetch_record(self, uuid):
            raise RuntimeError("network down")

    result = sync_mod.run_dhs_sync(
        db, contest_id=123, username="u", password="p",
        make_dhs=lambda u, p: _ok(FakeDHS()),
        make_lobby=lambda u, p: _ok(BrokenLobby()))
    assert result["games_added"] == 0
    assert len(result["errors"]) == 1
    assert "network down" in result["errors"][0]["message"]
    run = db.query(SyncRun).one()
    assert run.status == "partial"
