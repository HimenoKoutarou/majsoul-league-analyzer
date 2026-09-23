"""机器人专用只读 API。"""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

import app.config as config
from app.db import get_db
from app.models import EventOutbox

router = APIRouter(prefix="/bot")


def require_bot_token(
    authorization: str | None = Header(default=None),
    x_bot_token: str | None = Header(default=None),
):
    expected = config.BOT_API_TOKEN
    if not expected:
        raise HTTPException(503, "BOT_API_TOKEN 未配置")
    token = x_bot_token or ""
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(401, "机器人 token 无效")
    return True


@router.get("/events", dependencies=[Depends(require_bot_token)])
def list_events(
    after_id: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    rows = (db.query(EventOutbox)
            .filter(EventOutbox.id > after_id)
            .order_by(EventOutbox.id)
            .limit(limit).all())
    return {
        "items": [
            {
                "id": row.id,
                "event_type": row.event_type,
                "aggregate_id": row.aggregate_id,
                "payload": row.payload,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "next_id": rows[-1].id if rows else after_id,
    }
