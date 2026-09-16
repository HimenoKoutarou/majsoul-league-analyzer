"""SQLAlchemy engine / session。"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app import config


class Base(DeclarativeBase):
    pass


def _make_engine():
    connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
    return create_engine(config.DATABASE_URL, connect_args=connect_args)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

if config.DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
