"""Public response schemas for durable assistant conversation context."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _PublicContextModel(BaseModel):
    """Drop fields that are not part of the explicit public contract."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ConversationModelBindingResponse(_PublicContextModel):
    provider: str | None = None
    model: str | None = None
    display_name: str | None = None


class ConversationCheckpointSourceRangeResponse(_PublicContextModel):
    first_sequence: int
    last_sequence: int
    message_count: int
    source_hash: str


class ConversationContextStateResponse(_PublicContextModel):
    status: Literal["ready", "compressing", "failed"]
    policy_version: int
    active_checkpoint_id: str | None = None
    latest_checkpoint_id: str | None = None
    source_message_count: int
    recent_exact_turn_count: int
    original_history_tokens: int
    active_history_tokens: int
    trigger: str
    capacity_assurance: Literal["exact", "conservative", "unverified"]
    provider: str | None = None
    model: str | None = None
    model_binding: ConversationModelBindingResponse | None = None
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_detail: str | None = None
    retryable: bool
    updated_at: str | None = None


class ConversationCheckpointSummaryResponse(_PublicContextModel):
    id: str
    status: Literal[
        "pending",
        "compressing",
        "ready",
        "failed",
        "cancelled",
        "superseded",
    ]
    policy_version: int
    schema_version: str
    scope: Literal["workspace", "creation"]
    source_range: ConversationCheckpointSourceRangeResponse
    source_message_count: int
    original_history_tokens: int | None = None
    checkpoint_tokens: int | None = None
    model_binding: ConversationModelBindingResponse | None = None
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_detail: str | None = None
    retryable: bool
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None


class ConversationCheckpointDetailResponse(ConversationContextStateResponse):
    id: str
    status: Literal[
        "pending",
        "compressing",
        "ready",
        "failed",
        "cancelled",
        "superseded",
    ]
    schema_version: str
    checkpoint_schema: str | None = Field(
        default=None,
        validation_alias="schema",
        serialization_alias="schema",
    )
    scope: Literal["workspace", "creation"]
    source_range: ConversationCheckpointSourceRangeResponse
    original_tokens: int | None = None
    original_history_tokens: int | None = None
    checkpoint_tokens: int | None = None
    semantic_navigation: dict[str, Any] = Field(default_factory=dict)
    author_quotes: list[dict[str, Any]] = Field(default_factory=list)
    execution_ledger: list[dict[str, Any]] = Field(default_factory=list)
    project_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str | None = None
    completed_at: str | None = None


class ConversationCheckpointListResponse(_PublicContextModel):
    items: list[ConversationCheckpointSummaryResponse]
    total: int


class TranscriptImportResponse(_PublicContextModel):
    conversation_id: str
    transcript_revision: int
    applied_revision: int
    imported_message_count: int
    idempotent: bool


__all__ = [
    "ConversationCheckpointDetailResponse",
    "ConversationCheckpointListResponse",
    "ConversationCheckpointSourceRangeResponse",
    "ConversationCheckpointSummaryResponse",
    "ConversationContextStateResponse",
    "ConversationModelBindingResponse",
    "TranscriptImportResponse",
]
