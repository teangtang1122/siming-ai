"""Validation and context-neutral records for Creation Agent turns.

The durable system conversation is the transcript authority.  A completed
Creation Agent trace is used only to prove that the matching visible
user/assistant pair belongs to one closed creation turn; historical tool
protocol is deliberately not projected into the next model request.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.services.conversation_context.canonical import canonical_sha256
from app.services.conversation_context.checkpoint_validator import (
    CheckpointSourceMessage,
    checkpoint_source_hash,
)
from app.services.conversation_context.contracts import (
    ConversationMessage,
    ConversationRole,
    ConversationTurn,
    ExecutionLedgerEntry,
    ResourceReference,
    TurnStatus,
)
from app.services.conversation_context.errors import (
    ConversationContextError,
    ConversationContextErrorCode,
)
from app.services.conversation_context.execution_ledger import fold_execution_ledger
from app.services.conversation_context.tool_transactions import ToolExecutionReceipt

CREATION_AGENT_TURN_SCHEMA = "creation_agent_turn.v1"
CREATION_AGENT_RUNTIME_SCHEMA = "creation_agent_runtime.v1"


@dataclass(frozen=True)
class CreationConversationTurnRecord:
    """One complete, human-visible turn suitable for ContextFrame assembly."""

    turn_id: str
    user_message_id: str
    assistant_message_id: str
    sequence: int
    user_content: str
    assistant_content: str
    status: str
    source_hash: str


@dataclass(frozen=True)
class CreationExecutionLedgerProjection:
    """Trusted cross-turn execution navigation derived from sealed receipts."""

    entries: tuple[ExecutionLedgerEntry, ...]
    source_hashes: dict[str, str]


def verified_mcp_execution_receipt(
    *,
    session_id: str,
    turn_execution_id: str,
    result: dict[str, Any],
) -> ToolExecutionReceipt | None:
    """Seal the server's exact direct-MCP revision proof as one receipt."""

    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    revision_after = data.get("revision_after")
    if (
        result.get("tool") != "mcp_verified_write"
        or result.get("status") != "ok"
        or str(data.get("session_id") or "") != str(session_id)
        or isinstance(revision_after, bool)
        or not isinstance(revision_after, int)
    ):
        return None
    result_hash = canonical_sha256(result)
    step_hash = canonical_sha256({
        "session_id": str(session_id),
        "turn_execution_id": str(turn_execution_id),
        "tool": "mcp_verified_write",
        "result_hash": result_hash,
    })
    return ToolExecutionReceipt(
        step_id=f"creation-step:{step_hash}",
        tool="mcp_verified_write",
        status="ok",
        summary=(
            "MCP 写入已由服务器 revision 变化验证："
            f"{data.get('revision_before')}→{revision_after}"
        ),
        resource_ids=(str(session_id),),
        result_ref=f"creation-tool-result:sha256:{result_hash}",
        reread="继续前重新读取当前 creation snapshot 与 revision。",
        write_committed=True,
    )


