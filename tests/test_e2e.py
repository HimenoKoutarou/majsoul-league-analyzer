"""端到端：样本牌谱 → 公开 API 全链路（复用 client fixture 模式）。"""
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
    app.dependency_overrides[get_db] = lambda: (yield db)
    with TestClient(app) as c:
        yield c


def test_full_flow(client, db):
    # 0. 联赛单行（首页/积分需要）
    db.add(League(name="测试联赛"))
    # 1. 灌入两遍（验证幂等）
    ingest_tenhou_game(db, SAMPLE)
    ingest_tenhou_game(db, SAMPLE)
    # 2. 建队并分组
    db.add(Team(id=1, name="东军", color="#f00"))
    db.add(Team(id=2, name="西军", color="#00f"))
    db.flush()
    for i, team_id in enumerate([1, 2, 1, 2]):
        db.query(Player).filter(Player.nickname == SAMPLE["name"][i]).update({"team_id": team_id})
    db.commit()

    # 3. 各端点串起来
    league = client.get("/api/league").json()
    assert league["game_count"] == 1

    standings = client.get("/api/standings?by=team").json()
    assert len(standings["rows"]) == 2
    assert standings["rows"][0]["points"] == 135  # 西军 = 90 + 45

    games = client.get("/api/games").json()
    detail = client.get(f"/api/games/{games['items'][0]['uuid']}").json()
    assert len(detail["kyokus"]) == 9

    stats = client.get("/api/stats?by=player").json()
    assert len(stats["rows"]) == 4
    trend = client.get("/api/stats/trend?by=team").json()
    assert len(trend["rows"]) == 2

    yaku = client.get("/api/stats/yaku?by=team").json()
    assert sum(yaku.values()) >= 2  # 样本至少2个役条目