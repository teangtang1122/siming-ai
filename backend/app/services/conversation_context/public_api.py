"""Shared public projections for durable conversation-context REST routes."""

from __future__ import annotations

from typing import Any

from .checkpoint_state import checkpoint_record_payload, context_state_payload
from .runtime_types import ConversationContextStore


def public_context_state_payload(
    *,
    store: ConversationContextStore,
    conversation_kind: str,
    conversation_id: str,
    owner_id: str,
) -> dict[str, Any]:
    return context_state_payload(
        store=store,
        conversation_kind=conversation_kind,
        conversation_id=conversation_id,
        owner_id=owner_id,
    )


def public_checkpoint_detail_payload(
    *,
    store: ConversationContextStore,
    conversation_kind: str,
    conversation_id: str,
    owner_id: str,
    checkpoint_id: str,
) -> dict[str, Any]:
    """Merge one attempt with live budget state without exposing diagnostics."""

    detail = checkpoint_record_payload(
        store=store,
        conversation_kind=conversation_kind,
        conversation_id=conversation_id,
        owner_id=owner_id,
        checkpoint_id=checkpoint_id,
    )
    state = public_context_state_payload(
        store=store,
        conversation_kind=conversation_kind,
        conversation_id=conversation_id,
        owner_id=owner_id,
    )
    original_tokens = detail.get(
        "original_history_tokens",
        detail.get("original_tokens"),
    )
    return {
        **state,
        **detail,
        "status": detail["status"],
        "scope": conversation_kind,
        "schema": detail.get("schema_version"),
        "original_history_tokens": original_tokens,
        "model_binding": detail.get("model_binding") or state.get("model_binding"),
        "retryable": detail.get(
            "retryable",
            detail.get("status") in {"failed", "cancelled", "superseded"},
        ),
    }


def public_checkpoint_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep list responses small and free of semantic/model-generated bodies."""

    fields = (
        "id",
        "status",
        "policy_version",
        "schema_version",
        "scope",
        "source_range",
        "source_message_count",
        "original_history_tokens",
        "checkpoint_tokens",
        "model_binding",
        "warnings",
        "error_code",
        "error_detail",
        "retryable",
        "created_at",
        "updated_at",
        "completed_at",
    )
    return {field: payload.get(field) for field in fields}


def public_checkpoint_list_payload(
    *,
    store: ConversationContextStore,
    conversation_kind: str,
    conversation_id: str,
    owner_id: str,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    records = tuple(
        store.context_checkpoints(
            conversation_kind,
            conversation_id,
            owner_id=owner_id,
        )
    )
    newest = tuple(reversed(records))
    items = [
        public_checkpoint_summary(
            public_checkpoint_detail_payload(
                store=store,
                conversation_kind=conversation_kind,
                conversation_id=conversation_id,
                owner_id=owner_id,
                checkpoint_id=str(record.id),
            )
        )
        for record in newest[offset : offset + limit]
    ]
    return {"items": items, "total": len(records)}


__all__ = [
    "public_checkpoint_detail_payload",
    "public_checkpoint_list_payload",
    "public_checkpoint_summary",
    "public_context_state_payload",
]
