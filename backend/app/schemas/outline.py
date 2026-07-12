"""Pydantic schemas for outline planning."""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


OutlineNodeType = Literal["volume", "chapter", "section"]
OutlineStatus = Literal["pending", "in_progress", "completed"]


class OutlineCharacterLinkInput(BaseModel):
    """Character linked to an outline node."""

    character_id: str = Field(..., description="Character ID")
    role_in_scene: Optional[str] = Field(None, max_length=50, description="Role in this outline node")


class OutlineNodeCreate(BaseModel):
    """Schema for creating an outline node."""

    parent_id: Optional[str] = Field(None, description="Parent outline node ID")
    node_type: OutlineNodeType = Field(..., description="volume/chapter/section")
    title: str = Field(..., min_length=1, max_length=200)
    summary: Optional[str] = None
    status: OutlineStatus = "pending"
    sort_order: int = Field(0, ge=0)
    character_ids: list[str] = Field(default_factory=list)
    characters: Optional[list[OutlineCharacterLinkInput]] = None
    metadata: Optional[dict[str, Any]] = None


class OutlineNodeUpdate(BaseModel):
    """Schema for updating an outline node."""

    parent_id: Optional[str] = None
    node_type: Optional[OutlineNodeType] = None
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    summary: Optional[str] = None
    status: Optional[OutlineStatus] = None
    sort_order: Optional[int] = Field(None, ge=0)
    character_ids: Optional[list[str]] = None
    characters: Optional[list[OutlineCharacterLinkInput]] = None
    metadata: Optional[dict[str, Any]] = None


class OutlineReorderItem(BaseModel):
    """Single outline node reorder operation."""

    id: str
    parent_id: Optional[str] = None
    sort_order: int = Field(0, ge=0)


class OutlineReorderRequest(BaseModel):
    """Schema for reordering outline nodes.

    The API accepts either a list of explicit items, or a parent_id plus
    sort_order list for replacing one sibling group's order.
    """

    items: list[OutlineReorderItem] = Field(default_factory=list)
    parent_id: Optional[str] = None
    sort_order: Optional[list[str]] = None


