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
    from app.services.majsoul.live_sync import start_live_sync

    with SessionLocal() as session:
        league = session.query(League).first()
        if league and league.auto_sync_enabled is not None:
            config.AUTO_SYNC_ENABLED = bool(league.auto_sync_enabled)
        live_enabled = (
            bool(league.live_sync_enabled)
            if league and league.live_sync_enabled is not None
            else config.LIVE_SYNC_ENABLED
        )
        live_user = (league.lobby_username if league else "") or config.MS_USERNAME
        live_pass = (league.lobby_password if league else "") or config.MS_PASSWORD
        live_token = (league.lobby_access_token if league else "") or config.MS_ACCESS_TOKEN
        live_interval = (league.live_sync_interval if league else 0) or config.LIVE_SYNC_INTERVAL
    if config.AUTO_SYNC_ENABLED:
        start_auto_sync(SessionLocal)
        print(f"赛事场自动检测已开启：每天 {config.AUTO_SYNC_TIME} 轮询一次")
    if live_enabled and ((live_user and live_pass) or live_token):
        start_live_sync(SessionLocal, live_user, live_pass,
                        access_token=live_token, interval=live_interval)
        print(f"雀魂大厅实时抓取已开启：每 {live_interval} 秒扫描一次")
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