def seal_creation_runtime_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Seal one server-authored, restart-safe current-turn execution snapshot."""

    snapshot = dict(payload)
    snapshot.pop("source_hash", None)
    snapshot["schema"] = CREATION_AGENT_RUNTIME_SCHEMA
    snapshot["source_hash"] = canonical_sha256(snapshot)
    return snapshot


def validate_creation_runtime_snapshot(
    value: Any,
    *,
    session_id: str,
) -> dict[str, Any] | None:
    """Reject stale or tampered runtime receipts before recovery uses them."""

    if not isinstance(value, dict):
        return None
    snapshot = dict(value)
    source_hash = str(snapshot.pop("source_hash", "") or "")
    if (
        snapshot.get("schema") != CREATION_AGENT_RUNTIME_SCHEMA
        or str(snapshot.get("session_id") or "") != str(session_id)
        or not source_hash
        or canonical_sha256(snapshot) != source_hash
    ):
        return None
    snapshot["source_hash"] = source_hash
    return snapshot


def creation_current_user_context_message(
    conversation: dict[str, Any] | None,
    *,
    assistant_message_id: str,
    expected_content: str | None = None,
) -> ConversationMessage | None:
    """Resolve the exact persisted user message paired with a pending assistant."""

    messages = (conversation or {}).get("messages") or []
    for index, assistant in enumerate(messages):
        if (
            not isinstance(assistant, dict)
            or assistant.get("role") != "assistant"
            or str(assistant.get("id") or "") != str(assistant_message_id)
        ):
            continue
        if index == 0 or not isinstance(messages[index - 1], dict):
            return None
        user = messages[index - 1]
        if user.get("role") != "user" or user.get("status") != "completed":
            return None
        user_sequence = _message_sequence(user)
        assistant_sequence = _message_sequence(assistant)
        content = str(user.get("content") or "")
        message_id = str(user.get("id") or "").strip()
        if (
            not message_id
            or not content
            or user_sequence is None
            or assistant_sequence != user_sequence + 1
            or (expected_content is not None and content != expected_content)
        ):
            return None
        return ConversationMessage(
            message_id=message_id,
            sequence_no=user_sequence,
            role=ConversationRole.USER,
            content=content,
        )
    return None


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk_count = sum(
        1 for char in text
        if "一" <= char <= "鿿" or "㐀" <= char <= "䶿"
    )
    return cjk_count + max(1, (len(text) - cjk_count) // 4)


def record_prompt_metric(
    captured: list[dict[str, Any]],
    *,
    iteration: int,
    phase: str,
    active_categories: tuple[str, ...],
    messages: list[dict[str, Any]],
    schemas: list[dict[str, Any]],
    result: dict[str, Any] | None,
) -> None:
    raw_usage = result.get("usage") if isinstance(result, dict) else None
    usage = (
        raw_usage
        if isinstance(raw_usage, dict) and raw_usage.get("prompt_tokens") is not None
        else None
    )
    prompt_tokens = None
    if usage is not None:
        try:
            prompt_tokens = max(0, int(usage.get("prompt_tokens") or 0))
        except (TypeError, ValueError):
            prompt_tokens = None
    system_content = ""
    if messages and messages[0].get("role") == "system":
        system_content = str(messages[0].get("content") or "")
    request_projection = json.dumps(
        {"messages": messages, "tools": schemas},
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    captured.append({
        "iteration": iteration,
        "phase": phase,
        "active_categories": list(active_categories),
        "tool_count": len(schemas),
        "tool_schema_estimated_tokens": estimate_tokens(json.dumps(
            schemas,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )),
        "system_prompt_estimated_tokens": estimate_tokens(system_content),
        "request_estimated_tokens": estimate_tokens(request_projection),
        "prompt_tokens": prompt_tokens,
        "usage_reported": prompt_tokens is not None,
    })


def canonical_tool_call(
    value: Any,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    function = value.get("function")
    if not isinstance(function, dict):
        return None
    call_id = str(value.get("id") or "").strip()
    name = str(function.get("name") or "").strip()
    arguments = function.get("arguments")
    if not call_id or not name:
        return None
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments or {}, ensure_ascii=False, default=str)
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _validated_turn_messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        return []
    normalized: list[dict[str, Any]] = []
    pending_tool_ids: set[str] = set()
    final_assistant_seen = False
    for index, raw_message in enumerate(value):
        if not isinstance(raw_message, dict) or final_assistant_seen:
            return []
        role = str(raw_message.get("role") or "")
        content = str(raw_message.get("content") or "")
        if index == 0:
            if role != "user" or not content.strip():
                return []
            normalized.append({"role": "user", "content": content})
            continue
        if role == "assistant":
            if pending_tool_ids:
                return []
            raw_calls = raw_message.get("tool_calls")
            if isinstance(raw_calls, list) and raw_calls:
                calls = [canonical_tool_call(call) for call in raw_calls]
                if any(call is None for call in calls):
                    return []
                canonical_calls = [call for call in calls if call is not None]
                call_ids = [str(call["id"]) for call in canonical_calls]
                if len(call_ids) != len(set(call_ids)):
                    return []
                pending_tool_ids = set(call_ids)
                normalized.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": canonical_calls,
                })
            else:
                if not content.strip():
                    return []
                normalized.append({"role": "assistant", "content": content})
                final_assistant_seen = True
            continue
        if role == "tool":
            tool_call_id = str(raw_message.get("tool_call_id") or "").strip()
            if tool_call_id not in pending_tool_ids:
                return []
            pending_tool_ids.remove(tool_call_id)
            normalized.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            })
            continue
        return []
    if pending_tool_ids or not final_assistant_seen:
        return []
    return normalized


def _message_sequence(message: dict[str, Any]) -> int | None:
    """Read the durable sequence; missing values are never guessed."""

    try:
        sequence = int(message.get("sequence_no"))
    except (TypeError, ValueError):
        return None
    return sequence if sequence > 0 else None


def _closed_turn_status(
    *,
    assistant_status: str,
    trace_completed: bool,
) -> TurnStatus | None:
    """Map durable Creation presentation status to the shared closed-turn state.

    A Creation model turn may have completed while its separately persisted
    stage run is still active; in that case the system message deliberately
    remains ``running`` but the validated trace proves that the conversation
    turn itself is closed.  Failed/interrupted presentation turns stay exact
    and non-compressible instead of disappearing from history.
    """

    normalized = assistant_status.strip().lower()
    if trace_completed and normalized in {"completed", "running"}:
        return TurnStatus.COMPLETED
    return {
        "error": TurnStatus.ERROR,
        "failed": TurnStatus.ERROR,
        "interrupted": TurnStatus.ABORTED,
        "aborted": TurnStatus.ABORTED,
        "cancelled": TurnStatus.CANCELLED,
        "canceled": TurnStatus.CANCELLED,
    }.get(normalized)


def _turn_source_hash(
    *,
    user_message_id: str,
    assistant_message_id: str,
    sequence: int,
    user_content: str,
    assistant_content: str,
    status: str,
) -> str:
    return checkpoint_source_hash((
        CheckpointSourceMessage(
            message_id=user_message_id,
            sequence_no=sequence,
            role=ConversationRole.USER,
            content=user_content,
            status=status,
        ),
        CheckpointSourceMessage(
            message_id=assistant_message_id,
            sequence_no=sequence + 1,
            role=ConversationRole.ASSISTANT,
            content=assistant_content,
            status=status,
        ),
    ))


def _history_projection_error(
    reason: str,
    *,
    sequence_no: int | None = None,
) -> ConversationContextError:
    details: dict[str, Any] = {"reason": reason}
    if sequence_no is not None:
        details["sequence_no"] = sequence_no
    return ConversationContextError(
        ConversationContextErrorCode.PROTOCOL_INVALID,
        "立项历史存在无法安全映射的持久化回合，已停止构造模型上下文。",
        details=details,
    )


def _closed_creation_turn_record(
    *,
    user_message: dict[str, Any],
    assistant_message: dict[str, Any],
    session_id: str,
    seen_turn_ids: set[str],
) -> CreationConversationTurnRecord:
    sequence = _message_sequence(assistant_message)
    user_sequence = _message_sequence(user_message)
    assert sequence is not None and user_sequence is not None
    payload = assistant_message.get("payload")
    trace = payload.get("creation_agent_turn") if isinstance(payload, dict) else None
    if isinstance(trace, dict) and (
        trace.get("schema") != CREATION_AGENT_TURN_SCHEMA
        or str(trace.get("session_id") or "") != str(session_id)
    ):
        raise _history_projection_error(
            "history_creation_trace_scope_invalid",
            sequence_no=sequence,
        )
    outcome = trace.get("outcome") if isinstance(trace, dict) else None
    trace_completed = bool(
        isinstance(outcome, dict)
        and outcome.get("status") == "completed"
        and _validated_turn_messages(trace.get("messages"))
    )
    turn_status = _closed_turn_status(
        assistant_status=str(assistant_message.get("status") or ""),
        trace_completed=trace_completed,
    )
    if turn_status is None:
        raise _history_projection_error(
            "history_turn_not_closed_or_trace_invalid",
            sequence_no=sequence,
        )
    user_content = str(user_message.get("content") or "")
    assistant_content = str(assistant_message.get("content") or "")
    if not user_content.strip() or not assistant_content.strip():
        raise _history_projection_error(
            "history_turn_content_empty",
            sequence_no=sequence,
        )
    user_message_id = str(user_message.get("id") or "").strip()
    assistant_message_id = str(assistant_message.get("id") or "").strip()
    payload_turn_id = (
        payload.get("creation_agent_client_turn_id")
        if isinstance(payload, dict)
        else None
    )
    turn_id = str(
        (trace.get("client_turn_id") if isinstance(trace, dict) else None)
        or payload_turn_id
        or assistant_message_id
    ).strip()
    if not turn_id or turn_id in seen_turn_ids:
        raise _history_projection_error(
            "history_turn_id_invalid",
            sequence_no=sequence,
        )
    seen_turn_ids.add(turn_id)
    return CreationConversationTurnRecord(
        turn_id=turn_id,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        sequence=user_sequence,
        user_content=user_content,
        assistant_content=assistant_content,
        status=turn_status.value,
        source_hash=_turn_source_hash(
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            sequence=user_sequence,
            user_content=user_content,
            assistant_content=assistant_content,
            status=turn_status.value,
        ),
    )


def creation_agent_turn_records(
    conversation: dict[str, Any] | None,
    *,
    session_id: str,
    exclude_assistant_message_id: str | None = None,
) -> list[CreationConversationTurnRecord]:
    """Return every complete Creation Agent turn in transcript order.

    The only omitted pair may be the explicitly named current running
    assistant placeholder. Every older persisted message must form one exact,
    contiguous user/assistant pair; corrupt history fails closed instead of
    becoming an invisible gap in a checkpoint source range.
    """

    records: list[CreationConversationTurnRecord] = []
    pending_user: dict[str, Any] | None = None
    seen_turn_ids: set[str] = set()
    excluded_current_seen = False
    expected_sequence = 1
    messages = (conversation or {}).get("messages") or []
    if not isinstance(messages, list):
        raise _history_projection_error("history_messages_not_list")
    for message in messages:
        if excluded_current_seen:
            raise _history_projection_error("excluded_assistant_not_current")
        if not isinstance(message, dict):
            raise _history_projection_error("history_message_not_object")
        sequence = _message_sequence(message)
        if sequence is None or sequence != expected_sequence:
            raise _history_projection_error(
                "history_sequence_gap",
                sequence_no=sequence,
            )
        expected_sequence += 1
        role = str(message.get("role") or "")
        if role == "user":
            if pending_user is not None:
                raise _history_projection_error(
                    "history_user_without_closed_assistant",
                    sequence_no=sequence,
                )
            if str(message.get("status") or "completed") != "completed":
                raise _history_projection_error(
                    "history_user_not_completed",
                    sequence_no=sequence,
                )
            if not str(message.get("id") or "").strip():
                raise _history_projection_error(
                    "history_user_id_missing",
                    sequence_no=sequence,
                )
            pending_user = message
            continue
        if role != "assistant":
            raise _history_projection_error(
                "history_role_invalid",
                sequence_no=sequence,
            )
        if pending_user is None:
            raise _history_projection_error(
                "history_assistant_without_user",
                sequence_no=sequence,
            )

        user_message = pending_user
        pending_user = None
        assistant_message_id = str(message.get("id") or "").strip()
        if not assistant_message_id:
            raise _history_projection_error(
                "history_assistant_id_missing",
                sequence_no=sequence,
            )
        user_sequence = _message_sequence(user_message)
        if user_sequence is None or sequence != user_sequence + 1:
            raise _history_projection_error(
                "history_turn_sequence_invalid",
                sequence_no=sequence,
            )
        if assistant_message_id == exclude_assistant_message_id:
            if str(message.get("status") or "").strip().lower() != "running":
                raise _history_projection_error(
                    "excluded_assistant_not_running",
                    sequence_no=sequence,
                )
            excluded_current_seen = True
            continue

        records.append(_closed_creation_turn_record(
            user_message=user_message,
            assistant_message=message,
            session_id=session_id,
            seen_turn_ids=seen_turn_ids,
        ))
    if pending_user is not None:
        raise _history_projection_error(
            "history_user_without_assistant",
            sequence_no=_message_sequence(pending_user),
        )
    if exclude_assistant_message_id and not excluded_current_seen:
        raise _history_projection_error("excluded_assistant_not_found")
    return records


def creation_turns_as_context_turns(
    records: list[CreationConversationTurnRecord],
) -> tuple[ConversationTurn, ...]:
    """Adapt durable Creation records to the shared ContextFrame contract."""

    return tuple(
        ConversationTurn(
            turn_id=record.turn_id,
            status=TurnStatus(record.status),
            messages=(
                ConversationMessage(
                    message_id=record.user_message_id,
                    sequence_no=record.sequence,
                    role=ConversationRole.USER,
                    content=record.user_content,
                ),
                ConversationMessage(
                    message_id=record.assistant_message_id,
                    sequence_no=record.sequence + 1,
                    role=ConversationRole.ASSISTANT,
                    content=record.assistant_content,
                ),
            ),
        )
        for record in records
    )


def creation_checkpoint_source_turns(
    conversation: dict[str, Any] | None,
    *,
    session_id: str,
    before_sequence: int,
) -> tuple[ConversationTurn, ...]:
    """Reload only the closed prefix selected before checkpoint generation.

    Later user/assistant pairs may be appended while compression runs.  They
    remain in the durable transcript but are outside this immutable source
    snapshot and therefore must not invalidate it.
    """

    if isinstance(before_sequence, bool) or before_sequence <= 0:
        raise ValueError("before_sequence must be positive")
    messages = (conversation or {}).get("messages") or []
    if not isinstance(messages, list):
        raise _history_projection_error("history_messages_not_list")
    boundary_indexes = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, dict) and _message_sequence(message) == before_sequence
    ]
    if (
        len(boundary_indexes) != 1
        or not isinstance(messages[boundary_indexes[0]], dict)
        or str(messages[boundary_indexes[0]].get("role") or "") != "user"
    ):
        raise _history_projection_error(
            "checkpoint_boundary_not_user",
            sequence_no=before_sequence,
        )
    prefix = messages[: boundary_indexes[0]]
    return creation_turns_as_context_turns(
        creation_agent_turn_records(
            {"messages": prefix},
            session_id=session_id,
        )
    )


_LEDGER_SUCCESS_STATUSES = frozenset({
    "ok",
    "completed",
    "completed_with_warnings",
    "partial_success",
    "success",
    "succeeded",
    "warning",
})
_LEDGER_OPEN_STATUSES = frozenset({"pending", "queued", "running", "in_progress"})
_LEDGER_CLOSED_NON_ERROR_STATUSES = frozenset({
    "aborted",
    "cancelled",
    "canceled",
    "skipped",
    "superseded",
})


def _result_ref(value: dict[str, Any]) -> str:
    return f"creation-tool-result:sha256:{canonical_sha256(value)}"


def _resource_references_from_output_refs(
    value: dict[str, Any],
) -> tuple[ResourceReference, ...]:
    references: list[ResourceReference] = []
    seen: set[tuple[str, str]] = set()
    for resource_type in sorted(value):
        raw_items = value[resource_type]
        items = raw_items if isinstance(raw_items, list) else [raw_items]
        for item in items:
            if not isinstance(item, dict):
                continue
            resource_id = str(item.get("id") or "").strip()
            identity = (str(resource_type or "").strip(), resource_id)
            if not all(identity) or identity in seen:
                continue
            revision = item.get("revision")
            if isinstance(revision, bool) or not isinstance(revision, (int, str)):
                revision = None
            references.append(ResourceReference(
                type=identity[0],
                id=identity[1],
                revision=revision,
            ))
            seen.add(identity)
    return tuple(references)


def _receipt_error_code(status: str) -> str | None:
    normalized = status.strip().lower()
    if (
        normalized in _LEDGER_SUCCESS_STATUSES
        or normalized in _LEDGER_OPEN_STATUSES
        or normalized in _LEDGER_CLOSED_NON_ERROR_STATUSES
    ):
        return None
    if normalized in {"denied", "forbidden", "unauthorized"}:
        return "creation_tool_denied"
    if normalized in {"error", "failed", "failure"}:
        return "creation_tool_failed"
    return "creation_tool_non_success"


def _resolve_creation_retry_errors(
    entries: list[ExecutionLedgerEntry],
) -> tuple[ExecutionLedgerEntry, ...]:
    """A later server-verified success resolves older errors for that tool."""

    later_successes: set[str] = set()
    kept_reversed: list[ExecutionLedgerEntry] = []
    for entry in reversed(entries):
        status = entry.status.strip().lower()
        if status in _LEDGER_SUCCESS_STATUSES:
            later_successes.add(entry.tool)
        elif (
            status not in _LEDGER_OPEN_STATUSES
            and status not in _LEDGER_CLOSED_NON_ERROR_STATUSES
            and entry.tool in later_successes
        ):
            continue
        kept_reversed.append(entry)
    return tuple(reversed(kept_reversed))


def creation_execution_ledger_from_conversation(
    conversation: dict[str, Any] | None,
    *,
    session_id: str,
) -> CreationExecutionLedgerProjection:
    """Build trusted navigation from sealed runtime receipts and exact results.

    Assistant prose and trace text are deliberately ignored.  A receipt is
    accepted only when its ``result_ref`` verifies one full structured result
    inside the same sealed runtime snapshot.
    """

    # Imported lazily to keep this context-neutral record module out of the
    # workspace package's import-time registry graph.
    from app.services.workspace.tool_output_refs import (
        output_refs_from_tool_result,
    )

    entries: list[ExecutionLedgerEntry] = []
    source_hashes: dict[str, str] = {}
    conflicted_step_ids: set[str] = set()
    for message in (conversation or {}).get("messages") or ():
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        payload = message.get("payload")
        runtime = validate_creation_runtime_snapshot(
            (
                payload.get("creation_agent_runtime")
                if isinstance(payload, dict)
                else None
            ),
            session_id=session_id,
        )
        if runtime is None:
            continue
        raw_results = runtime.get("tool_results")
        raw_receipts = runtime.get("execution_receipts")
        if not isinstance(raw_results, list) or not isinstance(raw_receipts, list):
            continue
        results_by_ref = {
            _result_ref(result): result
            for result in raw_results
            if isinstance(result, dict)
        }
        run_id = f"creation-turn:{str(message.get('id') or '').strip()}"
        if run_id == "creation-turn:":
            continue
        for receipt in raw_receipts:
            if not isinstance(receipt, dict):
                continue
            step_id = str(receipt.get("step_id") or "").strip()
            tool = str(receipt.get("tool") or "").strip()
            status = str(receipt.get("status") or "").strip()
            result_ref = str(receipt.get("result_ref") or "").strip()
            result = results_by_ref.get(result_ref)
            if (
                not step_id.startswith("creation-step:")
                or not tool
                or not status
                or result is None
                or str(result.get("tool") or tool) != tool
                or str(result.get("status") or "").strip() != status
            ):
                continue
            output_refs = (
                output_refs_from_tool_result(
                    tool,
                    result,
                    request={"session_id": session_id},
                    step_status=status,
                )
                if receipt.get("write_committed") is True
                else {}
            )
            entry = ExecutionLedgerEntry(
                run_id=run_id,
                step_id=step_id,
                tool=tool,
                status=status,
                resource_refs=_resource_references_from_output_refs(output_refs),
                error_code=_receipt_error_code(status),
            )
            source_hash = canonical_sha256({
                "assistant_message_id": str(message.get("id") or ""),
                "assistant_sequence_no": _message_sequence(message),
                "runtime_source_hash": runtime.get("source_hash"),
                "receipt": receipt,
                "tool_result": result,
            })
            if step_id in conflicted_step_ids:
                continue
            if step_id in source_hashes:
                if source_hashes[step_id] == source_hash:
                    continue
                conflicted_step_ids.add(step_id)
                source_hashes.pop(step_id, None)
                entries = [item for item in entries if item.step_id != step_id]
                continue
            entries.append(entry)
            source_hashes[step_id] = source_hash

    resolved = _resolve_creation_retry_errors(entries)
    folded = fold_execution_ledger(resolved)
    kept_ids = {entry.step_id for entry in folded}
    return CreationExecutionLedgerProjection(
        entries=folded,
        source_hashes={
            step_id: source_hashes[step_id]
            for step_id in source_hashes
            if step_id in kept_ids
        },
    )


__all__ = [
    "CREATION_AGENT_RUNTIME_SCHEMA",
    "CREATION_AGENT_TURN_SCHEMA",
    "CreationConversationTurnRecord",
    "CreationExecutionLedgerProjection",
    "canonical_tool_call",
    "creation_checkpoint_source_turns",
    "creation_current_user_context_message",
    "creation_agent_turn_records",
    "creation_execution_ledger_from_conversation",
    "creation_turns_as_context_turns",
    "record_prompt_metric",
    "seal_creation_runtime_snapshot",
    "validate_creation_runtime_snapshot",
    "verified_mcp_execution_receipt",
]
