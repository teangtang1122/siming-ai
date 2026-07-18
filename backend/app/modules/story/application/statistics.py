"""Writing-statistics port used by HTTP and future tool interfaces."""
from __future__ import annotations

from typing import Protocol


class StoryStatistics(Protocol):
    """Persistence-neutral statistics operations for one request session."""

    def today(self, project_id: str) -> dict: ...

    def history(self, project_id: str, days: int) -> dict: ...

    def set_daily_goal(self, project_id: str, daily_word_goal: int) -> dict: ...


__all__ = ["StoryStatistics"]
