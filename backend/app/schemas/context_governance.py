"""Request schemas for auditable task-context governance."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ContextManifestPrepare(BaseModel):
    task_type: str = Field(..., min_length=1, max_length=50)
    model: str | None = Field(None, max_length=300)
    execution_route: str = Field("internal_api", max_length=50)
    arguments: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = Field(None, max_length=100)
    pinned_chunk_ids: list[str] = Field(default_factory=list, max_length=100)
    pinned_source_ids: list[str] = Field(default_factory=list, max_length=100)


class ContextManifestOverride(BaseModel):
    reason: str = Field(..., min_length=1, max_length=4000)
    actor: str = Field("author", min_length=1, max_length=100)


class ContextEvidenceSubmission(BaseModel):
    sources: list[dict[str, Any]] = Field(default_factory=list, max_length=50)


class ContextSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=12000)
    limit: int = Field(12, ge=1, le=40)


class ModelContextProfilePayload(BaseModel):
    provider: str = Field(..., min_length=1, max_length=80)
    model_name: str = Field(..., min_length=1, max_length=200)
    context_window_tokens: int = Field(..., ge=2048, le=10_000_000)
    max_output_tokens: int | None = Field(None, ge=1, le=10_000_000)
    safety_margin_tokens: int = Field(512, ge=0, le=100_000)
    enabled: bool = True


class ContextRebuildRequest(BaseModel):
    project_ids: list[str] | None = Field(None, max_length=1000)
    requested_by: str = Field("author", max_length=100)
