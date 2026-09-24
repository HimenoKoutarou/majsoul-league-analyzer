import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app.db import get_db
from app.main import create_app
from app.models import Game, GamePlayer, Kyoku, League, Player, ScheduleDay, Team

SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "sample_paipu.json").read_text(encoding="utf-8"))


@pytest.fixture
def client(db, monkeypatch):
    _ = db  # league 行由测试显式创建
    monkeypatch.setattr(config, "_admin_token_cache", "test-token")
    monkeypatch.setattr(config, "_admin_username_cache", "admin")
    monkeypatch.setattr(config, "_admin_password_cache", "test-pass")
    monkeypatch.setattr(config, "_session_secret_cache", "test-secret")
    app = create_app()
    app.dependency_overrides[get_db] = lambda: (yield db)
    with TestClient(app) as c:
        yield c


def _login(client, username="admin", password="test-pass"):
    """以 cookie 形式登录，便于覆盖 cookie 鉴权路径。"""
    r = client.post("/api/admin/login",
                    json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r


def _auth(client):
    return {"Authorization": "Bearer test-token"}


def _seed_league(db):
    db.add(League(name="测试联赛"))
    db.commit()


def test_admin_requires_token(client):
    assert client.get("/api/admin/sync-runs").status_code == 401


def test_admin_league_update(client, db):
    _seed_league(db)
    r = client.put("/api/admin/league", json={
        "name": "2026夏季赛", "organizer": "测试主办方", "season": "夏季赛",
        "description": "desc", "start_date": "2026-09-01", "end_date": "2026-10-31",
        "contact": "test@example.com",
    },
                   headers=_auth(client))
    assert r.status_code == 200
    league = db.query(League).first()
    assert league.name == "2026夏季赛"
    assert league.organizer == "测试主办方"
    assert league.season == "夏季赛"
    assert league.description == "desc"
    assert league.start_date.isoformat() == "2026-09-01"
    assert league.end_date.isoformat() == "2026-10-31"
    assert league.contact == "test@example.com"
    assert client.get("/api/league").json()["organizer"] == "测试主办方"


def test_admin_league_rejects_invalid_date(client, db):
    _seed_league(db)
    r = client.put("/api/admin/league", json={"start_date": "not-a-date"},
                   headers=_auth(client))
    assert r.status_code == 422


def test_sync_credentials_are_persistent_and_private(client, db):
    _seed_league(db)
    saved = client.put("/api/admin/sync-credentials", json={
        "username": "contest-admin", "password": "secret-pass"
    }, headers=_auth(client))
    assert saved.status_code == 200

    loaded = client.get("/api/admin/sync-credentials", headers=_auth(client))
    assert loaded.json() == {
        "username": "contest-admin", "password": "secret-pass", "persistent": True
    }
    public = client.get("/api/league").json()
    assert "sync_username" not in public
    assert "sync_password" not in public


def test_lobby_credentials_and_live_sync_require_credentials(client, db, monkeypatch):
    _seed_league(db)

    rejected = client.put("/api/admin/sync/live-status", json={"enabled": True},
                          headers=_auth(client))
    assert rejected.status_code == 422
    assert db.query(League).first().live_sync_enabled is None

    saved = client.put("/api/admin/lobby-credentials", json={
        "username": "ordinary-user", "password": "ordinary-pass"
    }, headers=_auth(client))
    assert saved.status_code == 200
    loaded = client.get("/api/admin/lobby-credentials", headers=_auth(client))
    assert loaded.json()["username"] == "ordinary-user"
    assert loaded.json()["password"] == "ordinary-pass"

    monkeypatch.setattr("app.services.majsoul.live_sync.start_live_sync",
                        lambda *args, **kwargs: True)
    started = client.put("/api/admin/sync/live-status",
                         json={"enabled": True, "interval": 30},
                         headers=_auth(client))
    assert started.status_code == 200
    assert started.json()["enabled"] is True
    assert db.query(League).first().live_sync_interval == 30

    monkeypatch.setattr("app.services.majsoul.live_sync.stop_live_sync",
                        lambda: True)
    stopped = client.put("/api/admin/sync/live-status", json={"enabled": False},
                         headers=_auth(client))
    assert stopped.status_code == 200
    assert stopped.json()["enabled"] is False


def test_admin_score_rule_is_read_only(client, db):
    _seed_league(db)
    r = client.put("/api/admin/score-rule",
                   json={"rank_points": [100, 50, -10, -100], "allow_negative": True,
                         "tiebreak": "raw_points"}, headers=_auth(client))
    assert r.status_code == 404
    assert db.query(League).first().score_rule["rank_points"] == [90, 45, 0, -45]


def test_admin_teams_players_crud(client, db):
    _seed_league(db)
    r = client.post("/api/admin/teams", json={"name": "红中会", "short_name": "红",
                                              "color": "#e5484d"}, headers=_auth(client))
    assert r.status_code == 200
    team_id = r.json()["id"]

    r = client.post("/api/admin/players",
                    json={"nickname": "测试雀士", "team_id": team_id, "account_id": 99},
                    headers=_auth(client))
    assert r.status_code == 200
    player_id = r.json()["id"]

    r = client.put(f"/api/admin/players/{player_id}", json={"team_id": None},
                   headers=_auth(client))
    assert r.status_code == 200
    assert db.get(Player, player_id).team_id is None

    r = client.delete(f"/api/admin/teams/{team_id}", headers=_auth(client))
    assert r.status_code == 200
    assert db.query(Team).count() == 0


def test_admin_can_move_player_between_teams(client, db):
    _seed_league(db)
    first = Team(name="一队", team_number=1)
    second = Team(name="二队", team_number=2)
    db.add_all([first, second])
    db.flush()
    player = Player(nickname="可转队选手", account_id=101, team_id=first.id)
    db.add(player)
    db.commit()

    moved = client.put(f"/api/admin/players/{player.id}",
                       json={"team_id": second.id}, headers=_auth(client))
    assert moved.status_code == 200
    assert moved.json()["team_id"] == second.id
    assert db.get(Player, player.id).team_id == second.id

    invalid = client.put(f"/api/admin/players/{player.id}",
                         json={"team_id": 9999}, headers=_auth(client))
    assert invalid.status_code == 422


def test_admin_can_delete_game_and_related_rows(client, db):
    _seed_league(db)
    player = Player(nickname="牌谱选手", account_id=102)
    db.add(player)
    db.add(Game(uuid="deletable-game"))
    db.flush()
    db.add(GamePlayer(game_uuid="deletable-game", seat=0, player_id=player.id,
                      nickname=player.nickname, rank=1, final_score=25000))
    db.add(Kyoku(game_uuid="deletable-game", index=0, round_data=[0, 0, 0],
                 data=[], summary={}))
    db.commit()

    deleted = client.delete("/api/admin/games/deletable-game", headers=_auth(client))
    assert deleted.status_code == 200
    assert db.get(Game, "deletable-game") is None
    assert db.query(GamePlayer).filter_by(game_uuid="deletable-game").count() == 0
    assert db.query(Kyoku).filter_by(game_uuid="deletable-game").count() == 0
    assert db.get(Player, player.id) is not None

    missing = client.delete("/api/admin/games/deletable-game", headers=_auth(client))
    assert missing.status_code == 404


def test_admin_team_numbers_are_unique_and_exposed(client, db):
    _seed_league(db)
    first = client.post("/api/admin/teams", json={"name": "一队"},
                        headers=_auth(client))
    assert first.status_code == 200
    assert first.json()["team_number"] == 1

    second = client.post("/api/admin/teams", json={"name": "二队", "team_number": 2},
                         headers=_auth(client))
    assert second.status_code == 200

    duplicate = client.post("/api/admin/teams",
                            json={"name": "重复编号", "team_number": 2},
                            headers=_auth(client))
    assert duplicate.status_code == 422
    assert {row["team_number"] for row in client.get("/api/teams").json()} == {1, 2}


def test_admin_schedule_can_be_saved_and_validated(client, db):
    _seed_league(db)
    for number in range(1, 6):
        db.add(Team(name=f"队伍{number}", team_number=number))
    db.commit()
    body = {"rows": [
        {"date": "2026-10-01", "team_numbers": [1, 2, 3, 4], "note": "开幕日"},
        {"date": "2026-10-02", "team_numbers": [2, 3, 4, 5], "note": ""},
    ]}
    saved = client.put("/api/admin/schedule", json=body, headers=_auth(client))
    assert saved.status_code == 200
    assert saved.json()["count"] == 2
    assert db.query(ScheduleDay).count() == 2

    listed = client.get("/api/admin/schedule", headers=_auth(client))
    assert listed.status_code == 200
    rows = listed.json()
    assert [row["date"] for row in rows] == ["2026-10-01", "2026-10-02"]
    assert rows[0]["team_numbers"] == [1, 2, 3, 4]
    assert [team["team_number"] for team in rows[0]["bye_teams"]] == [5]
    assert rows[1]["team_numbers"] == [2, 3, 4, 5]
    assert [team["team_number"] for team in rows[1]["bye_teams"]] == [1]

    invalid = client.put(
        "/api/admin/schedule",
        json={"rows": [{"date": "2026-10-03", "team_numbers": [1, 1, 2, 3]}]},
        headers=_auth(client),
    )
    assert invalid.status_code == 422

    appended = client.post(
        "/api/admin/schedule/append",
        json={"rows": [{"date": "2026-10-03", "team_numbers": [1, 2, 3, 5]}]},
        headers=_auth(client),
    )
    assert appended.status_code == 200
    assert appended.json()["added"] == 1
    duplicate = client.post(
        "/api/admin/schedule/append",
        json={"rows": [{"date": "2026-10-03", "team_numbers": [1, 2, 3, 5]}]},
        headers=_auth(client),
    )
    assert duplicate.status_code == 409


def test_admin_schedule_rejects_unknown_team_number(client, db):
    _seed_league(db)
    for number in range(1, 4):
        db.add(Team(name=f"队伍{number}", team_number=number))
    db.commit()
    r = client.put(
        "/api/admin/schedule",
        json={"rows": [{"date": "2026-10-03", "team_numbers": [1, 2, 3, 4]}]},
        headers=_auth(client),
    )
    assert r.status_code == 422


def test_admin_cannot_delete_player_with_history(client, db):
    _seed_league(db)
    db.add(Player(id=1, nickname="历史选手", account_id=1001))
    db.add(Game(uuid="game-1"))
    db.add(GamePlayer(game_uuid="game-1", seat=0, player_id=1,
                      nickname="历史选手", rank=1, final_score=25000))
    db.commit()
    r = client.delete("/api/admin/players/1", headers=_auth(client))
    assert r.status_code == 409
    assert db.get(Player, 1) is not None


def test_create_player_requires_account_id(client, db):
    _seed_league(db)
    r = client.post("/api/admin/players",
                    json={"nickname": "无名雀士", "team_id": None},
                    headers=_auth(client))
    assert r.status_code == 422
    assert db.query(Player).count() == 0


def test_list_players(client, db):
    _seed_league(db)
    client.post("/api/admin/players",
                json={"nickname": "张三", "team_id": None, "account_id": 111},
                headers=_auth(client))
    r = client.get("/api/admin/players", headers=_auth(client))
    assert r.status_code == 200
    rows = r.json()
    assert any(p["nickname"] == "张三" and p["account_id"] == 111 for p in rows)


def test_admin_ingest_with_fake_ninklang(client, db, monkeypatch):
    from app.services.paipu.ingest import ingest_tenhou_game  # noqa: F401 (确保符号)

    monkeypatch.setattr("app.api.admin.fetch_tenhou", lambda url: SAMPLE)
    r = client.post("/api/admin/games/ingest",
                    json={"share_url": "https://game.maj-soul.com/1/?paipu=xxx_1"},
                    headers=_auth(client))
    assert r.status_code == 200
    assert r.json()["uuid"] == SAMPLE["ref"]
    assert db.query(Game).count() == 1
