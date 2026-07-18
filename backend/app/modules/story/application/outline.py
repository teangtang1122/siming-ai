"""Outline application port."""
from __future__ import annotations

from typing import Any, Protocol

from .results import StoryMutation


class OutlineWorkspace(Protocol):
    def read(self, project_id: str) -> dict: ...

    def create(self, project_id: str, payload: dict[str, Any]) -> StoryMutation: ...

    def reorder(self, project_id: str, items: list[dict[str, Any]]) -> StoryMutation: ...

    def update(
        self, project_id: str, node_id: str, payload: dict[str, Any]
    ) -> StoryMutation: ...

    def delete(self, project_id: str, node_id: str) -> StoryMutation: ...


__all__ = ["OutlineWorkspace"]
