"""Strict native-call validation and durable receipt construction for Creation."""

from __future__ import annotations

import json
from collections.abc import Collection
from typing import Any

from app.architecture.tool_categories import TOOL_CATEGORY_CONTROLLER
from app.services.conversation_context.canonical import canonical_sha256
from app.services.conversation_context.errors import (
    ConversationContextError,
    ConversationContextErrorCode,
)
from app.services.conversation_context.tool_transactions import (
    NativeToolCall,
    NativeToolResult,
    ToolExecutionReceipt,
)
from app.services.creation_agent_turn_records import canonical_tool_call

_SAFE_TOOL_SUCCESS_STATUSES = frozenset({
    "ok", "warning", "running", "queued", "completed",
    "completed_with_warnings", "partial_success", "needs_confirmation",
    "waiting_user", "pending", "generated", "confirmed",
})
_SAFE_TOOL_DIAGNOSTICS = {
    "read_required": "写入前必须先读取真实立项数据，并在下一模型步骤重新决定写入。",
    "successful_write_limit": "本条作者消息已经成功写入一次；请结束本轮并等待下一条消息。",
    "failed_write_limit": "本轮写入失败次数已达上限；请结束本轮并等待作者调整要求。",
    "revision_conflict": "立项数据版本已变化；请重新读取当前版本后重试。",
    "tool_result_over_capacity": "工具结果超过模型可见容量；请缩小范围或使用分页后重试。",
    "tool_result_batch_over_capacity": "工具结果批次超过协议容量；请减少并行调用后重试。",
    "native_assistant_transaction_over_capacity": "原生工具事务超过协议容量；请减少调用或参数后重试。",
    "native_assistant_transaction_invalid": "原生工具事务无法安全验证；本批次未执行。",
    "model_result_projection_failed": "工具结果无法安全投影；请缩小范围或重试。",
}
_SAFE_DIAGNOSTIC_NUMERIC_FIELDS = frozenset({
    "actual_bytes", "max_bytes", "batch_call_count",
    "declared_batch_json_bytes", "max_batch_json_bytes", "successful_writes",
    "write_limit", "failed_writes", "failed_write_limit",
})


