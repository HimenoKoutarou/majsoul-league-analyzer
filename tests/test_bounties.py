import pytest
from fastapi.testclient import TestClient

import app.config as config
from app.db import get_db
from app.main import create_app
from app.models import Bounty, BountyClaim


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(config, "_admin_token_cache", "test-token")
    monkeypatch.setattr(config, "_admin_username_cache", "admin")
    monkeypatch.setattr(config, "_admin_password_cache", "test-pass")
    monkeypatch.setattr(config, "_session_secret_cache", "test-secret")
    app = create_app()
    app.dependency_overrides[get_db] = lambda: (yield db)
    with TestClient(app) as c:
        yield c


def _auth(client):
    return {"Authorization": "Bearer test-token"}


def _create_bounty(client, target_date="2099-01-01"):
    return client.post("/api/bounties", json={
        "title": "首个满贯", "description": "打出满贯",
        "reward": "一杯奶茶", "target_date": target_date,
        "nickname": "发起人", "account_id": 123,
    })


def test_bounty_submit_pending_and_approve(client, db):
    r = _create_bounty(client)
    assert r.status_code == 200
    bid = r.json()["id"]
    assert db.get(Bounty, bid).status == "pending"
    # 未审核不公开
    assert client.get("/api/bounties").json() == []

    # 管理端可见
    rows = client.get("/api/admin/bounties", headers=_auth(client)).json()
    assert any(b["id"] == bid and b["status"] == "pending" for b in rows)

    # 审核通过后公开
    r = client.put(f"/api/admin/bounties/{bid}", json={"status": "approved"},
                   headers=_auth(client))
    assert r.status_code == 200
    pub = client.get("/api/bounties").json()
    assert any(b["id"] == bid and b["claimed_count"] == 0 and
               b["submitted_by"] == "发起人" for b in pub)


def test_bounty_reject_not_public(client, db):
    r = _create_bounty(client)
    bid = r.json()["id"]
    client.put(f"/api/admin/bounties/{bid}", json={"status": "rejected"},
               headers=_auth(client))
    assert db.get(Bounty, bid).status == "rejected"
    assert client.get("/api/bounties").json() == []


def test_bounty_create_validation(client, db):
    r = client.post("/api/bounties", json={"title": "", "description": "x",
                                           "target_date": "2099-01-01"})
    assert r.status_code == 422
    r = client.post("/api/bounties", json={"title": "t", "description": "x",
                                           "target_date": "not-a-date"})
    assert r.status_code == 422


def test_update_max_claims(client, db):
    r = _create_bounty(client)
    bid = r.json()["id"]
    r = client.put(f"/api/admin/bounties/{bid}", json={"max_claims": 5},
                   headers=_auth(client))
    assert r.status_code == 200
    assert db.get(Bounty, bid).max_claims == 5
    # 非法值不改
    client.put(f"/api/admin/bounties/{bid}", json={"max_claims": 0},
               headers=_auth(client))
    assert db.get(Bounty, bid).max_claims == 5


def test_claim_flow_and_close(client, db):
    r = _create_bounty(client, target_date="2000-01-01")
    bid = r.json()["id"]
    client.put(f"/api/admin/bounties/{bid}", json={"status": "approved", "max_claims": 1},
               headers=_auth(client))

    # 目标日期前不可提交
    r2 = _create_bounty(client, target_date="2999-01-01")
    bid2 = r2.json()["id"]
    client.put(f"/api/admin/bounties/{bid2}", json={"status": "approved"},
               headers=_auth(client))
    r = client.post(f"/api/bounties/{bid2}/claims", json={"share_url": "http://x/1"})
    assert r.status_code == 422

    # 到期后可提交
    r = client.post(f"/api/bounties/{bid}/claims",
                    json={"share_url": "http://x/2", "nickname": "达成者", "note": "n"})
    assert r.status_code == 200
    cid = r.json()["id"]
    assert db.get(BountyClaim, cid).status == "pending"

    # 管理端可见并审核通过
    claims = client.get("/api/admin/claims", headers=_auth(client)).json()
    assert any(c["id"] == cid and c["status"] == "pending" for c in claims)
    r = client.put(f"/api/admin/claims/{cid}", json={"status": "approved"},
                   headers=_auth(client))
    assert r.status_code == 200
    assert db.get(BountyClaim, cid).status == "approved"
    # 达到上限自动关闭
    assert db.get(Bounty, bid).status == "closed"

    # 满额关闭后不能再提交
    r = client.post(f"/api/bounties/{bid}/claims", json={"share_url": "http://x/3"})
    assert r.status_code == 404


def test_claim_reject(client, db):
    r = _create_bounty(client, target_date="2000-01-01")
    bid = r.json()["id"]
    client.put(f"/api/admin/bounties/{bid}", json={"status": "approved"},
               headers=_auth(client))
    r = client.post(f"/api/bounties/{bid}/claims", json={"share_url": "http://x/4"})
    cid = r.json()["id"]
    client.put(f"/api/admin/claims/{cid}", json={"status": "rejected"},
               headers=_auth(client))
    assert db.get(BountyClaim, cid).status == "rejected"
    assert db.get(Bounty, bid).status == "approved"  # 驳回不关闭