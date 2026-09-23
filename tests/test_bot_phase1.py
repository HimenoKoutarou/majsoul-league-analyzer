import asyncio

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app.db import get_db
from app.main import create_app
from app.models import EventOutbox, League
from bot.commands import CommandService
from bot.renderers import (render_daily_digest, render_game_created,
                            render_game_detail, render_recent_profile,
                            render_related_games, render_riichi_profile,
                            render_tablemates)
from bot.state import StateStore


def _auth():
    return {"X-Bot-Token": "bot-test-token"}


@pytest.fixture
def client(db):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: (yield db)
    with TestClient(app) as test_client:
        yield test_client


def test_bot_events_api_requires_separate_token(client, db, monkeypatch):
    db.add(League(name="测试联赛"))
    db.add(EventOutbox(event_type="game.created", aggregate_id="game-1",
                       payload={"uuid": "game-1", "players": []}))
    db.commit()
    monkeypatch.setattr(config, "BOT_API_TOKEN", "bot-test-token")

    assert client.get("/api/bot/events").status_code == 401
    response = client.get("/api/bot/events", headers=_auth())
    assert response.status_code == 200
    assert response.json()["items"][0]["aggregate_id"] == "game-1"

    empty = client.get("/api/bot/events?after_id=1", headers=_auth())
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert empty.json()["next_id"] == 1


def test_bot_state_and_rendering(tmp_path):
    state = StateStore(tmp_path / "state.json")
    state.event_cursor = 12
    state.save()
    loaded = StateStore(tmp_path / "state.json")
    assert loaded.event_cursor == 12
    loaded.mark_digest_sent("2026-09-23")
    reloaded = StateStore(tmp_path / "state.json")
    assert reloaded.digest_sent("2026-09-23")
    assert not reloaded.digest_sent("2026-09-24")

    message = render_game_created({
        "uuid": "game-1",
        "start_time": "2026-09-23T20:00:00",
        "players": [{
            "seat": 0, "nickname": "甲", "team_name": "红中会",
            "rank": 1, "final_score": 32000, "pt": 62.0,
        }],
    })
    assert "红中会" in message
    assert "+32000点" in message
    assert "+62.0pt" in message

    assert state.target_groups(("100", "200")) == ("100", "200")
    assert state.subscribe("300", ("100", "200"))
    assert state.target_groups(("100", "200")) == ("100", "200", "300")
    assert state.unsubscribe("100", ("100", "200"))
    assert state.target_groups(("100", "200")) == ("200", "300")

    detail = render_game_detail({
        "uuid": "game-1",
        "start_time": "2026-09-23T20:00:00",
        "players": [{"seat": 0, "rank": 1, "nickname": "甲",
                     "final_score": 32000, "pt": 62.0}],
        "kyokus": [{"index": 0}],
    })
    assert "对局详情" in detail
    assert "小局：1局" in detail
    assert "+62.0pt" in detail

    related = render_related_games("【关联对局】", {
        "total": 1,
        "items": [{"uuid": "game-1", "start_time": "2026-09-23T20:00:00",
                   "players": [{"rank": 1, "nickname": "甲"}]}],
    })
    assert "game-1" in related

    profile = {
        "riichi": {
            "count": 2, "win_rate": 0.5, "dealin_rate": 0.1,
            "ryukyoku_rate": 0, "first_rate": 0.5, "chase_rate": 0,
            "chased_rate": 0.5, "avg_turn": 7.5, "ippatsu_rate": 0.5,
            "furiten_rate": 0, "multi_wait_rate": 0.5,
            "good_wait_rate": 1, "ura_hit_rate": 0.5, "balance": 1000,
        },
        "recent_large": [{"match_date": "09-23", "hanchan": 2,
                          "opponents": ["乙", "丙"], "score": 12000,
                          "fu_han": "30符3番", "yaku": ["立直"]}],
        "recent_losses": [{"match_date": "09-22", "hanchan": 1,
                           "opponents": ["乙"], "score": -8000,
                           "fu_han": "40符4番", "yaku": ["混一色"]}],
        "tablemates": [{"nickname": "乙", "games": 4, "win_rate": 0.25,
                        "self_avg_pt": 12.5, "opponent_avg_pt": 8.0}],
    }
    assert "立直次数：2" in render_riichi_profile("甲", profile)
    assert "最近大额放铳" in render_recent_profile("甲", profile)
    assert "我场均积分：+12.5" in render_tablemates("甲", profile)
    digest = render_daily_digest(
        "2026-09-23",
        {"rows": [{"name": "红中会", "points": 90, "raw_points": 12.5}]},
        {"items": [{"uuid": "game-1", "start_time": "09-23", "players": []}]},
    )
    assert "每日赛事摘要" in digest
    assert "红中会" in digest
    assert "game-1" in digest


class _FakeApi:
    async def games(self, size):
        return {"items": [{"uuid": "game-1", "start_time": "now", "players": []}]}

    async def standings(self, by):
        return {"rows": [{"name": "红中会", "points": 90, "raw_points": 12.5}]}

    async def teams(self):
        return [{"id": 8, "name": "红中会", "short_name": "红中",
                 "players": [{"id": 7, "nickname": "甲"}]}]

    async def profile(self, player_id):
        assert player_id in (7, 8)
        return {"basic": {"games": 1, "avg_rank": 1, "rank_counts": [1, 0, 0, 0],
                           "win_rate": 0.5, "dealin_rate": 0,
                           "riichi_rate": 0, "pt": 90},
                "lineage": {"start_shanten": 3},
                "riichi": {"count": 2, "win_rate": 0.5,
                            "dealin_rate": 0, "ryukyoku_rate": 0,
                            "first_rate": 1, "chase_rate": 0,
                            "chased_rate": 0, "avg_turn": 6,
                            "ippatsu_rate": 0.5, "furiten_rate": 0,
                            "multi_wait_rate": 0.5, "good_wait_rate": 1,
                            "ura_hit_rate": 0, "balance": 1000},
                "recent_large": [], "recent_losses": [],
                "tablemates": [{"nickname": "乙", "games": 2,
                                "win_rate": 0.5, "self_avg_pt": 10,
                                "opponent_avg_pt": 8}]}

    async def game(self, game_uuid):
        return {"uuid": game_uuid, "players": [], "kyokus": []}

    async def related_games(self, by, target_id):
        return {"total": 0, "items": []}


def test_bot_commands_use_site_api():
    service = CommandService(_FakeApi())
    assert "最新牌谱" in asyncio.run(service.handle("/最新牌谱 1"))
    assert "红中会" in asyncio.run(service.handle("/队伍排名"))
    assert "选手数据" in asyncio.run(service.handle("/选手数据 甲"))
    assert "对局详情" in asyncio.run(service.handle("/对局 game-1"))
    assert "暂无相关对局" in asyncio.run(service.handle("/选手对局 甲"))
    assert "暂无相关对局" in asyncio.run(service.handle("/队伍对局 红中会"))
    assert "队伍数据" in asyncio.run(service.handle("/队伍数据 红中会"))
    assert "立直次数：2" in asyncio.run(service.handle("/立直数据 甲"))
    assert "最近和铳" in asyncio.run(service.handle("/最近和铳 甲"))
    assert "最常同桌" in asyncio.run(service.handle("/最常同桌 甲"))
