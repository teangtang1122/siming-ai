"""FastAPI dependency for the project application port."""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from ....database.session import get_db
from ..application.projects import ProjectWorkspace

ProjectFactory = Callable[[Session], ProjectWorkspace]
_factory: ProjectFactory | None = None


def configure_project_dependencies(factory: ProjectFactory) -> None:
    global _factory
    _factory = factory


def get_project_workspace(
    db: Annotated[Session, Depends(get_db)],
) -> ProjectWorkspace:
    if _factory is None:
        raise RuntimeError("Project dependencies have not been configured")
    return _factory(db)


__all__ = ["configure_project_dependencies", "get_project_workspace"]
