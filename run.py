"""启动入口。"""
import uvicorn

from app import config
from app.main import init_db


def main():
    init_db()
    print(f"管理后台: http://127.0.0.1:{config.PORT}/admin")
    print(f"管理 token: {config.ensure_admin_token()}")
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
