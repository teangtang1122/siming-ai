"""Worldbuilding application port."""
from __future__ import annotations

from typing import Any, Protocol

from .results import StoryMutation


class WorldbuildingWorkspace(Protocol):
    def list(self, project_id: str, dimension: str | None = None) -> dict: ...

    def create(self, project_id: str, payload: dict[str, Any]) -> StoryMutation: ...

    def update(
        self, project_id: str, entry_id: str, payload: dict[str, Any]
    ) -> StoryMutation: ...

    def delete(self, project_id: str, entry_id: str) -> StoryMutation: ...

    def versions(self, project_id: str, entry_id: str) -> dict: ...

    def timeline(self, project_id: str, entry_id: str) -> dict: ...


__all__ = ["WorldbuildingWorkspace"]
