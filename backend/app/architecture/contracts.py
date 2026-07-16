"""Transport-neutral contracts for models, operations, and user attention."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FailureClass(StrEnum):
    authentication = "authentication"
    quota_or_rate_limit = "quota_or_rate_limit"
    timeout = "timeout"
    unavailable = "unavailable"
    invalid_response = "invalid_response"
    tool_unavailable = "tool_unavailable"
    cancelled = "cancelled"
    interrupted = "interrupted"
    unknown = "unknown"


class ModelMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None


class ModelRequest(BaseModel):
    """Provider-independent request accepted by model-runtime ports."""

    model_config = ConfigDict(protected_namespaces=())

    model: str | None = None
    messages: list[ModelMessage]
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_choice: str | dict[str, Any] | None = None
    stream: bool = True
    task_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelEvent(BaseModel):
    """One normalized event from API, CLI, or local model execution."""

    event_type: Literal[
        "started",
        "text_delta",
        "tool_call",
        "tool_result",
        "usage",
        "completed",
        "failed",
    ]
    text: str | None = None
    tool_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ModelResult(BaseModel):
    """Final normalized model result; provider exceptions never leak upward."""

    text: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    finish_reason: str | None = None
    failure_class: FailureClass | None = None
    failure_message: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.failure_class is None


class AttentionRequired(BaseModel):
    kind: str
    title: str
    message: str
    blocking: bool = True
    actions: list[dict[str, Any]] = Field(default_factory=list)


class OperationResult(BaseModel):
    """One result vocabulary for every long-running author workflow."""

    outcome: Literal[
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
    summary: str
    completed: list[str] = Field(default_factory=list)
    incomplete: list[str] = Field(default_factory=list)
    changes: list[dict[str, Any]] = Field(default_factory=list)
    attention: AttentionRequired | None = None
    failure_class: FailureClass | None = None
    next_action: str | None = None
