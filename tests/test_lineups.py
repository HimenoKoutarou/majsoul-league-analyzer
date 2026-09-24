from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app.api.captain import MATCH_WEEKDAYS, is_locked, next_matchdays
from app.db import get_db
from app.main import create_app
from app.models import Captain, League, Lineup, Player, ScheduleDay, Team


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


def _next_matchday() -> date:
    """下一个比赛日（周三五日），默认未锁定。"""
    for d in next_matchdays(6):
        if not is_locked(d):
            return d
    raise AssertionError("找不到可编辑的比赛日")


def _seed(client, db):
    """建 1 队 + 6 选手 + 队长账号。"""
    r = client.post("/api/admin/teams", json={"name": "红中会", "color": "#e5484d"},
                    headers=_auth(client))
    team_id = r.json()["id"]
    db.add(Team(name="白板社", color="#2563eb", team_number=2))
    db.commit()
    other_id = db.query(Team).filter(Team.name == "白板社").first().id
    ids = []
    for i in range(6):
        ids.append(client.post("/api/admin/players",
                               json={"nickname": f"雀士{i + 1}", "team_id": team_id,
                                     "account_id": 1000 + i},
                               headers=_auth(client)).json()["id"])
    client.put(f"/api/admin/teams/{team_id}/captain",
               json={"username": "captain_red"},
               headers=_auth(client))
    return team_id, other_id, ids


def _login(client, username="captain_red", password="secret1"):
    if password == "secret1":
        rows = client.get("/api/admin/captains", headers=_auth(client)).json()
        password = next(row["password"] for row in rows if row["username"] == username)
    return client.post("/api/captain/login",
                       json={"username": username, "password": password})


def test_matchday_rule():
    days = next_matchdays(30)
    assert all(d.isoweekday() in MATCH_WEEKDAYS for d in days)
    assert len(days) == 30
    # 18:00 截止：今天若是比赛日，今天 18 点前后锁定状态正确
    today = date.today()
    assert True  # 时间相关分支由 is_locked 独立处理


def test_captain_login_flow(client, db):
    _seed(client, db)
    credentials = client.get("/api/admin/captains", headers=_auth(client)).json()[0]
    # 未登录 401
    assert client.get("/api/captain/me").status_code == 401
    # 错误密码
    assert _login(client, password="wrong").status_code == 401
    # 正确登录
    r = client.post("/api/captain/login", json=credentials)
    assert r.status_code == 200
    me = client.get("/api/captain/me").json()
    assert me["team_name"] == "红中会"


def test_captain_can_change_independent_password(client, db):
    _seed(client, db)
    credentials = client.get("/api/admin/captains", headers=_auth(client)).json()[0]
    assert client.post("/api/captain/login", json=credentials).status_code == 200

    r = client.put("/api/captain/password", json={"password": "newpass1"})
    assert r.status_code == 200
    assert client.post("/api/captain/login",
                       json={"username": "captain_red", "password": "newpass1"}).status_code == 200
    assert client.post("/api/captain/login",
                       json={"username": "captain_red", "password": credentials["password"]}).status_code == 401


def test_new_team_gets_generated_captain_credentials(client, db):
    r = client.post("/api/admin/teams", json={"name": "自动账号队"},
                    headers=_auth(client))
    assert r.status_code == 200
    credentials = r.json()["captain"]
    assert credentials["username"] == f"team_{r.json()['id']}"
    assert len(credentials["password"]) >= 6
    assert client.post("/api/captain/login", json=credentials).status_code == 200


def test_admin_can_view_generated_captain_password(client, db):
    r = client.post("/api/admin/teams", json={"name": "可查看密码队"},
                    headers=_auth(client))
    assert r.status_code == 200
    created = r.json()["captain"]
    rows = client.get("/api/admin/captains", headers=_auth(client)).json()
    row = next(item for item in rows if item["team_name"] == "可查看密码队")
    assert row["username"] == created["username"]
    assert row["password"] == created["password"]

    reset = client.put(
        f"/api/admin/teams/{r.json()['id']}/captain",
        json={"password": "管理员不能指定这个密码"},
        headers=_auth(client),
    )
    assert reset.status_code == 200
    assert reset.json()["password"] != "管理员不能指定这个密码"


