"""Pydantic schemas for chapter management and version snapshots."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


SnapshotTrigger = Literal["manual_save", "ai_insert", "restore"]


class ChapterCreate(BaseModel):
    """Schema for creating a chapter."""

    title: str = Field(..., min_length=1, max_length=200)
    outline_node_id: Optional[str] = Field(None, description="Linked outline node ID")
    content: str = Field("", description="Chapter body")
    context_manifest_id: Optional[str] = Field(None, description="Auditable AI task context used for generated content")


class ChapterUpdate(BaseModel):
    """Schema for saving a chapter."""

    title: Optional[str] = Field(None, min_length=1, max_length=200)
    outline_node_id: Optional[str] = None
    content: Optional[str] = None
    trigger_type: SnapshotTrigger = "manual_save"
    context_manifest_id: Optional[str] = None


class ChapterListItem(BaseModel):
    """Chapter list item."""

    id: str
    project_id: str
    outline_node_id: Optional[str]
    title: str
    word_count: int
    current_version: int
    outline_title: Optional[str]
    outline_status: Optional[str]
    outline_node_type: Optional[str]
    outline_path: list[str]
    summary_text: Optional[str] = None
    key_events: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ChapterDetail(ChapterListItem):
    """Chapter detail with content."""

    content: str
    snapshot_count: int


class ChapterSnapshotItem(BaseModel):
    """Snapshot list item."""

    id: str
    chapter_id: str
    version_number: int
    word_count: int
    trigger_type: str
    created_at: datetime

    model_config = {"from_attributes": True}
