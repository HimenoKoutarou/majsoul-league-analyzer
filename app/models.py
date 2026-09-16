"""数据库模型。Player 以代理主键 + account_id 唯一索引（ninklang 通道无 account_id）。"""
from datetime import datetime

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, ForeignKey,
                        Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

DEFAULT_SCORE_RULE = {"rank_points": [90, 45, 0, -45], "allow_negative": True, "tiebreak": "raw_points"}


class League(Base):
    __tablename__ = "league"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="麻将联赛")
    logo_path: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    contest_id: Mapped[int | None] = mapped_column(Integer)
    score_rule: Mapped[dict] = mapped_column(JSON, default=lambda: dict(DEFAULT_SCORE_RULE))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    short_name: Mapped[str] = mapped_column(String(16), default="")
    logo_path: Mapped[str | None] = mapped_column(String(255))
    color: Mapped[str] = mapped_column(String(16), default="#5b8cff")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    nickname: Mapped[str] = mapped_column(String(64), index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    contest_registered: Mapped[bool] = mapped_column(Boolean, default=False)


class Game(Base):
    __tablename__ = "games"
    uuid: Mapped[str] = mapped_column(String(64), primary_key=True)
    contest_id: Mapped[int | None] = mapped_column(Integer)
    start_time: Mapped[datetime | None] = mapped_column(DateTime)
    mode: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_head: Mapped[dict] = mapped_column(JSON, default=dict)
    fetched_via: Mapped[str] = mapped_column(String(16), default="ninklang")
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class GamePlayer(Base):
    __tablename__ = "game_players"
    game_uuid: Mapped[str] = mapped_column(ForeignKey("games.uuid", ondelete="CASCADE"), primary_key=True)
    seat: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id", ondelete="SET NULL"), index=True)
    nickname: Mapped[str] = mapped_column(String(64))
    final_score: Mapped[int] = mapped_column(Integer, default=0)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    pt: Mapped[float] = mapped_column(Float, default=0.0)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)


class Kyoku(Base):
    __tablename__ = "kyokus"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_uuid: Mapped[str] = mapped_column(ForeignKey("games.uuid", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    round_data: Mapped[list] = mapped_column(JSON)
    data: Mapped[list] = mapped_column(JSON)
    summary: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("game_uuid", "index", name="uq_kyoku_game_index"),)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="running")
    games_added: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
