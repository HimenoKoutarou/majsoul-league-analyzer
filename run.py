"""启动入口。"""
import uvicorn

from app import config
from app.main import init_db


def main():
    init_db()
    print(f"管理后台: http://127.0.0.1:{config.PORT}/admin")
    print(f"登录页: http://127.0.0.1:{config.PORT}/admin/login")
    config.print_admin_credentials()
    from app.db import SessionLocal
    from app.models import League
    from app.services.majsoul.auto_sync import start_auto_sync

    with SessionLocal() as session:
        league = session.query(League).first()
        if league and league.auto_sync_enabled is not None:
            config.AUTO_SYNC_ENABLED = bool(league.auto_sync_enabled)
    if config.AUTO_SYNC_ENABLED:
        start_auto_sync(SessionLocal)
        print(f"赛事场自动检测已开启：每天 {config.AUTO_SYNC_TIME} 轮询一次")
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
