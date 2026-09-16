"""FastAPI 应用装配。"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app() -> FastAPI:
    application = FastAPI(title="雀魂联赛分析")
    from app.api import admin, auth, public

    application.include_router(public.router, prefix="/api")
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
    (WEB_DIR / "uploads").mkdir(parents=True, exist_ok=True)
    with SessionLocal() as session:
        from app.models import League

        if session.query(League).count() == 0:
            session.add(League(name="麻将联赛"))
            session.commit()
