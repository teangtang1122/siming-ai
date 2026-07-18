"""Project workspace application port."""
from __future__ import annotations

from typing import Any, Literal, Protocol

from .results import StoryMutation


class ProjectWorkspace(Protocol):
    def list(self, query: str | None = None) -> dict: ...

    def get(self, project_id: str) -> dict: ...

    def create(self, payload: dict[str, Any]) -> StoryMutation: ...

    def update(self, project_id: str, payload: dict[str, Any]) -> StoryMutation: ...

    def storage_health(self, project_id: str) -> dict: ...

    async def repair_storage(
        self,
        project_id: str,
        action: Literal["import_orphans", "refresh_mirror"],
    ) -> dict: ...

    def delete(self, project_id: str) -> StoryMutation: ...


__all__ = ["ProjectWorkspace"]
