"""Stable failure codes for conversation-context assembly."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ConversationContextErrorCode(StrEnum):
    CAPACITY_UNKNOWN = "conversation_capacity_unknown"
    CURRENT_USER_OVER_CAPACITY = "current_user_message_over_capacity"
    CHECKPOINT_REQUIRED = "conversation_checkpoint_required"
    CHECKPOINT_FAILED = "conversation_checkpoint_failed"
    CHECKPOINT_CANCELLED = "conversation_checkpoint_cancelled"
    CHECKPOINT_SUPERSEDED = "conversation_checkpoint_superseded"
    SOURCE_CHANGED = "conversation_source_changed"
    REQUIRED_STATE_OVER_CAPACITY = "conversation_required_state_over_capacity"
    PROTOCOL_INVALID = "conversation_protocol_invalid"
    ORPHAN_TOOL_RESULT = "orphan_tool_result"
    INCOMPLETE_TOOL_TRANSACTION = "incomplete_tool_transaction"
    TOOL_CAPABILITY_UNAVAILABLE = "tool_capability_unavailable"
    TOOL_RESULT_OVER_CAPACITY = "tool_result_over_capacity"
    PROVIDER_MAPPING_FAILED = "provider_message_mapping_failed"
    FINAL_REQUEST_OVER_CAPACITY = "final_agent_request_over_capacity"


class ConversationContextError(ValueError):
    """Pure-domain error safe for mapping to HTTP, SSE, or Android errors."""

    def __init__(
        self,
        code: ConversationContextErrorCode | str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = ConversationContextErrorCode(code)
        self.details = dict(details or {})
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": str(self),
            "details": self.details,
        }


__all__ = ["ConversationContextError", "ConversationContextErrorCode"]
