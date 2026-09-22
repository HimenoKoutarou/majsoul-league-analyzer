"""赛程读取和校验。每个比赛日固定两场，四支队伍出战，其余队伍轮空。"""
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import ScheduleDay, Team


def scheduled_day(db: Session, match_date: date) -> ScheduleDay | None:
    return db.query(ScheduleDay).filter(ScheduleDay.match_date == match_date).first()


def scheduled_matchdays(db: Session, count: int = 6) -> list[ScheduleDay]:
    return (db.query(ScheduleDay)
            .filter(ScheduleDay.match_date >= date.today())
            .order_by(ScheduleDay.match_date)
            .limit(count).all())


def has_schedule(db: Session) -> bool:
    return db.query(ScheduleDay.id).first() is not None


def active_team_numbers(row: ScheduleDay | None) -> list[int]:
    return [int(number) for number in (row.team_numbers if row else [])]


def schedule_view(db: Session, row: ScheduleDay | None) -> dict:
    active_numbers = set(active_team_numbers(row))
    teams = db.query(Team).order_by(Team.sort_order, Team.id).all()
    active = [t for t in teams if t.team_number in active_numbers]
    bye = [t for t in teams if t.team_number not in active_numbers]
    return {
        "date": row.match_date.isoformat() if row else None,
        "team_numbers": sorted(active_numbers),
        "active_teams": [{"id": t.id, "team_number": t.team_number,
                          "name": t.name, "color": t.color} for t in active],
        "bye_teams": [{"id": t.id, "team_number": t.team_number,
                       "name": t.name, "color": t.color} for t in bye],
        "note": row.note if row else "",
    }


def fallback_matchday(match_date: date) -> bool:
    return match_date.isoweekday() in (3, 5, 7)
