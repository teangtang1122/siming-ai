"""Public response contracts for the operation runtime."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

LifecycleStatus = Literal[
    "draft",
    "queued",
    "running",
    "waiting_user",
    "paused",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]
HealthStatus = Literal["active", "quiet", "suspected_stall", "stalled", "disconnected"]
OutcomeStatus = Literal[
    "completed_with_reply",
    "completed_with_tools",
    "partial_success",
    "empty_response",
    "skipped_preflight",
    "waiting_user",
    "blocked",
    "failed",
    "cancelled",
    "interrupted",
]


class OperationProgressResponse(BaseModel):
    mode: Literal["determinate", "indeterminate"] = "indeterminate"
    current: int | None = None
    total: int | None = None
    percent: int | None = None


class OperationAttentionResponse(BaseModel):
    kind: str | None = None
    title: str | None = None
    message: str | None = None
    action_label: str | None = None
    action_url: str | None = None
    blocking: bool | None = None

    model_config = {"extra": "allow"}


class OperationResultResponse(BaseModel):
    outcome: OutcomeStatus | None = None
    summary: str | None = None
    completed: list[str] = Field(default_factory=list)
    incomplete: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class OperationEventResponse(BaseModel):
    sequence: int
    event_type: str
    status: str
    message: str | None = None
    payload: dict[str, Any] | None = None
    created_at: str | None = None


class OperationResponse(BaseModel):
    id: str
    source_kind: str
    source_id: str | None = None
    project_id: str | None = None
    title: str
    status: LifecycleStatus
    health_status: HealthStatus
    outcome: OutcomeStatus | None = None
    attention: OperationAttentionResponse | None = None
    result: OperationResultResponse | None = None
    result_summary: str | None = None
    phase: str | None = None
    current_message: str | None = None
    progress: OperationProgressResponse
    model_source: str | None = None
    tool_mode: str | None = None
    failure_class: str | None = None
    next_action: str | None = None
    resume_url: str | None = None
    can_pause: bool = False
    can_cancel: bool = False
    can_retry: bool = False
    input_revision: int | None = None
    input_snapshot_hash: str | None = None
    process_metrics: dict[str, Any] | None = None
    elapsed_seconds: int = 0
    heartbeat_at: str | None = None
    last_activity_at: str | None = None
    last_output_at: str | None = None
    last_checkpoint_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    events: list[OperationEventResponse] | None = None

    model_config = {"protected_namespaces": ()}


class OperationListData(BaseModel):
    items: list[OperationResponse]
