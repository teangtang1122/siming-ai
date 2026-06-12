"""Writing statistics — today stats, history, and daily goal management."""
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.response import ApiResponse
from ..database.models import Chapter, Project
from ..database.session import get_db
from ..schemas.stats import GoalUpdate

router = APIRouter(tags=["stats"])


def _local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Return UTC-naive bounds for one local calendar day.

    SQLAlchemy defaults currently store timestamps as UTC-naive values. The UI
    and daily writing goal should follow the user's local calendar day, so we
    convert local midnight boundaries back to UTC-naive query bounds.
    """
    local_tz = datetime.now().astimezone().tzinfo
    start_local = datetime.combine(day, time.min).replace(tzinfo=local_tz)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _utc_naive_to_local_date(value: datetime) -> str:
    local_tz = datetime.now().astimezone().tzinfo
    return value.replace(tzinfo=timezone.utc).astimezone(local_tz).date().isoformat()


@router.get("/projects/{project_id}/stats/today")
def get_today_stats(project_id: str, db: Session = Depends(get_db)):
    """Get today's writing statistics based on chapter creation date."""
    project = get_project_or_404(db, project_id)
    today = datetime.now().astimezone().date()
    today_start, today_end = _local_day_bounds(today)

    row = (
        db.query(
            func.coalesce(func.sum(Chapter.word_count), 0).label("total_words"),
            func.count(Chapter.id).label("chapters_count"),
        )
        .filter(
            Chapter.project_id == project_id,
            Chapter.created_at >= today_start,
            Chapter.created_at < today_end,
        )
        .one()
    )

    goal = project.daily_word_goal or 6000
    today_words = int(row.total_words)
    progress = round((today_words / goal) * 100, 1) if goal > 0 else 0

    return ApiResponse.success(data={
        "date": today.isoformat(),
        "total_words": today_words,
        "daily_goal": goal,
        "progress_percent": min(progress, 100.0),
        "chapters_written": int(row.chapters_count),
    })


@router.get("/projects/{project_id}/stats/history")
def get_stats_history(
    project_id: str,
    days: int = Query(7, ge=1, le=365, description="Number of days to look back"),
    db: Session = Depends(get_db),
):
    """Get historical daily writing statistics based on chapter creation dates."""
    project = get_project_or_404(db, project_id)
    today = datetime.now().astimezone().date()
    start_date = today - timedelta(days=days - 1)
    start_dt, _ = _local_day_bounds(start_date)

    chapters = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.created_at >= start_dt,
        )
        .all()
    )

    words_by_date: dict[str, int] = {}
    for chapter in chapters:
        if not chapter.created_at:
            continue
        day_key = _utc_naive_to_local_date(chapter.created_at)
        words_by_date[day_key] = words_by_date.get(day_key, 0) + int(chapter.word_count or 0)
    goal = project.daily_word_goal or 6000
    items = []
    total_words = 0
    current = start_date
    while current <= today:
        words = words_by_date.get(current.isoformat(), 0)
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
