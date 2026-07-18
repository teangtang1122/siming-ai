"""SQLAlchemy writing-statistics adapter."""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from ....core.db_helpers import get_project_or_404
from .entities import Chapter


def _local_day_bounds(day: date) -> tuple[datetime, datetime]:
    local_tz = datetime.now().astimezone().tzinfo
    start_local = datetime.combine(day, time.min).replace(tzinfo=local_tz)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(UTC).replace(tzinfo=None),
        end_local.astimezone(UTC).replace(tzinfo=None),
    )


def _utc_naive_to_local_date(value: datetime) -> str:
    local_tz = datetime.now().astimezone().tzinfo
    return value.replace(tzinfo=UTC).astimezone(local_tz).date().isoformat()


class SqlAlchemyStoryStatistics:
    """Query and update writing statistics without leaking ORM into routers."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def today(self, project_id: str) -> dict:
        project = get_project_or_404(self._session, project_id)
        today = datetime.now().astimezone().date()
        today_start, today_end = _local_day_bounds(today)
        row = (
            self._session.query(
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
        return {
            "date": today.isoformat(),
            "total_words": today_words,
            "daily_goal": goal,
            "progress_percent": min(progress, 100.0),
            "chapters_written": int(row.chapters_count),
        }

    def history(self, project_id: str, days: int) -> dict:
        project = get_project_or_404(self._session, project_id)
        today = datetime.now().astimezone().date()
        start_date = today - timedelta(days=days - 1)
        start_dt, _ = _local_day_bounds(start_date)
        chapters = (
            self._session.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.created_at >= start_dt)
            .all()
        )
        words_by_date: dict[str, int] = {}
        for chapter in chapters:
            if chapter.created_at:
                day_key = _utc_naive_to_local_date(chapter.created_at)
                words_by_date[day_key] = words_by_date.get(day_key, 0) + int(
                    chapter.word_count or 0
                )
        goal = project.daily_word_goal or 6000
        items: list[dict] = []
        total_words = 0
        current = start_date
        while current <= today:
            words = words_by_date.get(current.isoformat(), 0)
            total_words += words
            items.append(
                {"date": current.isoformat(), "total_words": words, "daily_goal": goal}
            )
            current += timedelta(days=1)
        actual_days = max(len(items), 1)
        return {
            "items": items,
            "total_days": actual_days,
            "total_words": total_words,
            "average_words_per_day": round(total_words / actual_days, 1),
        }

    def set_daily_goal(self, project_id: str, daily_word_goal: int) -> dict:
        project = get_project_or_404(self._session, project_id)
        project.daily_word_goal = daily_word_goal
        self._session.flush()
        return {"daily_word_goal": project.daily_word_goal}


__all__ = ["SqlAlchemyStoryStatistics"]
