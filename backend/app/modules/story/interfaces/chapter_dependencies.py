"""FastAPI dependency for the chapter application port."""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from ....database.session import get_db
from ..application.chapters import ChapterWorkspace

ChapterFactory = Callable[[Session], ChapterWorkspace]
_factory: ChapterFactory | None = None


def configure_chapter_dependencies(factory: ChapterFactory) -> None:
    global _factory
    _factory = factory


def get_chapter_workspace(
    db: Annotated[Session, Depends(get_db)],
) -> ChapterWorkspace:
    if _factory is None:
        raise RuntimeError("Chapter dependencies have not been configured")
    return _factory(db)


__all__ = ["configure_chapter_dependencies", "get_chapter_workspace"]
