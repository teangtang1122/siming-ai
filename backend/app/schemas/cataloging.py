"""Schemas for project cataloging."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


CatalogingMode = Literal["auto", "manual"]
CandidateStatus = Literal["pending", "edited", "approved", "rejected", "applying", "applied", "apply_failed"]
ManualCandidateStatus = Literal["pending", "edited", "approved", "rejected"]


class CatalogingStartRequest(BaseModel):
    execution_mode: CatalogingMode = "auto"
    model: Optional[str] = Field(None, description="Optional model override")
    chapter_ids: list[str] = Field(default_factory=list, description="Optional ordered chapter IDs")


class CatalogingModeUpdate(BaseModel):
    execution_mode: CatalogingMode


class CatalogingCandidateUpdate(BaseModel):
    payload: Optional[dict[str, Any]] = None
    status: Optional[CandidateStatus] = None


class CatalogingCandidateCreate(BaseModel):
    chapter_run_id: Optional[str] = None
    item_type: str
    payload: dict[str, Any]
    status: ManualCandidateStatus = "edited"
    target_name: Optional[str] = None
    confidence: Optional[float] = None
    evidence: Optional[str] = None


class CatalogingCandidateBulkUpdate(BaseModel):
    candidate_ids: list[str] = Field(default_factory=list)
    status: CandidateStatus


class CatalogingJobResponse(BaseModel):
    id: str
    project_id: str
    status: str
    execution_mode: str
    execution_backend: str = "internal_llm"
    agent_run_id: Optional[str] = None
    current_chapter_id: Optional[str]
    last_completed_chapter_id: Optional[str]
    blocked_chapter_id: Optional[str]
    context_integrity: str
    total_chapters: int
    completed_chapters: int
    failed_chapters: int
    model: Optional[str]
    effective_model: Optional[str] = None
    model_source: Optional[str] = None
    provider: Optional[str] = None
    error: Optional[str]
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]

    model_config = {"from_attributes": True, "protected_namespaces": ()}