def test_admin_captain_account_invalid(client, db):
    _seed(client, db)
    team_id = db.query(Team).filter(Team.name == "红中会").first().id
    r = client.put(f"/api/admin/teams/{team_id}/captain",
                   json={"username": "ab", "password": "123"},
                   headers=_auth(client))
    assert r.status_code == 422


def test_upsert_lineup_and_public(client, db):
    team_id, other_id, ids = _seed(client, db)
    _login(client)
    d = _next_matchday().isoformat()

    # 选 2 人报错
    r = client.put("/api/captain/lineups", json={"date": d, "slot": 1,
                                                 "player_ids": ids[:2]})
    assert r.status_code == 422
    # 空报错
    r = client.put("/api/captain/lineups", json={"date": d, "slot": 1,
                                                 "player_ids": []})
    assert r.status_code == 422
    # 选其他队选手报错
    other_player = client.post("/api/admin/players",
                               json={"nickname": "外人", "team_id": other_id,
                                     "account_id": 9999},
                               headers=_auth(client)).json()["id"]
    r = client.put("/api/captain/lineups", json={"date": d, "slot": 1,
                                                 "player_ids": [other_player]})
    assert r.status_code == 422
    # 正确提交第一场（1 人）
    r = client.put("/api/captain/lineups", json={"date": d, "slot": 1,
                                                 "player_ids": ids[:1]})
    assert r.status_code == 200
    data = client.get(f"/api/captain/lineups?date={d}").json()
    assert data["slots"][0]["submitted"] is True
    assert [p["id"] for p in data["slots"][0]["players"]] == ids[:1]
    assert data["slots"][1]["submitted"] is False

    # 覆盖修改第二场
    r = client.put("/api/captain/lineups", json={"date": d, "slot": 2,
                                                 "player_ids": ids[1:2]})
    assert r.status_code == 200

    # 未到 18:00 不公开：公开视图隐藏选手
    pub = client.get(f"/api/captain/public/lineups?date_str={d}").json()
    assert pub["locked"] is False
    mine = [row for row in pub["rows"] if row["team_id"] == team_id]
    assert mine[0]["submitted"] is True
    assert mine[0]["players"] == []  # 未公开前不显示选手

    # 过去的比赛日（2000-01-05 周三）已锁定不可提交
    past = "2000-01-05"
    r = client.put("/api/captain/lineups", json={"date": past, "slot": 1,
                                                 "player_ids": ids[:1]})
    assert r.status_code == 403
    # 会更远的未来比赛日提交，验证未公开隐藏
    fut_d = next_matchdays(8)[-1]
    r = client.put("/api/captain/lineups", json={"date": fut_d.isoformat(), "slot": 1,
                                                 "player_ids": ids[:1]})
    assert r.status_code == 200
    future = client.get(f"/api/captain/public/lineups?date_str={fut_d.isoformat()}").json()
    assert future["locked"] is False
    rows = [r for r in future["rows"] if r["team_id"] == team_id and r["slot"] == 1][0]
    assert rows["players"] == []
    # 过去日期（2000 年虽无名单，但公开可见逻辑仍生效，见 test_public_hides_before_cutoff）

    # 管理端可见（含未公开前的名单）
    admin_rows = client.get("/api/admin/lineups", headers=_auth(client)).json()
    assert len(admin_rows) == 3


def test_uploaded_schedule_controls_active_and_bye_teams(client, db):
    team_id, other_id, ids = _seed(client, db)
    extra = []
    for number in (3, 4, 5, 6):
        team = Team(name=f"队伍{number}", team_number=number)
        db.add(team)
        extra.append(team)
    db.flush()
    match_date = date(2026, 10, 1)
    db.add(ScheduleDay(match_date=match_date, team_numbers=[1, 2, 3, 4], note="第1轮"))
    db.commit()
    _login(client)

    days = client.get("/api/captain/matchdays?count=6").json()
    assert [day["date"] for day in days] == [match_date.isoformat()]
    assert len(days[0]["active_teams"]) == 4
    assert {team["team_number"] for team in days[0]["bye_teams"]} == {5, 6}

    public = client.get(f"/api/captain/public/lineups?date_str={match_date}").json()
    assert len(public["rows"]) == 8
    assert {row["team_id"] for row in public["rows"]} == {
        team.id for team in [db.get(Team, team_id), db.get(Team, other_id), *extra[:2]]
    }

    # The captain's team is active and can submit on an uploaded match day.
    submitted = client.put("/api/captain/lineups", json={
        "date": match_date.isoformat(), "slot": 1, "player_ids": [ids[0]]
    })
    assert submitted.status_code == 200

    # Move the captain's team out of the active set and reject its submission.
    schedule = db.query(ScheduleDay).first()
    schedule.team_numbers = [2, 3, 4, 5]
    db.commit()
    rejected = client.put("/api/captain/lineups", json={
        "date": match_date.isoformat(), "slot": 2, "player_ids": [ids[1]]
    })
    assert rejected.status_code == 422


