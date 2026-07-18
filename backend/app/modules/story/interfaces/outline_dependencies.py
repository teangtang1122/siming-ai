"""FastAPI dependency for the outline application port."""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from ....database.session import get_db
from ..application.outline import OutlineWorkspace

OutlineFactory = Callable[[Session], OutlineWorkspace]
_factory: OutlineFactory | None = None


def configure_outline_dependencies(factory: OutlineFactory) -> None:
    global _factory
    _factory = factory


def get_outline_workspace(
    db: Annotated[Session, Depends(get_db)],
) -> OutlineWorkspace:
    if _factory is None:
        raise RuntimeError("Outline dependencies have not been configured")
    return _factory(db)


__all__ = ["configure_outline_dependencies", "get_outline_workspace"]
