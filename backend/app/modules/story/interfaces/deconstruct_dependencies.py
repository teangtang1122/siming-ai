"""FastAPI dependency for deconstruction read operations."""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from ....database.session import get_db
from ..application.deconstruct import DeconstructionReader

DeconstructionFactory = Callable[[Session], DeconstructionReader]
_factory: DeconstructionFactory | None = None


def configure_deconstruction_dependencies(factory: DeconstructionFactory) -> None:
    global _factory
    _factory = factory


def get_deconstruction_reader(
    db: Annotated[Session, Depends(get_db)],
) -> DeconstructionReader:
    if _factory is None:
        raise RuntimeError("Deconstruction dependencies have not been configured")
    return _factory(db)


__all__ = ["configure_deconstruction_dependencies", "get_deconstruction_reader"]
