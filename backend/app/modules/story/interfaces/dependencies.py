"""FastAPI dependencies for story command transaction boundaries."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from ....architecture.uow import SqlAlchemyUnitOfWork
from ....database.session import get_db
from ..application.commands import StoryCommandContext
from ..application.ports import ContentSyncOutbox

OutboxFactory = Callable[[Session], ContentSyncOutbox]
_outbox_factory: OutboxFactory | None = None


def configure_story_dependencies(outbox_factory: OutboxFactory) -> None:
    """Bind story application ports during explicit app composition."""

    global _outbox_factory
    _outbox_factory = outbox_factory


def get_story_command(
    db: Annotated[Session, Depends(get_db)],
) -> Iterator[StoryCommandContext]:
    """Yield one composed story command over the request-owned session."""

    if _outbox_factory is None:
        raise RuntimeError("Story dependencies have not been configured")
    with SqlAlchemyUnitOfWork.from_session(db) as uow:
        yield StoryCommandContext(uow=uow, outbox=_outbox_factory(db))


__all__ = ["configure_story_dependencies", "get_story_command"]