def safe_creation_tool_result(
    name: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Project every non-success result to a server-owned public envelope."""

    status = str(result.get("status") or "error").strip().lower()
    if status in _SAFE_TOOL_SUCCESS_STATUSES:
        return result
    raw_data = result.get("data") if isinstance(result.get("data"), dict) else {}
    raw_reason = str(raw_data.get("reason") or "").strip()
    if status == "conflict" and not raw_reason:
        raw_reason = "revision_conflict"
    default_reason = {
        "denied": "creation_tool_denied",
        "skipped": "creation_tool_skipped",
        "cancelled": "creation_tool_cancelled",
        "canceled": "creation_tool_cancelled",
    }.get(status, "creation_tool_failed")
    reason = raw_reason if raw_reason in _SAFE_TOOL_DIAGNOSTICS else default_reason
    detail = _SAFE_TOOL_DIAGNOSTICS.get(reason) or {
        "creation_tool_denied": "工具调用被当前立项安全边界拒绝。",
        "creation_tool_skipped": "工具调用未执行；请重新读取当前状态或调整请求。",
        "creation_tool_cancelled": "工具调用已取消；请确认当前状态后再继续。",
    }.get(reason, "工具未能完成本次操作；请重新读取当前状态或调整请求后重试。")
    data: dict[str, Any] = {"reason": reason}
    if reason in _SAFE_TOOL_DIAGNOSTICS:
        for field_name in _SAFE_DIAGNOSTIC_NUMERIC_FIELDS:
            value = raw_data.get(field_name)
            if isinstance(value, int) and not isinstance(value, bool):
                data[field_name] = value
    public_status = {
        "error": "error",
        "failed": "failed",
        "failure": "failed",
        "denied": "denied",
        "skipped": "skipped",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "aborted": "cancelled",
        "conflict": "error",
    }.get(status, "error")
    return {"tool": name, "status": public_status, "detail": detail, "data": data}


def _native_protocol_error(
    *,
    iteration: int,
    reason: str,
    call_index: int | None = None,
    **details: Any,
) -> ConversationContextError:
    payload: dict[str, Any] = {
        "iteration": iteration + 1,
        "reason": reason,
        **details,
    }
    if call_index is not None:
        payload["call_index"] = call_index
    return ConversationContextError(
        ConversationContextErrorCode.PROTOCOL_INVALID,
        "模型返回的原生工具调用批次不符合可验证协议，本批次未执行。",
        details=payload,
    )


def validate_native_call_batch(
    raw_calls: list[Any],
    *,
    iteration: int,
    allowed_tool_names: frozenset[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Validate the complete native batch before the first handler runs."""

    calls: list[dict[str, Any]] = []
    arguments_by_call_id: dict[str, dict[str, Any]] = {}
    for index, raw_call in enumerate(raw_calls):
        call = canonical_tool_call(raw_call)
        if call is None:
            raise _native_protocol_error(
                iteration=iteration,
                call_index=index,
                reason="invalid_native_tool_call_identity",
            )
        call_id = str(call["id"])
        if call_id in arguments_by_call_id:
            raise _native_protocol_error(
                iteration=iteration,
                call_index=index,
                reason="duplicate_native_tool_call_id",
                call_id=call_id,
            )
        raw_function = raw_call.get("function") if isinstance(raw_call, dict) else None
        raw_arguments = (
            raw_function.get("arguments")
            if isinstance(raw_function, dict)
            else None
        )
        if not isinstance(raw_arguments, str):
            raise _native_protocol_error(
                iteration=iteration,
                call_index=index,
                reason="native_tool_arguments_not_json_string",
                call_id=call_id,
            )
        arguments_json = raw_arguments.strip()
        if not arguments_json:
            raise _native_protocol_error(
                iteration=iteration,
                call_index=index,
                reason="native_tool_arguments_empty",
                call_id=call_id,
            )
        try:
            arguments = json.loads(arguments_json)
        except json.JSONDecodeError as exc:
            raise _native_protocol_error(
                iteration=iteration,
                call_index=index,
                reason="invalid_native_tool_arguments_json",
                call_id=call_id,
            ) from exc
        if not isinstance(arguments, dict):
            raise _native_protocol_error(
                iteration=iteration,
                call_index=index,
                reason="native_tool_arguments_not_object",
                call_id=call_id,
            )
        # Preserve the provider's exact JSON string as protocol evidence.
        call["function"]["arguments"] = raw_arguments
        calls.append(call)
        arguments_by_call_id[call_id] = arguments

    controller_calls = [
        call
        for call in calls
        if call["function"]["name"] == TOOL_CATEGORY_CONTROLLER
    ]
    if controller_calls and len(calls) != 1:
        raise _native_protocol_error(
            iteration=iteration,
            reason="category_controller_must_be_only_call",
            call_count=len(calls),
        )
    for index, call in enumerate(calls):
        name = str(call["function"]["name"])
        if name not in allowed_tool_names:
            raise _native_protocol_error(
                iteration=iteration,
                call_index=index,
                reason="native_tool_not_open",
                call_id=str(call["id"]),
                tool=name,
            )
    return calls, arguments_by_call_id


def _receipt_resource_ids(result: dict[str, Any]) -> tuple[str, ...]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    candidates: list[Any] = [
        data.get(key)
        for key in (
            "session_id",
            "project_id",
            "operation_id",
            "entity_id",
            "artifact",
        )
    ]
    for nested_name in ("entity", "run", "session", "artifact"):
        nested = data.get(nested_name)
        if not isinstance(nested, dict):
            continue
        candidates.extend(
            nested.get(key)
            for key in (
                "id",
                "run_id",
                "session_id",
                "project_id",
                "operation_id",
                "entity_id",
                "artifact",
            )
        )
    return tuple(dict.fromkeys(
        str(value).strip()
        for value in candidates
        if (
            value is not None
            and not isinstance(value, (dict, list, tuple, set))
            and str(value).strip()
        )
    ))


def _receipt_summary(name: str, result: dict[str, Any]) -> str:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    facts: dict[str, Any] = {}
    for key in (
        "session_id",
        "revision",
        "current_revision",
        "artifact",
        "operation_id",
        "project_id",
        "reason",
    ):
        value = data.get(key)
        if value is not None and not isinstance(value, (dict, list)):
            facts[key] = value
    for nested_name in ("entity", "run", "session"):
        nested = data.get(nested_name)
        if isinstance(nested, dict):
            facts[nested_name] = {
                key: nested.get(key)
                for key in (
                    "id",
                    "run_id",
                    "status",
                    "revision",
                    "artifact",
                    "entity_type",
                    "entity_key",
                    "operation_id",
                    "project_id",
                )
                if nested.get(key) is not None
            }
    return json.dumps(
        {
            "tool": name,
            "status": str(result.get("status") or "unknown"),
            "detail": str(result.get("detail") or ""),
            "facts": facts,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def build_creation_execution_receipt(
    *,
    session_id: str,
    turn_execution_id: str,
    transaction_number: int,
    call: NativeToolCall,
    result: dict[str, Any],
    model_content: str,
    read_tools: Collection[str],
    write_tools: Collection[str],
    write_success_statuses: Collection[str],
) -> tuple[NativeToolResult, ToolExecutionReceipt]:
    """Build an auditable native result/receipt pair from exact server data."""

    result_hash = canonical_sha256(result)
    step_hash = canonical_sha256({
        "session_id": session_id,
        "turn_execution_id": turn_execution_id,
        "transaction_number": transaction_number,
        "call": call.to_provider_dict(),
        "result_hash": result_hash,
    })
    step_id = f"creation-step:{step_hash}"
    result_ref = f"creation-tool-result:sha256:{result_hash}"
    status = str(result.get("status") or "unknown")
    receipt = ToolExecutionReceipt(
        step_id=step_id,
        tool=call.name,
        status=status,
        summary=_receipt_summary(call.name, result),
        resource_ids=_receipt_resource_ids(result),
        result_ref=result_ref,
        reread=(
            f"重新调用 {call.name} 获取当前立项事实；本回执只证明当时的执行结果。"
            if call.name in read_tools
            else "继续前重新读取当前 creation snapshot 与 revision。"
        ),
        write_committed=(
            call.name in write_tools
            and status in write_success_statuses
        ),
    )
    return (
        NativeToolResult(
            call_id=call.call_id,
            content=model_content,
            result_ref=result_ref,
            persisted_step_id=step_id,
        ),
        receipt,
    )


__all__ = [
    "build_creation_execution_receipt",
    "safe_creation_tool_result",
    "validate_native_call_batch",
]
