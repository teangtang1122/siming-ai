"""SQLAlchemy worldbuilding application adapter."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....core.db_helpers import get_project_or_404
from ....core.exceptions import NotFoundError, ValidationError
from ....database.models import WorldbuildingTimeline, WorldbuildingVersion
from ..application.results import StoryMutation
from ..domain.content_sync import ContentSyncIntent, ContentSyncTarget
from .entities import WorldbuildingEntry

DIMENSION_LABELS = {
    "geography": "地理",
    "history": "历史",
    "factions": "势力",
    "power_system": "规则体系",
    "races": "种族",
    "culture": "文化",
}


def _entry_data(entry: WorldbuildingEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "project_id": entry.project_id,
        "dimension": entry.dimension,
        "title": entry.title,
        "content": entry.content,
        "status": entry.status,
        "confidence": entry.confidence,
        "first_seen_chapter_id": entry.first_seen_chapter_id,
        "last_updated_chapter_id": entry.last_updated_chapter_id,
        "sort_order": entry.sort_order,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


class SqlAlchemyWorldbuildingWorkspace:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _entry(self, project_id: str, entry_id: str) -> WorldbuildingEntry:
        entry = (
            self._session.query(WorldbuildingEntry)
            .filter(
                WorldbuildingEntry.id == entry_id,
                WorldbuildingEntry.project_id == project_id,
            )
            .first()
        )
        if not entry:
            raise NotFoundError("世界观条目不存在")
        return entry

    def list(self, project_id: str, dimension: str | None = None) -> dict:
        get_project_or_404(self._session, project_id)
        query = self._session.query(WorldbuildingEntry).filter(
            WorldbuildingEntry.project_id == project_id
        )
        if dimension:
            query = query.filter(WorldbuildingEntry.dimension == dimension)
        entries = (
            query.order_by(
                WorldbuildingEntry.dimension.asc(),
                WorldbuildingEntry.sort_order.asc(),
                WorldbuildingEntry.updated_at.desc(),
            ).all()
        )
        visible = [dimension] if dimension else list(DIMENSION_LABELS)
        grouped: dict[str, list[dict]] = {key: [] for key in visible if key}
        for entry in entries:
            grouped.setdefault(entry.dimension, []).append(_entry_data(entry))
        return {
            "dimensions": [
                {
                    "dimension": key,
                    "label": DIMENSION_LABELS.get(key, key),
                    "items": grouped.get(key, []),
                }
                for key in visible
            ],
            "grouped": grouped,
            "total": sum(len(items) for items in grouped.values()),
        }

    def create(self, project_id: str, payload: dict[str, Any]) -> StoryMutation:
        get_project_or_404(self._session, project_id)
        entry = WorldbuildingEntry(project_id=project_id, **payload)
        self._session.add(entry)
        self._session.flush()
        return StoryMutation(
            data=_entry_data(entry),
            sync_intents=[
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.WORLD_BUILDING,
                    entity_id=entry.id,
                )
            ],
        )

    def update(
        self, project_id: str, entry_id: str, payload: dict[str, Any]
    ) -> StoryMutation:
        get_project_or_404(self._session, project_id)
        entry = self._entry(project_id, entry_id)
        if not payload:
            raise ValidationError("未提供任何更新字段")
        for field, value in payload.items():
            setattr(entry, field, value)
        self._session.flush()
        return StoryMutation(
            data=_entry_data(entry),
            sync_intents=[
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.WORLD_BUILDING,
                    entity_id=entry.id,
                )
            ],
        )

    def delete(self, project_id: str, entry_id: str) -> StoryMutation:
        project = get_project_or_404(self._session, project_id)
        entry = self._entry(project_id, entry_id)
        content_file_path = entry.content_file_path
        self._session.delete(entry)
        self._session.flush()
        return StoryMutation(
            sync_intents=[
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.FILE_DELETE,
                    entity_id=entry_id,
                    payload={
                        "folder_path": project.folder_path,
                        "relative_path": content_file_path,
                    },
                ),
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.WORLD_BUILDING_RELATIONSHIPS,
                ),
            ]
        )

    def versions(self, project_id: str, entry_id: str) -> dict:
        get_project_or_404(self._session, project_id)
        entry = self._entry(project_id, entry_id)
        versions = (
            self._session.query(WorldbuildingVersion)
            .filter(WorldbuildingVersion.entry_id == entry.id)
            .order_by(
                WorldbuildingVersion.version_number.desc(),
                WorldbuildingVersion.created_at.desc(),
            )
            .all()
        )
        items = [
            {
                "id": item.id,
                "entry_id": item.entry_id,
                "version_number": item.version_number,
                "change_summary": item.change_summary,
                "source_chapter_id": item.source_chapter_id,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in versions
        ]
        return {"items": items, "total": len(items)}

    def timeline(self, project_id: str, entry_id: str) -> dict:
        get_project_or_404(self._session, project_id)
        entry = self._entry(project_id, entry_id)
        events = (
            self._session.query(WorldbuildingTimeline)
            .filter(WorldbuildingTimeline.entry_id == entry.id)
            .order_by(
                WorldbuildingTimeline.sort_order.asc(),
                WorldbuildingTimeline.created_at.asc(),
            )
            .all()
        )
        items = [
            {
                "id": item.id,
                "entry_id": item.entry_id,
                "chapter_id": item.chapter_id,
                "event_description": item.event_description,
                "event_type": item.event_type,
                "evidence": item.evidence,
                "sort_order": item.sort_order,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in events
        ]
        return {"items": items, "total": len(items)}


__all__ = ["SqlAlchemyWorldbuildingWorkspace"]
