"""Worldbuilding CRUD and AI expansion endpoints."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..ai.gateway import LLMGateway
from ..core.db_helpers import get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.models import Project, WorldbuildingEntry, WorldbuildingTimeline, WorldbuildingVersion
from ..database.session import get_db
from ..schemas.worldbuilding import (
    WorldbuildingDimension,
    WorldbuildingEntryCreate,
    WorldbuildingEntryResponse,
    WorldbuildingEntryUpdate,
)
from ..services.content_store import delete_project_file, sync_worldbuilding_to_file

router = APIRouter(tags=["worldbuilding"])


DIMENSION_LABELS: dict[str, str] = {
    "geography": "地理",
    "history": "历史",
    "factions": "势力",
    "power_system": "规则体系",
    "races": "种族",
    "culture": "文化",
}


def _get_entry_or_404(db: Session, project_id: str, entry_id: str) -> WorldbuildingEntry:
    entry = (
        db.query(WorldbuildingEntry)
        .filter(WorldbuildingEntry.id == entry_id, WorldbuildingEntry.project_id == project_id)
        .first()
    )
    if not entry:
        raise NotFoundError("世界观条目不存在")
    return entry


def _entry_to_dict(entry: WorldbuildingEntry) -> dict:
    return WorldbuildingEntryResponse.model_validate(entry).model_dump(mode="json")


def _group_entries(entries: list[WorldbuildingEntry], dimension: Optional[str] = None) -> dict:
    visible_dimensions = [dimension] if dimension else list(DIMENSION_LABELS.keys())
    grouped: dict[str, list[dict]] = {key: [] for key in visible_dimensions if key}

    for entry in entries:
        grouped.setdefault(entry.dimension, []).append(_entry_to_dict(entry))

    dimensions = [
        {
            "dimension": key,
            "label": DIMENSION_LABELS.get(key, key),
            "items": grouped.get(key, []),
        }
        for key in visible_dimensions
    ]
    return {
        "dimensions": dimensions,
        "grouped": grouped,
        "total": sum(len(items) for items in grouped.values()),
    }

@router.get("/projects/{project_id}/worldbuilding")
def list_worldbuilding_entries(
    project_id: str,
    dimension: Optional[WorldbuildingDimension] = Query(None, description="按维度过滤"),
    db: Session = Depends(get_db),
):
    """Get worldbuilding entries grouped by dimension."""
    get_project_or_404(db, project_id)

    query = db.query(WorldbuildingEntry).filter(WorldbuildingEntry.project_id == project_id)
    if dimension:
        query = query.filter(WorldbuildingEntry.dimension == dimension)

    entries = (
        query.order_by(
            WorldbuildingEntry.dimension.asc(),
            WorldbuildingEntry.sort_order.asc(),
            WorldbuildingEntry.updated_at.desc(),
        )
        .all()
    )
    return ApiResponse.success(data=_group_entries(entries, dimension))


@router.post("/projects/{project_id}/worldbuilding")
def create_worldbuilding_entry(
    project_id: str,
    payload: WorldbuildingEntryCreate,
    db: Session = Depends(get_db),
):
    """Create a worldbuilding entry."""
    project = get_project_or_404(db, project_id)

    entry = WorldbuildingEntry(project_id=project_id, **payload.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    sync_worldbuilding_to_file(db, project, entry)
    db.commit()
    db.refresh(entry)
    return ApiResponse.success(data=_entry_to_dict(entry), message="世界观条目创建成功")


@router.put("/projects/{project_id}/worldbuilding/{entry_id}")
def update_worldbuilding_entry(
    project_id: str,
    entry_id: str,
    payload: WorldbuildingEntryUpdate,
    db: Session = Depends(get_db),
):
    """Update a worldbuilding entry."""
    project = get_project_or_404(db, project_id)
    entry = _get_entry_or_404(db, project_id, entry_id)

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        raise ValidationError("未提供任何更新字段")

    for field, value in update_data.items():
        setattr(entry, field, value)

    db.commit()
    db.refresh(entry)
    sync_worldbuilding_to_file(db, project, entry)
    db.commit()
    db.refresh(entry)
    return ApiResponse.success(data=_entry_to_dict(entry), message="世界观条目更新成功")


@router.delete("/projects/{project_id}/worldbuilding/{entry_id}")
def delete_worldbuilding_entry(
    project_id: str,
    entry_id: str,
    db: Session = Depends(get_db),
):
    """Delete a worldbuilding entry."""
    project = get_project_or_404(db, project_id)
    entry = _get_entry_or_404(db, project_id, entry_id)
    content_file_path = entry.content_file_path

    db.delete(entry)
    delete_project_file(project, content_file_path)
    db.commit()
    return ApiResponse.success(message="世界观条目已删除")




@router.get("/projects/{project_id}/worldbuilding/{entry_id}/versions")
def list_worldbuilding_versions(project_id: str, entry_id: str, db: Session = Depends(get_db)):
    """Get worldbuilding version history."""
    get_project_or_404(db, project_id)
    entry = _get_entry_or_404(db, project_id, entry_id)
    versions = (
        db.query(WorldbuildingVersion)
        .filter(WorldbuildingVersion.entry_id == entry.id)
        .order_by(WorldbuildingVersion.version_number.desc(), WorldbuildingVersion.created_at.desc())
        .all()
    )
    return ApiResponse.success(data={
        "items": [
            {
                "id": item.id,
                "entry_id": item.entry_id,
                "version_number": item.version_number,
                "change_summary": item.change_summary,
                "source_chapter_id": item.source_chapter_id,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in versions
        ],
        "total": len(versions),
    })


@router.get("/projects/{project_id}/worldbuilding/{entry_id}/timeline")
def list_worldbuilding_timeline(project_id: str, entry_id: str, db: Session = Depends(get_db)):
    """Get worldbuilding timeline entries."""
    get_project_or_404(db, project_id)
    entry = _get_entry_or_404(db, project_id, entry_id)
    events = (
        db.query(WorldbuildingTimeline)
        .filter(WorldbuildingTimeline.entry_id == entry.id)
        .order_by(WorldbuildingTimeline.sort_order.asc(), WorldbuildingTimeline.created_at.asc())
        .all()
    )
    return ApiResponse.success(data={
        "items": [
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
        ],
        "total": len(events),
    })
