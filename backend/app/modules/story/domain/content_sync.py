"""Domain vocabulary for database-authoritative content mirror updates."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ContentSyncTarget(StrEnum):
    """A stable mirror projection that can be rebuilt from database state."""

    PROJECT_MANIFEST = "project_manifest"
    PROJECT = "project"
    CHAPTER = "chapter"
    CHARACTER = "character"
    WORLD_BUILDING = "worldbuilding"
    OUTLINE = "outline"
    CHARACTER_RELATIONSHIPS = "character_relationships"
    WORLD_BUILDING_RELATIONSHIPS = "worldbuilding_relationships"
    FILE_DELETE = "file_delete"
    PROJECT_DELETE = "project_delete"


@dataclass(frozen=True, slots=True)
class ContentSyncIntent:
    """One durable request to update a human-readable project mirror."""

    project_id: str
    target: ContentSyncTarget
    entity_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "story_command"

    @property
    def dedupe_key(self) -> str:
        entity = self.entity_id or self.payload.get("relative_path") or "project"
        return f"{self.project_id}:{self.target.value}:{entity}"


__all__ = ["ContentSyncIntent", "ContentSyncTarget"]
