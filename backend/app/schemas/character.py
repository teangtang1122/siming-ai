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
    aliases: Optional[list[str]] = Field(None, description="别名/称呼列表")
    role_type: Optional[str] = Field(None, max_length=50, description="角色类型")
    age: Optional[str] = Field(None, max_length=100, description="年龄/时间状态")
    is_evolution_tracked: bool = Field(True, description="是否开启自动追踪")


    life_status: Optional[str] = Field(None, max_length=50, description="当前生死状态")
    current_location: Optional[str] = Field(None, max_length=200, description="当前位置")
    realm_or_level: Optional[str] = Field(None, max_length=200, description="境界/等级")
    physical_state: Optional[str] = Field(None, description="身体情况")
    mental_state: Optional[str] = Field(None, description="心理状态")
    current_goal: Optional[str] = Field(None, description="当前目标")
    active_conflict: Optional[str] = Field(None, description="当前冲突")
    abilities_state: Optional[str] = Field(None, description="能力当前状态")
    items_or_assets: Optional[str] = Field(None, description="持有物/资源")


class CharacterCreate(CharacterBase):
    """Schema for creating a character."""


class CharacterUpdate(BaseModel):
    """Schema for updating a character."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    appearance: Optional[str] = None
    personality: Optional[str] = None
    background: Optional[str] = None
    abilities: Optional[list[str]] = None
    aliases: Optional[list[str]] = None
    role_type: Optional[str] = Field(None, max_length=50)
    age: Optional[str] = Field(None, max_length=100)
    life_status: Optional[str] = Field(None, max_length=50)
    current_location: Optional[str] = Field(None, max_length=200)
    realm_or_level: Optional[str] = Field(None, max_length=200)
    physical_state: Optional[str] = None
    mental_state: Optional[str] = None
    current_goal: Optional[str] = None
    active_conflict: Optional[str] = None
    abilities_state: Optional[str] = None
    items_or_assets: Optional[str] = None
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
    aliases: list[str]
    role_type: Optional[str]
    age: Optional[str]
    life_status: Optional[str]
    current_location: Optional[str]
    realm_or_level: Optional[str]
    physical_state: Optional[str]
    mental_state: Optional[str]
    current_goal: Optional[str]
    active_conflict: Optional[str]
    abilities_state: Optional[str]
    items_or_assets: Optional[str]
    last_seen_chapter_id: Optional[str]
    last_updated_chapter_id: Optional[str]
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


class CharacterMergeRequest(BaseModel):
    """Request for previewing or applying a duplicate-character merge."""

    primary_id: str = Field(..., description="保留的主角色ID")
    secondary_id: str = Field(..., description="被合并的重复角色ID")
    canonical_name: Optional[str] = Field(None, max_length=100, description="合并后的主名称")
    aliases: list[str] = Field(default_factory=list, description="合并后的别名/称呼")
    confidence_reason: Optional[str] = Field(None, description="合并依据")
    background_append: Optional[str] = Field(None, description="补充写入背景的身份合并说明")


class CharacterAIConfigUpdate(BaseModel):
    """Schema for updating a character's AI dialogue config."""

    model_config = {"protected_namespaces": ()}

    tone_style: Optional[str] = Field(None, max_length=100, description="语气风格")
    catchphrases: Optional[list[str]] = Field(None, description="口头禅列表")
    verbosity: Optional[str] = Field(None, max_length=50, description="话量偏好 brief/moderate/verbose")
    emotion_tendency: Optional[str] = Field(None, max_length=100, description="情感倾向")
    model_override: Optional[str] = Field(None, max_length=200, description="角色专用模型覆盖")
    custom_system_prompt: Optional[str] = Field(None, description="自定义额外系统提示词")


