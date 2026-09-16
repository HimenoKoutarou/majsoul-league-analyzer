import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app.db import get_db
from app.main import create_app
from app.models import Game, League, Player, Team

SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "sample_paipu.json").read_text(encoding="utf-8"))


@pytest.fixture
def client(db, monkeypatch):
    _ = db  # league 行由测试显式创建
    monkeypatch.setattr(config, "_admin_token_cache", "test-token")
    app = create_app()
    app.dependency_overrides[get_db] = lambda: (yield db)
    with TestClient(app) as c:
        yield c


def _auth(client):
    return {"Authorization": "Bearer test-token"}


def _seed_league(db):
    db.add(League(name="测试联赛"))
    db.commit()


def test_admin_requires_token(client):
    assert client.get("/api/admin/sync-runs").status_code == 401


def test_admin_league_update(client, db):
    _seed_league(db)
    r = client.put("/api/admin/league", json={"name": "2026夏季赛", "description": "desc"},
                   headers=_auth(client))
    assert r.status_code == 200
    assert db.query(League).first().name == "2026夏季赛"
    assert db.query(League).first().description == "desc"


def test_admin_score_rule(client, db):
    _seed_league(db)
    r = client.put("/api/admin/score-rule",
                   json={"rank_points": [100, 50, -10, -100], "allow_negative": True,
                         "tiebreak": "raw_points"}, headers=_auth(client))
    assert r.status_code == 200
    assert db.query(League).first().score_rule["rank_points"] == [100, 50, -10, -100]
    r = client.put("/api/admin/score-rule", json={"rank_points": [1, 2]}, headers=_auth(client))
    assert r.status_code == 422


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


def test_admin_ingest_with_fake_ninklang(client, db, monkeypatch):
    from app.services.paipu.ingest import ingest_tenhou_game  # noqa: F401 (确保符号)

    monkeypatch.setattr("app.api.admin.fetch_tenhou", lambda url: SAMPLE)
    r = client.post("/api/admin/games/ingest",
                    json={"share_url": "https://game.maj-soul.com/1/?paipu=xxx_1"},
                    headers=_auth(client))
    assert r.status_code == 200
    assert r.json()["uuid"] == SAMPLE["ref"]
    assert db.query(Game).count() == 1