"""Application ports for story write use cases."""

from __future__ import annotations

from typing import Protocol

from ..domain.content_sync import ContentSyncIntent


class ContentSyncOutbox(Protocol):
    """Persist mirror work in the active story transaction."""

    def enqueue(self, intent: ContentSyncIntent) -> str:
        """Return the durable synchronization job identifier."""


__all__ = ["ContentSyncOutbox"]
