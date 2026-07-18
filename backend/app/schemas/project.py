"""Pydantic schemas for Project (作品)."""
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime


class ProjectBase(BaseModel):
    """Base project fields."""
    title: str = Field(..., min_length=1, max_length=200, description="作品标题")
    description: Optional[str] = Field(None, description="作品简介")
    tags: Optional[list[str]] = Field(None, description="类型标签列表")
    narrative_perspective: Optional[str] = Field("third_person", description="叙事视角")
    writing_style: Optional[str] = Field("natural", description="文风偏好")
    forbidden_sentence_patterns: Optional[str] = Field(None, description="禁用句式，每行一条")
    rhetoric_guidelines: Optional[str] = Field(None, description="修辞与比喻使用限制")
    short_sentences: bool = Field(False, description="短句模式：以短句为主，减少长句和从句嵌套")
    custom_style_prompt: Optional[str] = Field(None, description="自定义风格提示词，会追加到所有AI文案生成中")
    daily_word_goal: Optional[int] = Field(6000, ge=0, description="每日字数目标")


class ProjectCreate(ProjectBase):
    """Schema for creating a project."""
    pass


class ProjectUpdate(BaseModel):
    """Schema for updating a project."""
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    narrative_perspective: Optional[str] = None
    writing_style: Optional[str] = None
    forbidden_sentence_patterns: Optional[str] = None
    rhetoric_guidelines: Optional[str] = None
    short_sentences: Optional[bool] = None
    custom_style_prompt: Optional[str] = None
    daily_word_goal: Optional[int] = Field(None, ge=0)


class ProjectResponse(BaseModel):
    """Schema for project response."""
    id: str
    title: str
    description: Optional[str]
    tags: Optional[str]  # stored as JSON string in DB
    narrative_perspective: str
    writing_style: str
    forbidden_sentence_patterns: Optional[str]
    rhetoric_guidelines: Optional[str]
    short_sentences: bool
    custom_style_prompt: Optional[str]
    daily_word_goal: int
    storage_mode: Optional[str] = None
    folder_path: Optional[str] = None
    content_migrated_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectListItem(BaseModel):
    """Schema for project list item."""
    id: str
    title: str
    description: Optional[str]
    tags: Optional[str]
    storage_mode: Optional[str] = None
    folder_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectListData(BaseModel):
    """Typed payload returned by the project collection endpoint."""

    items: list[ProjectListItem]
    total: int
