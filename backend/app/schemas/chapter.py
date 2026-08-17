"""Pydantic schemas for chapter management and version snapshots."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


SnapshotTrigger = Literal["manual_save", "ai_insert", "de_ai", "restore"]


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


class ChapterReorderRequest(BaseModel):
    """Replace the reading order of all chapters in one project."""

    ids: list[str] = Field(default_factory=list, description="Chapter IDs in reading order")


class ChapterDeAiPreviewRequest(BaseModel):
    """Generate a non-destructive de-AI revision candidate for editor review."""

    content: str = Field(..., min_length=1, max_length=100_000)
    original_content: str | None = Field(
        None,
        min_length=1,
        max_length=100_000,
        description=(
            "Initial source text for a multi-round revision. Required after round 1 "
            "so every round can be audited against the unchanged original."
        ),
    )
    revision_round: int = Field(
        1,
        ge=1,
        le=3,
        description="One-based de-AI treatment round; at most three rounds are supported.",
    )
    model: str | None = Field(
        None,
        max_length=300,
        description="API or local CLI model identity. Falls back to the global default.",
    )

    @model_validator(mode="after")
    def require_original_for_follow_up_round(self):
        if self.revision_round > 1 and self.original_content is None:
            raise ValueError("第 2/3 轮必须同时提交最初原文，防止故事在连续处理时漂移")
        return self


class ChapterQualityScoreRequest(BaseModel):
    """Score the current editor text without modifying the saved chapter."""

    content: str = Field(..., min_length=1, max_length=100_000)
    title: str | None = Field(None, max_length=200)
    model: str | None = Field(
        None,
        max_length=300,
        description="API or local CLI model identity. Falls back to the global default.",
    )


class ChapterListItem(BaseModel):
    """Chapter list item."""

    id: str
    project_id: str
    outline_node_id: Optional[str]
    title: str
    word_count: int
    current_version: int
    sort_order: int
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
