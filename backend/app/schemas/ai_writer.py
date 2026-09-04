"""Pydantic schemas for AI writing engine endpoints."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.story.domain.outline_contract import OUTLINE_PROPOSAL_MAX_NODES
from app.services.conversation_context import ReferenceContext


class MobileProviderEnvelope(BaseModel):
    """End-to-end encrypted Android-owned provider configuration."""

    version: Literal[1] = 1
    ephemeral_public_key: str = Field(min_length=40, max_length=64)
    nonce: str = Field(min_length=16, max_length=32)
    ciphertext: str = Field(min_length=32, max_length=100_000)


class WorkspaceAssistantRequest(BaseModel):
    """Conversational assistant for a project workspace."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1, max_length=1_000_000)
    scope: Literal["project"] | None = Field(
        None,
        description="Deprecated project-scope marker retained for typed client compatibility",
    )
    conversation_id: str | None = None
    canonical_conversation_id: str | None = Field(
        None,
        description="Canonical project conversation ID used to reuse the internal execution thread",
    )
    creation_session_id: str | None = Field(
        None,
        description="Typed source-session reference; never used as workspace conversation history",
    )
    selected_text: str | None = Field(None, description="User-selected text in the editor")
    selected_text_chapter_id: str | None = Field(
        None, description="Chapter ID the selected text belongs to"
    )
    active_chapter_draft_id: str | None = Field(
        None,
        description="Current pending editor draft ID; context only and never an intent override",
    )
    reference_context: ReferenceContext | None = Field(
        None,
        description="Typed data-only reference material for the current exact author message",
    )
    model: str | None = None
    temperature: float | None = Field(0.3, ge=0.0, le=2.0)
    max_tokens: int | None = Field(None, ge=1)
    outline_batch_count: int = Field(
        3,
        ge=1,
        le=OUTLINE_PROPOSAL_MAX_NODES,
        description="Preferred number of consecutive outline chapters to plan",
    )
    local_cli_read_permission_grant: Literal["none", "read_once"] = Field(
        "none",
        description="One-turn consent to snapshot explicitly named local paths for OpenCode",
    )
    local_cli_read_paths: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Absolute files/directories explicitly confirmed by the user for this turn",
    )
    model_route: Literal["pc", "mobile"] = "pc"
    mobile_provider: MobileProviderEnvelope | None = Field(
        None,
        repr=False,
        exclude=True,
        description="Encrypted, request-only provider credentials from a paired Android device",
    )

    @model_validator(mode="after")
    def require_mobile_provider_envelope(self):
        if self.model_route == "mobile" and self.mobile_provider is None:
            raise ValueError("选择手机模型线路时必须提供加密凭据")
        if self.model_route == "pc" and self.mobile_provider is not None:
            raise ValueError("PC 模型线路不能携带手机模型凭据")
        return self


class WorkspaceAssistantRunResponse(BaseModel):
    """Stable public contract for one durable workspace-assistant run."""

    run_id: str
    operation_id: str | None = None
    actual_model: str | None = None
    status: str

    # Compatibility aliases retained for pre-3.1 clients.
    id: str
    model: str | None = None

    project_id: str
    conversation_id: str | None = None
    canonical_conversation_id: str | None = Field(
        None,
        description="Canonical project conversation ID used to reuse the internal execution thread",
    )
    assistant_message_id: str | None = None
    phase: str | None = None
    scope: str | None = None
    current_iteration: int = 0
    error: str | None = None
    error_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class WorkspaceAssistantRunStepResponse(BaseModel):
    id: str
    run_id: str
    step_type: str
    tool: str | None = None
    status: str
    iteration: int = 0
    detail: str | None = None
    error: str | None = None
    attempt_no: int = 1
    retry_of_step_id: str | None = None
    resolved_step_id: str | None = None
    idempotency_key: str | None = None
    can_retry: bool = False
    retry_block_reason: str | None = None
    request_sha256: str | None = None
    request_bytes: int = 0
    result_sha256: str | None = None
    result_bytes: int = 0
    resource_refs: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    request: Any = None
    result: Any = None


class WorkspaceAssistantRunListResponse(BaseModel):
    items: list[WorkspaceAssistantRunResponse]
    total: int


class WorkspaceAssistantRunDetailResponse(BaseModel):
    run: WorkspaceAssistantRunResponse
    assistant_message: dict[str, Any] | None = None
    steps: list[WorkspaceAssistantRunStepResponse]
