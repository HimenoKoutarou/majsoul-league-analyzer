import json
from pathlib import Path
from types import SimpleNamespace

import liqi_combined_pb2 as pb

from app.models import EventOutbox, Game, MajsoulFetchTask, SyncRun
from app.services.majsoul import live_sync
from app.services.majsoul.clients import RecordNotReadyError
from app.services.majsoul.parse import record_head_to_game

SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "sample_paipu.json").read_text(encoding="utf-8"))


def _head(uuid):
    head = pb.GameLiveHead(uuid=uuid, start_time=1757929449)
    return head


class FakeLobby:
    def __init__(self, record=None):
        self.record = record
        self.closed = False
        self.calls = []

    async def fetch_game_live_list(self, filter_id):
        self.calls.append(filter_id)
        return SimpleNamespace(live_list=[_head(SAMPLE["ref"])])

    async def fetch_record(self, uuid, raw=None):
        if isinstance(self.record, Exception):
            raise self.record
        return self.record

    async def close(self):
        self.closed = True


def _sample_game():
    from app.services.majsoul.parse import record_head_to_game

    head = pb.RecordGame(uuid=SAMPLE["ref"], start_time=1757929449)
    for seat, (nick, aid) in enumerate(zip(SAMPLE["name"], range(1, 5))):
        account = head.accounts.add()
        account.seat, account.nickname, account.account_id = seat, nick, aid
    for seat, (score, pt) in enumerate(zip([24400, 24900, 24500, 26200],
                                            [-356, 49, -155, 462])):
        player = head.result.players.add()
        player.seat, player.part_point_1, player.total_point = seat, score, pt
    game = record_head_to_game(head)
    game["log"] = SAMPLE["log"]
    return game


def test_live_sync_discovers_deduplicates_and_ingests(db):
    lobby = FakeLobby(_sample_game())
    result = live_sync.run_live_sync_once(
        db, "ordinary-user", "password", filter_ids=[216],
        make_lobby=lambda u, p: _async_return(lobby),
    )
    assert result["status"] == "success"
    assert result["games_added"] == 1
    assert db.get(Game, SAMPLE["ref"]) is not None
    event = db.query(EventOutbox).one()
    assert event.event_type == "game.created"
    assert event.aggregate_id == SAMPLE["ref"]
    assert len(event.payload["players"]) == 4
    task = db.get(MajsoulFetchTask, SAMPLE["ref"])
    assert task.status == "completed"
    assert task.attempts == 1
    assert db.query(SyncRun).one().channel == "lobby"

    lobby2 = FakeLobby(_sample_game())
    result2 = live_sync.run_live_sync_once(
        db, "ordinary-user", "password", filter_ids=[216],
        make_lobby=lambda u, p: _async_return(lobby2),
    )
    assert result2["games_added"] == 0
    assert db.get(MajsoulFetchTask, SAMPLE["ref"]).attempts == 1
    assert db.query(EventOutbox).count() == 1


def test_live_sync_retries_incomplete_record(db):
    lobby = FakeLobby(RecordNotReadyError("not finished"))
    result = live_sync.run_live_sync_once(
        db, "ordinary-user", "password", filter_ids=[216],
        make_lobby=lambda u, p: _async_return(lobby),
    )
    assert result["status"] == "partial"
    task = db.get(MajsoulFetchTask, SAMPLE["ref"])
    assert task.status == "retry"
    assert task.attempts == 1
    assert task.next_attempt_at is not None
    assert "not finished" in task.last_error


async def _async_return(value):
    return value
