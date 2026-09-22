"""悬赏板公开只读/提交 API（匿名 + 雀魂ID）。"""
from datetime import date, datetime
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Bounty, BountyClaim

router = APIRouter()


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _parse_account(value) -> int | None:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _validate_share_url(value: str) -> str:
    url = value.strip()
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "game.maj-soul.com"
            or parsed.path.rstrip("/") != "/1"
            or not parse_qs(parsed.query).get("paipu")):
        raise HTTPException(422, "牌谱链接必须是有效的雀魂 HTTPS 分享链接")
    return url


def _serialize_bounty(b: Bounty, claimed_count: int) -> dict:
    return {
        "id": b.id, "title": b.title, "description": b.description,
        "reward": b.reward,
        "target_date": b.target_date.isoformat() if b.target_date else None,
        "max_claims": b.max_claims, "status": b.status,
        "claimed_count": claimed_count,
        "submitted_by": b.submitter_nickname,
    }


def _claimed_count(db: Session, bounty_id: int) -> int:
    return (db.query(BountyClaim)
            .filter(BountyClaim.bounty_id == bounty_id,
                    BountyClaim.status == "approved")
            .count())


@router.get("/bounties")
def list_bounties(db: Session = Depends(get_db)):
    rows = (db.query(Bounty)
            .filter(Bounty.status.in_(["approved", "closed"]))
            .order_by(Bounty.target_date.desc(), Bounty.id.desc()).all())
    return [_serialize_bounty(b, _claimed_count(db, b.id)) for b in rows]


@router.post("/bounties")
def create_bounty(body: dict, db: Session = Depends(get_db)):
    title = str(body.get("title") or "").strip()
    description = str(body.get("description") or "").strip()
    if not title or not description:
        raise HTTPException(422, "标题与达成条件不能为空")
    target_date = _parse_date(body.get("target_date"))
    if not target_date:
        raise HTTPException(422, "目标日期无效（需 YYYY-MM-DD）")
    bounty = Bounty(
        title=title, description=description,
        reward=str(body.get("reward") or "").strip(),
        target_date=target_date, max_claims=1, status="pending",
        submitter_nickname=str(body.get("nickname") or "").strip(),
        submitter_account_id=_parse_account(body.get("account_id")))
    db.add(bounty)
    db.commit()
    return {"id": bounty.id}


@router.get("/bounties/{bounty_id}/claims")
def list_claims(bounty_id: int, db: Session = Depends(get_db)):
    claims = (db.query(BountyClaim)
              .filter(BountyClaim.bounty_id == bounty_id,
                      BountyClaim.status == "approved")
              .order_by(BountyClaim.created_at.asc()).all())
    return [{"id": c.id, "submitter_nickname": c.submitter_nickname,
             "note": c.note,
             "created_at": c.created_at.isoformat() if c.created_at else None}
            for c in claims]


@router.post("/bounties/{bounty_id}/claims")
def create_claim(bounty_id: int, body: dict, db: Session = Depends(get_db)):
    bounty = db.get(Bounty, bounty_id)
    if not bounty or bounty.status != "approved":
        raise HTTPException(404, "悬赏不存在或未开放")
    if bounty.target_date and bounty.target_date > date.today():
        raise HTTPException(422, "尚未到目标日期，暂不能提交牌谱")
    if _claimed_count(db, bounty_id) >= bounty.max_claims:
        raise HTTPException(409, "该悬赏已达成上限")
    share_url = str(body.get("share_url") or "").strip()
    if not share_url:
        raise HTTPException(422, "牌谱链接不能为空")
    share_url = _validate_share_url(share_url)
    claim = BountyClaim(
        bounty_id=bounty_id, share_url=share_url,
        submitter_nickname=str(body.get("nickname") or "").strip(),
        submitter_account_id=_parse_account(body.get("account_id")),
        note=str(body.get("note") or "").strip(),
        status="pending")
    db.add(claim)
    db.commit()
    return {"id": claim.id}
