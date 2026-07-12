from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class GovernanceItemPayload(BaseModel):
    type: str
    data: dict[str, Any]


class GovernanceStatusUpdate(BaseModel):
    status: Literal["open", "fulfilled", "resolved", "deferred", "abandoned", "invalidated", "pending_review"]
    target_chapter_id: Optional[str] = None
    target_chapter_number: Optional[int] = Field(None, ge=1)
    resolved_chapter_id: Optional[str] = None
    evidence: Optional[str] = None


class GovernanceCandidateBatch(BaseModel):
    chapter_id: Optional[str] = None
    mode: Literal["preview", "apply"] = "preview"
    candidates: list[dict[str, Any]] = Field(default_factory=list)


class CheckpointCreate(BaseModel):
    chapter_id: Optional[str] = None
    label: Optional[str] = None
    trigger_type: str = "manual"
