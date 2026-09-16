import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base

# 让 majsoul 子包内的 import liqi_combined_pb2 直接可用
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "services" / "majsoul"))


@pytest.fixture
def db():
    """每测试独立的内存 SQLite 会话。"""
    import app.models  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
