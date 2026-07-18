"""Composition boundary for assistant workspace persistence."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..application.workspace import AssistantWorkspace

AssistantWorkspaceFactory = Callable[[Any], AssistantWorkspace]
_factory: AssistantWorkspaceFactory | None = None


def configure_assistant_workspace(factory: AssistantWorkspaceFactory) -> None:
    global _factory
    _factory = factory


def assistant_workspace(session: Any) -> AssistantWorkspace:
    if _factory is None:
        raise RuntimeError("Assistant workspace persistence has not been configured")
    return _factory(session)


__all__ = ["assistant_workspace", "configure_assistant_workspace"]
