import httpx
import pytest

from app.services import ninklang


def test_normalize_share_url():
    assert ninklang.normalize_share_url(
        "260915-9cb4dafb-2a77-483e-ad86-1bdabc28f3cf_a29440659"
    ) == "https://game.maj-soul.com/1/?paipu=260915-9cb4dafb-2a77-483e-ad86-1bdabc28f3cf_a29440659"
    url = "https://game.maj-soul.com/1/?paipu=xxx_1"
    assert ninklang.normalize_share_url(url) == url
    with pytest.raises(ValueError):
        ninklang.normalize_share_url("not-a-link")


def test_fetch_tenhou_success(monkeypatch):
    state = {"polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/desktop/requests" and request.method == "POST":
            return httpx.Response(202, json={
                "request_id": "r1", "request_token": "rq_" + "x" * 30,
                "status": "queued", "poll_after_ms": 1})
        if request.url.path == "/api/v1/requests/r1" and request.method == "GET":
            state["polls"] += 1
            status = "fetching" if state["polls"] == 1 else "ready"
            return httpx.Response(200, json={"status": status, "poll_after_ms": 1})
        if request.url.path == "/api/v1/requests/r1/result":
            return httpx.Response(200, json={
                "ver": "2.3", "ref": "260915-x", "name": ["a", "b", "c", "d"],
                "log": [[[0, 0, 0]]], "sc": [], "title": ["x", "2026-09-15 15:44:09"]})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(ninklang, "_client",
                        lambda: httpx.Client(transport=transport, base_url="https://ninklang.tech"))

    data = ninklang.fetch_tenhou("https://game.maj-soul.com/1/?paipu=xxx_1")
    assert data["ref"] == "260915-x"
    assert state["polls"] == 2


def test_fetch_tenhou_failed(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={
                "request_id": "r2", "request_token": "rq_" + "x" * 30,
                "status": "queued", "poll_after_ms": 1})
        return httpx.Response(200, json={
            "status": "failed",
            "error": {"code": "RECORD_UNAVAILABLE", "message": "not found"}})

    monkeypatch.setattr(ninklang, "_client", lambda: httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://ninklang.tech"))
    with pytest.raises(ninklang.NinklangError, match="RECORD_UNAVAILABLE"):
        ninklang.fetch_tenhou("https://game.maj-soul.com/1/?paipu=yyy_2")