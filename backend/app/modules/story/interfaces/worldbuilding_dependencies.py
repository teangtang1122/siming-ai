"""FastAPI dependency for the worldbuilding application port."""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from ....database.session import get_db
from ..application.worldbuilding import WorldbuildingWorkspace

WorldbuildingFactory = Callable[[Session], WorldbuildingWorkspace]
_factory: WorldbuildingFactory | None = None


def configure_worldbuilding_dependencies(factory: WorldbuildingFactory) -> None:
    global _factory
    _factory = factory


def get_worldbuilding_workspace(
    db: Annotated[Session, Depends(get_db)],
) -> WorldbuildingWorkspace:
    if _factory is None:
        raise RuntimeError("Worldbuilding dependencies have not been configured")
    return _factory(db)


__all__ = ["configure_worldbuilding_dependencies", "get_worldbuilding_workspace"]
