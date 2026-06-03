"""Schemas for skill management."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SkillCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="技能名称")
    description: Optional[str] = Field(None, description="技能描述")
    trigger_examples: list[str] = Field(default_factory=list, description="触发关键词列表")
    system_prompt: str = Field(..., min_length=1, description="技能系统提示词")
    recommended_tools: list[str] = Field(default_factory=list, description="推荐使用的工具（信息性）")
    scope: str = Field("global", description="适用范围: global|project|writing|outline|characters|worldbuilding|cataloging|research")
    priority: int = Field(0, ge=0, le=100, description="优先级，越高越先注入")
    enabled: bool = Field(True, description="是否启用")


class SkillUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    trigger_examples: Optional[list[str]] = None
    system_prompt: Optional[str] = Field(None, min_length=1)
    recommended_tools: Optional[list[str]] = None
    scope: Optional[str] = None
    priority: Optional[int] = Field(None, ge=0, le=100)
    enabled: Optional[bool] = None


class SkillResponse(BaseModel):
    id: str
    project_id: str
    builtin_key: Optional[str] = None
    name: str
    description: Optional[str] = None
    trigger_examples: list[str] = []
    system_prompt: str
    recommended_tools: list[str] = []
    scope: str = "global"
    priority: int = 0
    enabled: bool = True
    is_builtin: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SkillDraftRequest(BaseModel):
    requirements: str = Field(..., min_length=1, description="用户想创建的技能需求")
    template_key: Optional[str] = Field(None, description="可选模板 key")
    scope: str = Field("global", description="期望适用范围")


class SkillMatchPreviewRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用于测试触发的用户消息")
    scope: str = Field("project", description="助手所在范围")
    candidate: Optional[SkillCreate] = Field(None, description="未保存的技能草案，可用于预览触发效果")


class SkillVersionResponse(BaseModel):
    id: str
    skill_id: str
    project_id: str
    title: str
    change_summary: Optional[str] = None
    snapshot: dict
    created_at: datetime
