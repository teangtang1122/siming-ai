"""Application-facing access to durable story mirror synchronization."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..domain.content_sync import ContentSyncIntent


class ContentSyncRuntime(Protocol):
    """Port implemented by the configured story storage infrastructure."""

    def enqueue(self, session: Any, intent: ContentSyncIntent) -> Any:
        """Persist one synchronization intent in the active transaction."""

    def enqueue_project(
        self,
        session: Any,
        project_id: str,
        *,
        source: str,
    ) -> Any:
        """Persist a complete project mirror rebuild intent."""

    def ensure_chapter(
        self,
        session: Any,
        project: Any,
        chapter: Any,
        *,
        index: int = 0,
        source: str = "mirror_read",
    ) -> tuple[Path, Path]:
        """Return a readable chapter mirror, rebuilding it when needed."""

    def health(self, session: Any, project_id: str) -> dict[str, Any]:
        """Return synchronization queue health for one project."""


_runtime: ContentSyncRuntime | None = None


def configure_content_sync_runtime(runtime: ContentSyncRuntime) -> None:
    """Bind the storage implementation from the application composition root."""

    global _runtime
    _runtime = runtime


def _configured_runtime() -> ContentSyncRuntime:
    if _runtime is None:
        raise RuntimeError("Story content synchronization has not been configured")
    return _runtime


def queue_content_sync(session: Any, intent: ContentSyncIntent) -> Any:
    return _configured_runtime().enqueue(session, intent)


def enqueue_project_sync(
    session: Any,
    project_id: str,
    *,
    source: str,
) -> Any:
    return _configured_runtime().enqueue_project(session, project_id, source=source)


def ensure_chapter_mirror(
    session: Any,
    project: Any,
    chapter: Any,
    *,
    index: int = 0,
    source: str = "mirror_read",
) -> tuple[Path, Path]:
    return _configured_runtime().ensure_chapter(
        session,
        project,
        chapter,
        index=index,
        source=source,
    )


def content_sync_health(session: Any, project_id: str) -> dict[str, Any]:
    return _configured_runtime().health(session, project_id)


__all__ = [
    "ContentSyncRuntime",
    "configure_content_sync_runtime",
    "content_sync_health",
    "enqueue_project_sync",
    "ensure_chapter_mirror",
    "queue_content_sync",
]
