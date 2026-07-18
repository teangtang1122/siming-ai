"""FastAPI dependency for the writing-statistics application port."""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from ....database.session import get_db
from ..application.statistics import StoryStatistics

StatisticsFactory = Callable[[Session], StoryStatistics]
_factory: StatisticsFactory | None = None


def configure_statistics_dependencies(factory: StatisticsFactory) -> None:
    global _factory
    _factory = factory


def get_story_statistics(
    db: Annotated[Session, Depends(get_db)],
) -> StoryStatistics:
    if _factory is None:
        raise RuntimeError("Story statistics dependencies have not been configured")
    return _factory(db)


__all__ = ["configure_statistics_dependencies", "get_story_statistics"]
