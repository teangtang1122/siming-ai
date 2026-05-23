"""Pydantic schemas for character management."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CharacterBase(BaseModel):
    """Shared character fields."""

    name: str = Field(..., min_length=1, max_length=100, description="角色名称")
    appearance: Optional[str] = Field(None, description="外貌描述")
    personality: Optional[str] = Field(None, description="性格描述")
    background: Optional[str] = Field(None, description="背景故事")
    abilities: Optional[list[str]] = Field(None, description="能力/技能列表")
    role_type: Optional[str] = Field(None, max_length=50, description="角色类型")
    is_evolution_tracked: bool = Field(True, description="是否开启自动追踪")


class CharacterCreate(CharacterBase):
    """Schema for creating a character."""


class CharacterUpdate(BaseModel):
    """Schema for updating a character."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    appearance: Optional[str] = None
    personality: Optional[str] = None
    background: Optional[str] = None
    abilities: Optional[list[str]] = None
    role_type: Optional[str] = Field(None, max_length=50)
    is_evolution_tracked: Optional[bool] = None
    change_summary: Optional[str] = Field(None, description="本次变更摘要")


class CharacterResponse(BaseModel):
    """Character response with parsed abilities."""

    id: str
    project_id: str
    name: str
    appearance: Optional[str]
    personality: Optional[str]
    background: Optional[str]
    abilities: list[str]
    role_type: Optional[str]
    current_version: int
    is_evolution_tracked: bool
    created_at: datetime
    updated_at: datetime


class CharacterVersionItem(BaseModel):
    """Character version list item."""

    id: str
    character_id: str
    version_number: int
    change_summary: Optional[str]
    source_chapter_id: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class RelationshipInput(BaseModel):
    """Relationship from the current character to another character."""

    target_character_id: str = Field(..., description="目标角色ID")
    relationship_type: str = Field(..., min_length=1, max_length=100, description="关系类型")
    description: Optional[str] = Field(None, description="关系描述")


class RelationshipUpdate(BaseModel):
    """Schema for replacing a character's relationships."""

    relationships: list[RelationshipInput] = Field(default_factory=list)


class CharacterAIConfigUpdate(BaseModel):
    """Schema for updating a character's AI dialogue config."""

    model_config = {"protected_namespaces": ()}

    tone_style: Optional[str] = Field(None, max_length=100, description="语气风格")
    catchphrases: Optional[list[str]] = Field(None, description="口头禅列表")
    verbosity: Optional[str] = Field(None, max_length=50, description="话量偏好 brief/moderate/verbose")
    emotion_tendency: Optional[str] = Field(None, max_length=100, description="情感倾向")
    model_override: Optional[str] = Field(None, max_length=200, description="角色专用模型覆盖")
    custom_system_prompt: Optional[str] = Field(None, description="自定义额外系统提示词")


