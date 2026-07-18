"""FastAPI dependency for system-assistant conversation persistence."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from ....architecture.uow import SqlAlchemyUnitOfWork
from ....database.session import get_db
from ..application.system_conversations import SystemConversationStore

ConversationFactory = Callable[[Session], SystemConversationStore]
_factory: ConversationFactory | None = None


def configure_system_conversation_dependencies(factory: ConversationFactory) -> None:
    global _factory
    _factory = factory


def get_system_conversation_store(
    db: Annotated[Session, Depends(get_db)],
) -> Iterator[SystemConversationStore]:
    if _factory is None:
        raise RuntimeError("System conversation dependencies have not been configured")
    with SqlAlchemyUnitOfWork.from_session(db) as uow:
        yield _factory(db)
        uow.commit()


__all__ = [
    "configure_system_conversation_dependencies",
    "get_system_conversation_store",
]
