"""Composition boundary for the local model persistence port."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..application.local_model_store import LocalModelStore

LocalModelStoreFactory = Callable[[Any], LocalModelStore]
_factory: LocalModelStoreFactory | None = None


def configure_local_model_store(factory: LocalModelStoreFactory) -> None:
    global _factory
    _factory = factory


def local_model_store(session: Any) -> LocalModelStore:
    if _factory is None:
        raise RuntimeError("Local model persistence has not been configured")
    return _factory(session)


__all__ = ["configure_local_model_store", "local_model_store"]