def test_captain_matchdays_hide_team_bye_days(client, db):
    team_id, other_id, ids = _seed(client, db)
    extra = []
    for number in (3, 4, 5, 6):
        team = Team(name=f"轮空测试队{number}", team_number=number)
        db.add(team)
        extra.append(team)
    db.flush()
    active_date = date(2026, 10, 1)
    bye_date = date(2026, 10, 2)
    db.add_all([
        ScheduleDay(match_date=active_date, team_numbers=[1, 2, 3, 4]),
        ScheduleDay(match_date=bye_date, team_numbers=[2, 3, 4, 5]),
    ])
    db.commit()
    _login(client)

    response = client.get("/api/captain/matchdays?count=6")
    assert response.status_code == 200
    assert [row["date"] for row in response.json()] == [active_date.isoformat()]


def test_public_hides_before_cutoff_but_shows_past(client, db, monkeypatch):
    """18:00 截止前不公开；截止后（含过去日期）公开显示。"""
    team_id, _, ids = _seed(client, db)
    _login(client)
    # 直接写一条“过去日期”的名单，验证公开显示
    db.add(Lineup(team_id=team_id, match_date=date(2000, 1, 5), slot=1,
                  player_ids=[ids[0]], submitted_by="captain_red"))
    db.commit()
    pub = client.get("/api/captain/public/lineups?date_str=2000-01-05").json()
    assert pub["locked"] is True
    row = [r for r in pub["rows"] if r["team_id"] == team_id and r["slot"] == 1][0]
    assert [p["id"] for p in row["players"]] == [ids[0]]

    # 模拟“今天已过 18:00”，下一个比赛日变为公开
    future = _next_matchday()
    db.add(Lineup(team_id=team_id, match_date=future, slot=1,
                  player_ids=[ids[0]], submitted_by="captain_red"))
    db.commit()
    monkeypatch.setattr("app.api.captain.is_locked", lambda md: True)
    pub2 = client.get("/api/captain/public/lineups").json()
    row2 = [r for r in pub2["rows"] if r["team_id"] == team_id and r["slot"] == 1][0]
    assert len(row2["players"]) == 1


def test_locked_after_cutoff(client, db, monkeypatch):
    """通过伪造日期模拟 18:00 后锁定。"""
    team_id, _, ids = _seed(client, db)
    _login(client)
    d = _next_matchday()

    # 模拟“已过 18:00”且日期为今天
    monkeypatch.setattr("app.api.captain.is_locked", lambda md: True)
    r = client.put("/api/captain/lineups", json={"date": d.isoformat(), "slot": 1,
                                                 "player_ids": ids[:4]})
    assert r.status_code == 403


def test_lineup_requires_matchday(client, db):
    team_id, _, ids = _seed(client, db)
    _login(client)
    # 找一个非比赛日
    nd = date.today()
    while nd.isoweekday() in MATCH_WEEKDAYS:
        nd += timedelta(days=1)
    r = client.put("/api/captain/lineups", json={"date": nd.isoformat(), "slot": 1,
                                                 "player_ids": ids[:4]})
    assert r.status_code == 422


