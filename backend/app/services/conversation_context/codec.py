"""Strict decoders for persisted or cross-platform context protocol JSON."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .budget import RequestBudgetEnvelope
from .context_frame import ContextFrame, ContextFrameIntegrity
from .contracts import (
    AuthorQuote,
    ConversationCheckpoint,
    ConversationIdentity,
    ConversationMessage,
    ConversationTurn,
    ExecutionLedgerEntry,
    GenerationModelBinding,
    ProjectReference,
    ResourceReference,
    SemanticNavigation,
    SourceRange,
    SystemContract,
)
from .tool_transactions import (
    NativeToolCall,
    NativeToolResult,
    ToolExecutionReceipt,
    ToolTransaction,
)


def checkpoint_from_dict(payload: Mapping[str, Any]) -> ConversationCheckpoint:
    navigation = _mapping(payload.get("semantic_navigation"), "semantic_navigation")
    source_range = _mapping(payload.get("source_range"), "source_range")
    return ConversationCheckpoint(
        schema=str(payload.get("schema") or ""),
        scope=str(payload.get("scope") or ""),
        conversation_id=str(payload.get("conversation_id") or ""),
        source_range=SourceRange(**source_range),
        semantic_navigation=SemanticNavigation(
            authority=str(navigation.get("authority") or "non_authoritative_navigation"),
            current_objectives=tuple(navigation.get("current_objectives") or ()),
            resolved_decisions=tuple(navigation.get("resolved_decisions") or ()),
            superseded_directions=tuple(navigation.get("superseded_directions") or ()),
            unresolved_questions=tuple(navigation.get("unresolved_questions") or ()),
            next_context_needed=tuple(navigation.get("next_context_needed") or ()),
        ),
        author_quotes=tuple(
            AuthorQuote(**_mapping(item, "author_quote"))
            for item in payload.get("author_quotes") or ()
        ),
        execution_ledger=tuple(
            _execution_entry_from_dict(_mapping(item, "execution_ledger"))
            for item in payload.get("execution_ledger") or ()
        ),
        project_refs=tuple(
            ProjectReference(**_mapping(item, "project_ref"))
            for item in payload.get("project_refs") or ()
        ),
        warnings=tuple(payload.get("warnings") or ()),
        segment_ids=tuple(payload.get("segment_ids") or ()),
        policy_version=int(payload.get("policy_version") or 0),
    )


def context_frame_from_dict(payload: Mapping[str, Any]) -> ContextFrame:
    conversation = _mapping(payload.get("conversation"), "conversation")
    binding = _mapping(payload.get("model_binding"), "model_binding")
    system = _mapping(payload.get("system_contract"), "system_contract")
    integrity = _mapping(payload.get("integrity"), "integrity")
    budget = _mapping(payload.get("budget"), "budget")
    checkpoint_payload = payload.get("checkpoint")
    return ContextFrame(
        schema=str(payload.get("schema") or ""),
        conversation=ConversationIdentity(**conversation),
        model_binding=GenerationModelBinding(**binding),
        system_contract=SystemContract(**system),
        checkpoint=(
            checkpoint_from_dict(_mapping(checkpoint_payload, "checkpoint"))
            if checkpoint_payload is not None
            else None
        ),
        recent_turns=tuple(
            _turn_from_dict(_mapping(item, "recent_turn"))
            for item in payload.get("recent_turns") or ()
        ),
        current_user_message=_message_from_dict(
            _mapping(payload.get("current_user_message"), "current_user_message")
        ),
        current_turn_ledger=tuple(
            ToolExecutionReceipt(**_mapping(item, "current_turn_ledger"))
            for item in payload.get("current_turn_ledger") or ()
        ),
        pending_tool_transactions=tuple(
            _transaction_from_dict(_mapping(item, "pending_tool_transaction"))
            for item in payload.get("pending_tool_transactions") or ()
        ),
        budget=RequestBudgetEnvelope(**budget),
        integrity=ContextFrameIntegrity(**integrity),
        checkpoint_segments=tuple(
            checkpoint_from_dict(_mapping(item, "checkpoint_segment"))
            for item in payload.get("checkpoint_segments") or ()
        ),
    )


def _message_from_dict(payload: Mapping[str, Any]) -> ConversationMessage:
    return ConversationMessage(
        message_id=str(payload.get("message_id") or ""),
        sequence_no=int(payload.get("sequence_no") or 0),
        role=str(payload.get("role") or ""),
        content=str(payload.get("content") or ""),
        tool_call_id=(
            str(payload["tool_call_id"]) if payload.get("tool_call_id") is not None else None
        ),
        tool_calls=tuple(payload.get("tool_calls") or ()),
    )


def _turn_from_dict(payload: Mapping[str, Any]) -> ConversationTurn:
    return ConversationTurn(
        turn_id=str(payload.get("turn_id") or ""),
        status=str(payload.get("status") or ""),
        messages=tuple(
            _message_from_dict(_mapping(item, "turn_message"))
            for item in payload.get("messages") or ()
        ),
    )


def _execution_entry_from_dict(payload: Mapping[str, Any]) -> ExecutionLedgerEntry:
    return ExecutionLedgerEntry(
        run_id=str(payload.get("run_id") or ""),
        step_id=str(payload.get("step_id") or ""),
        tool=str(payload.get("tool") or ""),
        status=str(payload.get("status") or ""),
        resource_refs=tuple(
            ResourceReference(**_mapping(item, "resource_ref"))
            for item in payload.get("resource_refs") or ()
        ),
        error_code=(str(payload["error_code"]) if payload.get("error_code") is not None else None),
    )


def _transaction_from_dict(payload: Mapping[str, Any]) -> ToolTransaction:
    return ToolTransaction(
        transaction_id=str(payload.get("transaction_id") or ""),
        assistant_message_id=str(payload.get("assistant_message_id") or ""),
        assistant_content=str(payload.get("assistant_content") or ""),
        calls=tuple(
            NativeToolCall(**_mapping(item, "native_tool_call"))
            for item in payload.get("calls") or ()
        ),
        assistant_reasoning_content=str(payload.get("assistant_reasoning_content") or ""),
        assistant_provider_state=tuple(
            dict(_mapping(item, "assistant_provider_state"))
            for item in payload.get("assistant_provider_state") or ()
        ),
        results=tuple(
            NativeToolResult(**_mapping(item, "native_tool_result"))
            for item in payload.get("results") or ()
        ),
        state=str(payload.get("state") or ""),
    )


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


__all__ = ["checkpoint_from_dict", "context_frame_from_dict"]
