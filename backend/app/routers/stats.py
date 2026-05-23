"""Writing statistics — today stats, history, and daily goal management."""
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.response import ApiResponse
from ..database.models import Chapter, Project, WritingLog
from ..database.session import get_db
from ..schemas.stats import GoalUpdate

router = APIRouter(tags=["stats"])


def _get_or_create_today_log(db: Session, project_id: str) -> WritingLog:
    today = date.today()
    log = (
        db.query(WritingLog)
        .filter(WritingLog.project_id == project_id, WritingLog.date == today)
        .first()
    )
    if not log:
        log = WritingLog(project_id=project_id, date=today, total_words=0)
        db.add(log)
        db.commit()
        db.refresh(log)
    return log


def _compute_today_words(db: Session, project_id: str) -> int:
    """Read today's accumulated net writing delta from writing_logs."""
    log = (
        db.query(WritingLog)
        .filter(WritingLog.project_id == project_id, WritingLog.date == date.today())
        .first()
    )
    return log.total_words if log else 0


@router.get("/projects/{project_id}/stats/today")
def get_today_stats(project_id: str, db: Session = Depends(get_db)):
    """Get today's writing statistics."""
    project = get_project_or_404(db, project_id)
    today = date.today()
    today_words = _compute_today_words(db, project_id)

    _get_or_create_today_log(db, project_id)

    # Count chapters updated today
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    chapters_count = (
        db.query(func.count(Chapter.id))
        .filter(
            Chapter.project_id == project_id,
            Chapter.updated_at >= today_start,
        )
        .scalar()
    ) or 0

    goal = project.daily_word_goal or 6000
    progress = round((today_words / goal) * 100, 1) if goal > 0 else 0

    return ApiResponse.success(data={
        "date": today.isoformat(),
        "total_words": today_words,
        "daily_goal": goal,
        "progress_percent": min(progress, 100.0),
        "chapters_written": chapters_count,
    })


@router.get("/projects/{project_id}/stats/history")
def get_stats_history(
    project_id: str,
    days: int = Query(7, ge=1, le=365, description="Number of days to look back"),
    db: Session = Depends(get_db),
):
    """Get historical daily writing statistics."""
    project = get_project_or_404(db, project_id)
    start_date = date.today() - timedelta(days=days - 1)

    logs = (
        db.query(WritingLog)
        .filter(
            WritingLog.project_id == project_id,
            WritingLog.date >= start_date,
        )
        .order_by(WritingLog.date.asc())
        .all()
    )

    log_by_date = {log_entry.date: log_entry for log_entry in logs}
    goal = project.daily_word_goal or 6000
    items = []
    total_words = 0
    current = start_date
    while current <= date.today():
        log_entry = log_by_date.get(current)
        words = log_entry.total_words if log_entry else 0
        total_words += words
        items.append({
            "date": current.isoformat(),
            "total_words": words,
            "daily_goal": goal,
        })
        current += timedelta(days=1)

    actual_days = max(len(items), 1)
    return ApiResponse.success(data={
        "items": items,
        "total_days": actual_days,
        "total_words": total_words,
        "average_words_per_day": round(total_words / actual_days, 1),
    })


@router.put("/projects/{project_id}/stats/goal")
def set_daily_goal(project_id: str, payload: GoalUpdate, db: Session = Depends(get_db)):
    """Set the daily word count goal for a project."""
    project = get_project_or_404(db, project_id)
    project.daily_word_goal = payload.daily_word_goal
    db.commit()
    return ApiResponse.success(
        data={"daily_word_goal": project.daily_word_goal},
        message=f"每日目标已更新为 {project.daily_word_goal} 字",
    )
