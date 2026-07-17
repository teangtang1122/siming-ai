"""Shared transaction context for story-changing application use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ....architecture.uow import UnitOfWork
from ..domain.content_sync import ContentSyncIntent
from .ports import ContentSyncOutbox


@dataclass(slots=True)
class StoryCommandContext:
    """Own the transaction and mirror outbox used by one story command."""

    uow: UnitOfWork
    outbox: ContentSyncOutbox

    @property
    def session(self) -> Any:
        """Compatibility bridge while repositories replace legacy ORM access."""

        return self.uow.session

    def queue(self, intent: ContentSyncIntent) -> str:
        return self.outbox.enqueue(intent)

    def flush(self) -> None:
        self.uow.flush()

    def finish(self) -> None:
        """Commit the business change and its outbox records atomically."""

        self.uow.commit()

    def rollback(self) -> None:
        self.uow.rollback()


__all__ = ["StoryCommandContext"]
