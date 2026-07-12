"""Pydantic schemas for novel creation sessions."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Any

from pydantic import BaseModel, Field


class NovelCreationSessionCreate(BaseModel):
    """Schema for creating a novel creation session."""
    mode: str = Field(default="internal_llm", description="internal_llm|external_agent")
    user_brief: Optional[str] = Field(default=None, description="User's novel brief")
    target_audience: Optional[str] = None
    genre: Optional[str] = None
    platform: Optional[str] = None
    preset_id: Optional[str] = None
    theme_id: Optional[str] = None
    target_words: Optional[int] = None
    target_chapters: Optional[int] = None


class NovelCreationSessionRead(BaseModel):
    """Schema for reading a novel creation session."""
    id: str
    source_project_id: Optional[str] = None
    created_project_id: Optional[str] = None
    status: str
    mode: str
    user_brief: Optional[str] = None
    target_audience: Optional[str] = None
    genre: Optional[str] = None
    platform: Optional[str] = None
    schema_version: int = 1
    current_stage: Optional[str] = None
    revision: int = 0
    blueprint_json: Optional[Any] = None
    review_json: Optional[Any] = None
    draft_json: Optional[Any] = None
    checkpoints_json: Optional[Any] = None
    last_error_json: Optional[Any] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True
