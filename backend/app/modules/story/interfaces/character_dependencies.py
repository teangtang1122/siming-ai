"""Composition boundary for character profile persistence."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..application.characters import CharacterWorkspace

CharacterWorkspaceFactory = Callable[[Any], CharacterWorkspace]
_factory: CharacterWorkspaceFactory | None = None


def configure_character_workspace(factory: CharacterWorkspaceFactory) -> None:
    global _factory
    _factory = factory


def character_workspace(session: Any) -> CharacterWorkspace:
    if _factory is None:
        raise RuntimeError("Character workspace has not been configured")
    return _factory(session)


__all__ = ["character_workspace", "configure_character_workspace"]