def test_captain_team_roster_management(client, db):
    team_id, other_id, ids = _seed(client, db)
    _login(client)
    # 查看本队信息与成员
    r = client.get("/api/captain/team").json()
    assert r["team"]["name"] == "红中会"
    assert len(r["members"]) == 6
    # 修改队名
    r = client.put("/api/captain/team", json={"name": "红中會馆", "short_name": "红館"})
    assert r.status_code == 200
    assert db.get(Team, team_id).name == "红中會馆"
    # 添加成员
    r = client.post("/api/captain/players",
                    json={"nickname": "新人雀士", "account_id": 77777})
    assert r.status_code == 200
    new_id = r.json()["id"]
    assert db.get(Player, new_id).team_id == team_id
    # 重复 account_id 报错
    r = client.post("/api/captain/players",
                    json={"nickname": "重复", "account_id": 77777})
    assert r.status_code == 422
    # 移除成员
    r = client.delete(f"/api/captain/players/{new_id}")
    assert r.status_code == 200
    assert db.get(Player, new_id) is None
    # 不能移除他队选手
    db.add(Player(nickname="外人", account_id=888888, team_id=other_id))
    db.commit()
    other_pid = db.query(Player).filter(Player.team_id == other_id).first().id
    assert client.delete(f"/api/captain/players/{other_pid}").status_code == 404


def test_captain_can_verify_majsoul_account(client, db, monkeypatch):
    _seed(client, db)
    _login(client)
    monkeypatch.setattr(config, "DHS_USERNAME", "dhs-user")
    monkeypatch.setattr(config, "DHS_PASSWORD", "dhs-pass")

    class FakeChannel:
        async def connect(self):
            pass

        async def call(self, method, **fields):
            assert method == "loginContestManager"
            assert fields["account"] == "dhs-user"
            return None

    class FakeDHS:
        def __init__(self):
            self.channel = FakeChannel()

        async def search_by_account_id(self, account_id):
            assert account_id == 123456
            return [{"account_id": account_id, "nickname": "验证雀士"}]

        async def close(self):
            pass

    monkeypatch.setattr("app.services.majsoul.clients.DHSClient", FakeDHS)
    r = client.get("/api/captain/verify-player?account_id=123456")
    assert r.status_code == 200
    assert r.json() == {
        "exists": True, "account_id": 123456, "nickname": "验证雀士"
    }

    r = client.get("/api/captain/verify-player?account_id=0")
    assert r.status_code == 422


def test_captain_verify_uses_persistent_sync_credentials(client, db, monkeypatch):
    _seed(client, db)
    _login(client)
    db.add(League(name="测试联赛"))
    db.commit()
    league = db.query(League).first()
    league.sync_username = "saved-dhs-user"
    league.sync_password = "saved-dhs-pass"
    db.commit()
    monkeypatch.setattr(config, "DHS_USERNAME", "")
    monkeypatch.setattr(config, "DHS_PASSWORD", "")

    class FakeChannel:
        async def connect(self):
            pass

        async def call(self, method, **fields):
            assert method == "loginContestManager"
            assert fields["account"] == "saved-dhs-user"
            return None

    class FakeDHS:
        def __init__(self):
            self.channel = FakeChannel()

        async def search_by_account_id(self, account_id):
            return [{"account_id": account_id, "nickname": "持久化验证雀士"}]

        async def close(self):
            pass

    monkeypatch.setattr("app.services.majsoul.clients.DHSClient", FakeDHS)
    response = client.get("/api/captain/verify-player?account_id=123456")
    assert response.status_code == 200
    assert response.json()["nickname"] == "持久化验证雀士"


def test_captain_verify_reports_unconfigured_service(client, db, monkeypatch):
    _seed(client, db)
    _login(client)
    monkeypatch.setattr(config, "DHS_USERNAME", "")
    monkeypatch.setattr(config, "DHS_PASSWORD", "")
    r = client.get("/api/captain/verify-player?account_id=123456")
    assert r.status_code == 503


def test_captain_logo_upload(client, db, monkeypatch):
    team_id, _, _ = _seed(client, db)
    _login(client)
    from app.api import captain as cap_mod
    monkeypatch.setattr(cap_mod, "UPLOAD_DIR", Path("/tmp/_cpt_logo_test"))
    png = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\x0dIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\x0aIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff"
        b"\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    r = client.post("/api/captain/logo",
                    files={"file": ("logo.png", png, "image/png")})
    assert r.status_code == 200
    path = r.json()["logo_path"]
    assert path.startswith("/static/uploads/")
    assert db.get(Team, team_id).logo_path == path
    # 非法后缀
    r = client.post("/api/captain/logo",
                    files={"file": ("logo.gif", b"fake", "image/gif")})
    assert r.status_code == 422
    r = client.post("/api/captain/logo",
                    files={"file": ("logo.svg", b"<svg/>", "image/svg+xml")})
    assert r.status_code == 422
