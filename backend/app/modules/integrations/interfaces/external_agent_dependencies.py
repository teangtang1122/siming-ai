"""FastAPI dependency for External Agent permission settings."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from ....architecture.uow import SqlAlchemyUnitOfWork
from ....database.session import get_db
from ..application.external_agent_settings import ExternalAgentSettingsStore

SettingsFactory = Callable[[Session], ExternalAgentSettingsStore]
_factory: SettingsFactory | None = None


def configure_external_agent_dependencies(factory: SettingsFactory) -> None:
    global _factory
    _factory = factory


def get_external_agent_settings_store(
    db: Annotated[Session, Depends(get_db)],
) -> Iterator[ExternalAgentSettingsStore]:
    if _factory is None:
        raise RuntimeError("External Agent dependencies have not been configured")
    with SqlAlchemyUnitOfWork.from_session(db) as uow:
        yield _factory(db)
        uow.commit()


__all__ = [
    "configure_external_agent_dependencies",
    "get_external_agent_settings_store",
]
