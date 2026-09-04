"""Pydantic schemas for worldbuilding entries."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


WorldbuildingDimension = Literal[
    "geography",
    "history",
    "factions",
    "power_system",
    "races",
    "culture",
]
WorldbuildingStatus = Literal["active", "superseded", "archived", "draft"]


class WorldbuildingEntryCreate(BaseModel):
    """Schema for creating a worldbuilding entry."""

    dimension: WorldbuildingDimension = Field(..., description="世界观维度")
    title: str = Field(..., min_length=1, max_length=200, description="条目标题")
    content: str = Field(..., min_length=1, description="条目内容")
    sort_order: int = Field(0, ge=0, description="排序值")


class WorldbuildingEntryUpdate(BaseModel):
    """Schema for updating a worldbuilding entry."""

    dimension: Optional[WorldbuildingDimension] = None
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    content: Optional[str] = Field(None, min_length=1)
    sort_order: Optional[int] = Field(None, ge=0)
    status: Optional[WorldbuildingStatus] = None


class WorldbuildingEntryResponse(BaseModel):
    """Worldbuilding entry response."""

    id: str
    project_id: str
    dimension: str
    title: str
    content: str
    status: Optional[str] = None
    confidence: Optional[float] = None
    first_seen_chapter_id: Optional[str] = None
    last_updated_chapter_id: Optional[str] = None
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorldbuildingAIExpandRequest(BaseModel):
    """Schema for AI-assisted worldbuilding expansion."""

    dimension: WorldbuildingDimension = Field(..., description="目标世界观维度")
    concept: str = Field(..., min_length=1, max_length=500, description="用户输入的概念或关键词")
    model: Optional[str] = Field(None, description="可选模型标识，例如 openai:gpt-4o")
