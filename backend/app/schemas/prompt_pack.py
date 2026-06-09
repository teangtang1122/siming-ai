"""Pydantic schemas for public prompt packs and method cards."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Any

from pydantic import BaseModel, Field


class PublicPromptPackRead(BaseModel):
    """Schema for reading a public prompt pack."""
    id: str
    project_id: Optional[str] = None
    pack_id: str
    version: str
    scope: str
    title: str
    summary: Optional[str] = None
    system_prompt: str
    workflow_json: Optional[Any] = None
    quality_rubric_json: Optional[Any] = None
    tool_playbook_json: Optional[Any] = None
    forbidden_patterns_json: Optional[Any] = None
    context_policy_json: Optional[Any] = None
    output_contract_json: Optional[Any] = None
    enabled: bool
    is_builtin: bool
    tags_json: Optional[Any] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PublicPromptPackCreate(BaseModel):
    """Schema for creating a public prompt pack."""
    pack_id: str = Field(..., min_length=1, max_length=100)
    version: str = Field(default="1.0.0")
    scope: str = Field(..., min_length=1, max_length=50)
    title: str = Field(..., min_length=1, max_length=200)
    summary: Optional[str] = None
    system_prompt: str = Field(..., min_length=1)
    workflow_json: Optional[Any] = None
    quality_rubric_json: Optional[Any] = None
    tool_playbook_json: Optional[Any] = None
    forbidden_patterns_json: Optional[Any] = None
    context_policy_json: Optional[Any] = None
    output_contract_json: Optional[Any] = None
    enabled: bool = True
    tags_json: Optional[Any] = None


class MethodCardRead(BaseModel):
    """Schema for reading a method card."""
    id: str
    project_id: Optional[str] = None
    card_id: str
    version: str
    title: str
    description: Optional[str] = None
    content_json: Any
    card_type: str
    enabled: bool
    is_builtin: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MethodCardCreate(BaseModel):
    """Schema for creating a method card."""
    card_id: str = Field(..., min_length=1, max_length=100)
    version: str = Field(default="1.0.0")
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    content_json: Any
    card_type: str = Field(..., min_length=1, max_length=50)
    enabled: bool = True
