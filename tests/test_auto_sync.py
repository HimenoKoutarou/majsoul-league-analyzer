"""赛事场自动检测（定时轮询）单元测试。"""
import pytest

import app.config as config
from app.services.majsoul import auto_sync


class _League:
    def __init__(self, contest_id):
        self.contest_id = contest_id


class _FakeQuery:
    def __init__(self, contest_id):
        self._contest_id = contest_id

    def first(self):
        return _League(self._contest_id) if self._contest_id is not None else None


class _FakeSession:
    def __init__(self, contest_id):
        self._contest_id = contest_id

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def query(self, _model):
        return _FakeQuery(self._contest_id)


@pytest.fixture(autouse=True)
def reset_state():
    auto_sync._state.update({
        "enabled": False, "schedule": "", "running": False,
        "last_run_at": None, "next_run_at": None, "last_added": 0,
        "last_status": None, "last_error": None,
    })
    yield


def test_start_disabled_returns_false(monkeypatch):
    monkeypatch.setattr(config, "AUTO_SYNC_ENABLED", False)
    assert auto_sync.start_auto_sync(lambda: None) is False
    assert auto_sync._state["enabled"] is False


def test_tick_skip_without_contest(monkeypatch):
    monkeypatch.setattr(config, "DHS_USERNAME", "u")
    monkeypatch.setattr(config, "DHS_PASSWORD", "p")
    r = auto_sync._tick(lambda: _FakeSession(None))
    assert r is None
    assert auto_sync._state["last_status"] == "skipped"
    assert "赛事场" in auto_sync._state["last_error"]


def test_tick_skip_without_credentials(monkeypatch):
    monkeypatch.setattr(config, "DHS_USERNAME", "")
    monkeypatch.setattr(config, "DHS_PASSWORD", "")
    r = auto_sync._tick(lambda: _FakeSession(123))
    assert r is None
    assert auto_sync._state["last_status"] == "skipped"
    assert "DHS_USERNAME" in auto_sync._state["last_error"]


def test_tick_starts_sync_with_callback(monkeypatch):
    monkeypatch.setattr(config, "DHS_USERNAME", "u")
    monkeypatch.setattr(config, "DHS_PASSWORD", "p")
    captured = {}

    def fake_start(session_factory, contest_id, username, password,
                   triggered_by="manual", callback=None):
        captured["contest_id"] = contest_id
        captured["triggered_by"] = triggered_by
        captured["callback"] = callback
        return True

    monkeypatch.setattr("app.services.majsoul.sync.start_sync_thread", fake_start)
    r = auto_sync._tick(lambda: _FakeSession(123))
    assert r is True
    assert captured["contest_id"] == 123
    assert captured["triggered_by"] == "auto"
    assert auto_sync._state["running"] is True

    # 同步完成回调更新状态
    captured["callback"]({"games_added": 3, "status": "success", "errors": []})
    assert auto_sync._state["running"] is False
    assert auto_sync._state["last_added"] == 3
    assert auto_sync._state["last_status"] == "success"
    assert auto_sync._state["last_error"] is None


def test_tick_skip_when_already_running(monkeypatch):
    monkeypatch.setattr(config, "DHS_USERNAME", "u")
    monkeypatch.setattr(config, "DHS_PASSWORD", "p")
    auto_sync._state["running"] = True
    r = auto_sync._tick(lambda: _FakeSession(123))
    assert r is None


def test_next_run_at_same_day(monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(config, "AUTO_SYNC_TIME", "22:00")
    nxt = auto_sync._next_run_after(datetime(2026, 9, 16, 10, 0, 0))
    assert nxt.hour == 22 and nxt.minute == 0
    assert nxt.day == 16


def test_next_run_at_rollover_next_day(monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(config, "AUTO_SYNC_TIME", "22:00")
    nxt = auto_sync._next_run_after(datetime(2026, 9, 16, 23, 0, 0))
    assert nxt.hour == 22 and nxt.minute == 0
    assert nxt.day == 17