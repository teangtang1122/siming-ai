"""Chapter and version-history application port."""
from __future__ import annotations

from typing import Any, Protocol

from .results import StoryMutation


class ChapterWorkspace(Protocol):
    def list(self, project_id: str) -> dict: ...

    def detail(self, project_id: str, chapter_id: str) -> dict: ...

    def create(self, project_id: str, payload: dict[str, Any]) -> StoryMutation: ...

    def save(
        self, project_id: str, chapter_id: str, payload: dict[str, Any]
    ) -> StoryMutation: ...

    def delete(self, project_id: str, chapter_id: str) -> StoryMutation: ...

    def snapshots(self, project_id: str, chapter_id: str) -> dict: ...

    def snapshot(self, project_id: str, chapter_id: str, snapshot_id: str) -> dict: ...

    def diff(
        self,
        project_id: str,
        chapter_id: str,
        from_snapshot_id: str,
        to_snapshot_id: str,
    ) -> dict: ...

    def restore(
        self, project_id: str, chapter_id: str, snapshot_id: str
    ) -> StoryMutation: ...

    def create_narrative_checkpoint(
        self,
        project_id: str,
        *,
        chapter_id: str | None,
        label: str,
        trigger_type: str,
    ) -> dict: ...


__all__ = ["ChapterWorkspace"]
