"""鉴权流程测试：登录/登出/me/cookie/Bearer 兼容/错误凭证。"""
import time

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app.db import get_db
from app.main import create_app


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(config, "_admin_token_cache", "tok123")
    monkeypatch.setattr(config, "_admin_username_cache", "admin")
    monkeypatch.setattr(config, "_admin_password_cache", "pass456")
    monkeypatch.setattr(config, "_session_secret_cache", "ssh-secret")
    app = create_app()
    app.dependency_overrides[get_db] = lambda: (yield db)
    with TestClient(app) as c:
        yield c


def test_unauthorized_without_credential(client):
    r = client.get("/api/admin/sync-runs")
    assert r.status_code == 401


def test_login_sets_cookie_and_me_works(client):
    r = client.post("/api/admin/login",
                    json={"username": "admin", "password": "pass456"})
    assert r.status_code == 200
    assert r.json()["username"] == "admin"
    assert "mla_session" in r.cookies

    me = client.get("/api/admin/me")
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


def test_cookie_session_grants_admin_access(client):
    client.post("/api/admin/login",
                json={"username": "admin", "password": "pass456"})
    r = client.get("/api/admin/sync-runs")
    assert r.status_code == 200


def test_wrong_password_rejected(client):
    r = client.post("/api/admin/login",
                    json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401
    assert "mla_session" not in r.cookies


def test_wrong_username_rejected(client):
    r = client.post("/api/admin/login",
                    json={"username": "root", "password": "pass456"})
    assert r.status_code == 401


def test_logout_clears_session(client):
    client.post("/api/admin/login",
                json={"username": "admin", "password": "pass456"})
    assert client.get("/api/admin/me").status_code == 200
    client.post("/api/admin/logout")
    assert client.get("/api/admin/me").status_code == 401


def test_bearer_token_still_supported(client):
    """Bearer 通道用于 API/CI 调用，必须仍然可用。"""
    r = client.get("/api/admin/sync-runs",
                   headers={"Authorization": "Bearer tok123"})
    assert r.status_code == 200


def test_invalid_bearer_rejected(client):
    r = client.get("/api/admin/sync-runs",
                   headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_tampered_cookie_rejected(client, monkeypatch):
    """伪造 cookie 必须被拒。"""
    client.post("/api/admin/login",
                json={"username": "admin", "password": "pass456"})
    raw = client.cookies.get("mla_session", "")
    assert raw
    # 篡改最后一段（签名）
    parts = raw.split(":")
    parts[-1] = "0" * len(parts[-1])
    tampered = ":".join(parts)
    r = client.get("/api/admin/me",
                   headers={"Cookie": f"mla_session={tampered}"})
    assert r.status_code == 401


def test_expired_cookie_rejected(client, monkeypatch):
    """过期 cookie 必须失效。"""
    # 把时间往前推 8 天（默认 7 天有效期）
    import app.api.auth as auth

    real_sign = auth._sign

    def _expired_sign(user, exp):
        return real_sign(user, int(time.time()) - 8 * 24 * 3600)

    monkeypatch.setattr(auth, "_sign", _expired_sign)
    client.post("/api/admin/login",
                json={"username": "admin", "password": "pass456"})
    assert client.get("/api/admin/me").status_code == 401
