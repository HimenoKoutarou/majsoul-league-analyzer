import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import create_app
from app.models import League, Player, Team
from app.services.paipu.ingest import ingest_tenhou_game

SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "sample_paipu.json").read_text(encoding="utf-8"))


@pytest.fixture
def client(db):
    app = create_app()

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c


def test_public_endpoints(client, db):
    db.add(League(name="测试联赛"))
    ingest_tenhou_game(db, SAMPLE)
    db.add(Team(id=1, name="A队", color="#f00"))
    db.flush()
    db.query(Player).filter(Player.nickname == SAMPLE["name"][3]).update({"team_id": 1})
    db.commit()

    r = client.get("/api/league")
    assert r.status_code == 200
    assert r.json()["game_count"] == 1

    r = client.get("/api/teams")
    assert r.status_code == 200
    assert any(t["name"] == "A队" for t in r.json())

    r = client.get("/api/standings?by=team")
    assert r.status_code == 200
    assert any(row["name"] == "A队" for row in r.json()["rows"])
    assert any(row["name"] == "未分组" for row in r.json()["rows"])

    r = client.get("/api/games")
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert len(item["players"]) == 4
    assert item["players"][3]["rank"] == 1
    assert item["players"][3]["team_name"] == "A队"

    uuid = item["uuid"]
    r = client.get(f"/api/games/{uuid}")
    detail = r.json()
    assert len(detail["kyokus"]) == 9
    assert detail["kyokus"][2]["summary"]["end"] == "agari"
    assert detail["kyokus"][2]["summary"]["agari"][0]["score"] == 2000

    r = client.get("/api/stats?by=player")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 4
    top = next(row for row in rows if row["nickname"] == SAMPLE["name"][3])

    pid = top["player_id"]
    r = client.get(f"/api/stats?by=player&id={pid}")
    assert r.json()["win"] == 2

    r = client.get(f"/api/stats/games?by=player&id={pid}")
    related = r.json()
    assert related["total"] == 1
    assert len(related["items"][0]["players"]) == 4
    assert any(player["player_id"] == pid for player in related["items"][0]["players"])

    r = client.get("/api/stats/games?by=team&id=1")
    related = r.json()
    assert related["total"] == 1
    assert all(player["team_id"] == 1 for player in related["items"][0]["players"]
               if player["nickname"] == SAMPLE["name"][3])

    r = client.get("/api/stats/yaku?by=player")
    assert "役牌 白" in r.json()

    r = client.get("/api/stats/trend?by=player")
    trend = r.json()["rows"]
    assert len(trend) == 4
    one = [t for t in trend if t["player_id"] == pid][0]
    assert one["points"] == [90]

    # 未带 token 访问管理端点应 401（admin 路由在 T8 注册，此处先允许 404）
    r = client.get("/api/admin/teams")
    assert r.status_code in (401, 404, 405)
