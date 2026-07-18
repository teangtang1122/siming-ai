"""Composition boundary for novel-creation session persistence."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..application.session_store import NovelCreationSessionStore

NovelCreationSessionStoreFactory = Callable[[Any], NovelCreationSessionStore]
_factory: NovelCreationSessionStoreFactory | None = None


def configure_novel_creation_session_store(factory: NovelCreationSessionStoreFactory) -> None:
    global _factory
    _factory = factory


def novel_creation_session_store(session: Any) -> NovelCreationSessionStore:
    if _factory is None:
        raise RuntimeError("Novel creation session persistence has not been configured")
    return _factory(session)


__all__ = ["configure_novel_creation_session_store", "novel_creation_session_store"]
