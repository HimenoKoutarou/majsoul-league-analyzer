"""FastAPI 应用装配。"""
from pathlib import Path
from html import escape

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import app.config as config

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app() -> FastAPI:
    application = FastAPI(title="雀魂联赛分析")

    @application.middleware("http")
    async def prevent_stale_frontend_cache(request: Request, call_next):
        """页面和前端脚本需及时反映管理员保存的赛事信息。"""
        response = await call_next(request)
        path = request.url.path
        frontend_pages = {
            "/", "/games", "/analysis", "/bounties", "/lineups",
            "/captain", "/captain/login", "/admin", "/admin/login",
        }
        if path in frontend_pages or path.endswith(".html") or path.startswith("/static/js/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
        if path in frontend_pages or path.endswith(".html"):
            content_type = response.headers.get("content-type", "")
            if "text/html" in content_type:
                body = b"".join([chunk async for chunk in response.body_iterator])
                from app.db import SessionLocal
                from app.models import League

                with SessionLocal() as session:
                    league = session.query(League).first()
                    brand = escape(league.name if league and league.name else "联赛")
                body = body.replace(
                    '<span class="brand">联赛</span>'.encode(),
                    f'<span class="brand">{brand}</span>'.encode(),
                )
                headers = dict(response.headers)
                for header in ("content-length", "etag", "last-modified"):
                    headers.pop(header, None)
                response = Response(
                    content=body,
                    status_code=response.status_code,
                    headers=headers,
                    media_type="text/html",
                )
                response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    from app.api import admin, auth, bot, bounties, captain, public

    application.include_router(public.router, prefix="/api")
    application.include_router(bot.router, prefix="/api")
    application.include_router(bounties.router, prefix="/api")
    application.include_router(captain.router, prefix="/api")
    application.include_router(auth.router, prefix="/api/admin")
    application.include_router(admin.router, prefix="/api/admin")
    application.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @application.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @application.get("/games")
    def games_page():
        return FileResponse(WEB_DIR / "games.html")

    @application.get("/analysis")
    def analysis_page():
        return FileResponse(WEB_DIR / "analysis.html")

    @application.get("/bounties")
    def bounties_page():
        return FileResponse(WEB_DIR / "bounties.html")

    @application.get("/lineups")
    def lineups_page():
        return FileResponse(WEB_DIR / "lineups.html")

    @application.get("/captain")
    def captain_page():
        return FileResponse(WEB_DIR / "captain.html")

    @application.get("/captain/login")
    def captain_login_page():
        return FileResponse(WEB_DIR / "captain_login.html")

    @application.get("/admin")
    def admin_page():
        return FileResponse(WEB_DIR / "admin.html")

    @application.get("/admin/login")
    def admin_login_page():
        return FileResponse(WEB_DIR / "admin_login.html")

    return application


app = create_app()


def init_db():
    """建表并保证 league 单行存在。"""
    from app import models  # noqa: F401 确保模型注册
    from app.db import Base, SessionLocal, engine

    Base.metadata.create_all(engine)
    # create_all does not add columns to an existing database.
    from sqlalchemy import inspect, text
    additions = {
        "league": {
            "organizer": "VARCHAR(128) NOT NULL DEFAULT ''",
            "season": "VARCHAR(64) NOT NULL DEFAULT ''",
            "start_date": "DATE",
            "end_date": "DATE",
            "contact": "VARCHAR(255) NOT NULL DEFAULT ''",
            "sync_username": "VARCHAR(128) NOT NULL DEFAULT ''",
            "sync_password": "VARCHAR(255) NOT NULL DEFAULT ''",
            "lobby_username": "VARCHAR(128) NOT NULL DEFAULT ''",
            "lobby_password": "VARCHAR(255) NOT NULL DEFAULT ''",
            "lobby_access_token": "VARCHAR(512) NOT NULL DEFAULT ''",
            "auto_sync_enabled": "BOOLEAN",
            "live_sync_enabled": "BOOLEAN",
            "live_sync_interval": "INTEGER NOT NULL DEFAULT 60",
        },
        "captains": {"password_plaintext": "VARCHAR(128)"},
        "teams": {"team_number": "INTEGER NOT NULL DEFAULT 0"},
    }
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                    ))
    (WEB_DIR / "uploads").mkdir(parents=True, exist_ok=True)
    with SessionLocal() as session:
        from app.models import League, Team

        if session.query(League).count() == 0:
            session.add(League(name="麻将联赛"))
            session.commit()
        # 为升级前创建的队伍补齐连续编号，避免旧数据一直显示为 0。
        teams = session.query(Team).filter(Team.team_number <= 0).order_by(Team.id).all()
        used = {team.team_number for team in session.query(Team).filter(Team.team_number > 0)}
        next_number = 1
        for team in teams:
            while next_number in used:
                next_number += 1
            team.team_number = next_number
            used.add(next_number)
            next_number += 1
        if teams:
            session.commit()
