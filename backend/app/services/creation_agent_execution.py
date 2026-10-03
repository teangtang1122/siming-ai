"""Single-turn execution state machine for the conversational Creation Agent."""
from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import count
from typing import Any

from app.architecture.tool_categories import (
    TOOL_CATEGORY_CONTROLLER,
    tool_names_for_categories,
)
from app.core.exceptions import LLMError
from app.database.models import NovelCreationStageRun
from app.modules.creation.interfaces.agent_progress import (
    creation_tool_completed_event,
    creation_tool_started_event,
)
from app.modules.creation.interfaces.agent_scope import (
    CREATION_AGENT_REVISION_TOOL_NAMES,
    CREATION_AGENT_TOOL_NAMES,
    CREATION_AGENT_WRITE_TOOL_NAMES,
    CREATION_TURN_MAX_FAILED_WRITES,
    CREATION_WRITE_SUCCESS_STATUSES,
    creation_turn_write_denial,
    creation_turn_writes_closed,
)
from app.modules.operations.application.trace_capture import record_payload, record_tool_outcome
from app.modules.operations.application.trace_decorators import observed
from app.services.conversation_context.errors import (
    ConversationContextError,
    ConversationContextErrorCode,
)
from app.services.conversation_context.tool_transactions import (
    NativeToolCall,
    NativeToolResult,
    ToolExecutionReceipt,
    ToolTransaction,
)
from app.services.creation_agent_native_protocol import (
    build_creation_execution_receipt,
    safe_creation_tool_result,
    validate_native_call_batch,
)
from app.services.creation_agent_reply import (
    CREATION_READ_ONLY_COMPLETION_INSTRUCTION,
    CREATION_READ_ONLY_NOTICE,
    CREATION_REPLY_FAILURE_NOTICE,
    CREATION_REPLY_INSTRUCTION,
    CREATION_REPLY_MAX_ATTEMPTS,
    CREATION_REPLY_REPAIR_INSTRUCTION,
    creation_receipt_reply,
    creation_reply_error,
)
from app.services.creation_agent_state import CreationExecutionBindings as CreationExecutionBindings
from app.services.creation_agent_state import CreationTurnState as CreationTurnState
from app.services.creation_agent_state import _all_execution_receipts as _all_execution_receipts
from app.services.creation_agent_state import (
    _archive_consumed_transactions as _archive_consumed_transactions,
)
from app.services.creation_agent_state import (
    _consume_delivered_transactions as _consume_delivered_transactions,
)
from app.services.creation_agent_state import _durable_runtime_snapshot as _durable_runtime_snapshot
from app.services.creation_agent_tool_projection import (
    creation_tool_message_content as _tool_message_content,
)
from app.services.creation_agent_turn_records import (
    CREATION_AGENT_TURN_SCHEMA,
    record_prompt_metric,
)
from app.services.workspace.executor import execute_workspace_action
from app.services.workspace.registry import registry
from app.services.workspace.tool_result_projection import (
    MAX_CONSECUTIVE_TOOL_CAPACITY_REJECTIONS,
    ToolResultBatchOverCapacity,
    ToolResultProjectionError,
    admit_native_assistant_transaction,
    declared_model_results_for_tool_names,
)

CREATION_AGENT_TOOLS = set(CREATION_AGENT_TOOL_NAMES)
SESSION_TOOLS = CREATION_AGENT_TOOLS - {
    "get_creation_operation", "get_creation_entity", "patch_creation_entity",
    "delete_creation_entity", "get_creation_artifact_diff",
    "restore_creation_artifact_version", "cancel_creation_operation",
    "pause_creation_operation", "resume_creation_operation",
    "retry_creation_operation", "read_imported_file",
}
REVISION_TOOLS = set(CREATION_AGENT_REVISION_TOOL_NAMES)
WRITE_TOOLS = set(CREATION_AGENT_WRITE_TOOL_NAMES)
READ_TOOLS = CREATION_AGENT_TOOLS - WRITE_TOOLS
async def _report_stream_resume(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
    payload: dict[str, Any],
) -> None:
    checkpoint_chars = max(0, int(payload.get("checkpoint_chars") or 0))
    await bindings.emit_progress(
        state.on_event,
        state.progress_events,
        "model_step_started",
        (
            "模型连接中断，正在从已验证的文字检查点继续…"
            if checkpoint_chars else "模型工具响应中断，正在重新获取完整工具调用…"
        ),
        {
            "resume_attempt": max(1, int(payload.get("resume_attempt") or 1)),
            "checkpoint_chars": checkpoint_chars,
        },
    )


async def _execute_domain_call(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
    name: str,
    arguments: dict[str, Any],
    available_tool_names: set[str],
) -> tuple[dict[str, Any], tuple[str, ...] | None]:
    if name == TOOL_CATEGORY_CONTROLLER:
        result, categories = bindings.category_tool_result(arguments)
        if categories is not None:
            await bindings.emit_progress(
                state.on_event,
                state.progress_events,
                "tool_categories_changed",
                str(result.get("detail") or "已准备立项能力"),
                dict(result.get("data") or {}),
            )
        return result, categories
    if name not in available_tool_names or name not in CREATION_AGENT_TOOLS:
        return {
            "tool": name,
            "status": "skipped",
            "detail": "该工具当前未向立项会话开放",
        }, None
    write_denial = creation_turn_write_denial(
        name,
        successful_writes=state.successful_write_count,
        failed_writes=state.failed_write_count,
    )
    if write_denial is not None:
        return write_denial, None
    if name in SESSION_TOOLS:
        arguments["session_id"] = state.session.id
    if name in REVISION_TOOLS and not arguments.get("expected_revision"):
        state.db.refresh(state.session)
        arguments["expected_revision"] = int(state.session.revision or 0)
    if name in {
        "generate_creation_artifact",
        "refine_creation_artifact",
        "regenerate_creation_artifact",
    }:
        if not str(arguments.get("model") or "").strip():
            arguments["model"] = state.model
        if state.model:
            arguments["use_model"] = True
    signature = json.dumps(
        {"name": name, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    seen_calls = (
        state.seen_write_calls if name in WRITE_TOOLS else state.active_read_calls
    )
    if signature in seen_calls:
        return {
            "tool": name,
            "status": "skipped",
            "detail": "相同工具调用已执行，本轮不重复提交",
            "data": {"reason": "duplicate_tool_call"},
        }, None
    seen_calls.add(signature)
    started = creation_tool_started_event(name, arguments)
    await bindings.emit_progress(
        state.on_event,
        state.progress_events,
        started["type"],
        started["message"],
        started["data"],
    )
    return await execute_workspace_action(
        state.db,
        "",
        {"tool": name, "arguments": arguments},
    ), None


@dataclass(frozen=True)
class _PreparedNativeBatch:
    transaction_number: int
    transaction: ToolTransaction
    native_calls: tuple[NativeToolCall, ...]
    batch_rejection: ToolResultBatchOverCapacity | None
    invalid_assistant_detail: str
    terminal_error: ConversationContextError | None


def _native_assistant_payload(
    calls: list[dict[str, Any]],
    *,
    assistant_content: str,
    assistant_reasoning_content: str,
    assistant_provider_state: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": "assistant", "content": assistant_content, "tool_calls": calls,
    }
    if assistant_reasoning_content:
        payload["reasoning_content"] = assistant_reasoning_content
    if assistant_provider_state:
        payload["provider_state"] = list(assistant_provider_state)
    return payload


def _prepare_native_batch(
    state: CreationTurnState,
    calls: list[dict[str, Any]],
    *,
    assistant_content: str,
    assistant_reasoning_content: str,
    assistant_provider_state: tuple[dict[str, Any], ...],
    result_contents: tuple[str, ...] | None = None,
) -> _PreparedNativeBatch:
    if any(transaction.state.value != "consumed" for transaction in state.tool_transactions):
        raise RuntimeError("上一批原生工具事务尚未消费，不能创建下一批事务")
    resolved = declared_model_results_for_tool_names(
        (
            str((call.get("function") or {}).get("name") or "")
            for call in calls
        ),
        resolve_tool=registry.get,
    )
    rejection: ToolResultBatchOverCapacity | None = None
    invalid_detail = ""
    terminal_error: ConversationContextError | None = None
    assistant_payload = _native_assistant_payload(
        calls,
        assistant_content=assistant_content,
        assistant_reasoning_content=assistant_reasoning_content,
        assistant_provider_state=assistant_provider_state,
    )
    try:
        admit_native_assistant_transaction(
            assistant_payload, resolved, request_budget=state.provider_request_budget(),
            result_contents=result_contents,
        )
        state.consecutive_capacity_rejections = 0
    except ToolResultBatchOverCapacity as exc:
        rejection = exc
        state.consecutive_capacity_rejections += 1
        exc.recovery_fits = (
            exc.recovery_fits
            and state.consecutive_capacity_rejections < MAX_CONSECUTIVE_TOOL_CAPACITY_REJECTIONS
        )
        if not exc.recovery_fits:
            terminal_error = ConversationContextError(
                (ConversationContextErrorCode.PROTOCOL_INVALID
                 if exc.reason == "native_assistant_transaction_invalid"
                 else ConversationContextErrorCode.TOOL_TRANSACTION_OVER_CAPACITY),
                "工具批次无法在当前模型预算内恢复，已保留本轮进度；本批次未执行。",
                details={
                    **exc.model_error_result("tool_batch")["data"],
                    "consecutive_rejections": state.consecutive_capacity_rejections,
                },
            )
    except ToolResultProjectionError:
        invalid_detail = "原生工具事务无法安全验证；本批次未执行。"
        terminal_error = ConversationContextError(
            ConversationContextErrorCode.PROTOCOL_INVALID,
            "模型返回的原生工具事务无法安全序列化，本批次未执行。",
            details={
                "reason": "native_assistant_transaction_invalid",
                "call_count": len(calls),
                "remediation": "重试本轮；若持续出现，请切换模型或检查提供商响应。",
            },
        )
    state.native_transaction_count += 1
    transaction_number = state.native_transaction_count
    native_calls = tuple(
        NativeToolCall(
            call_id=str(call.get("id") or ""),
            name=str((call.get("function") or {}).get("name") or ""),
            arguments_json=str((call.get("function") or {}).get("arguments")),
        )
        for call in calls
    )
    transaction = ToolTransaction(
        transaction_id=f"creation-transaction-{transaction_number}",
        assistant_message_id=f"creation-tool-assistant-{transaction_number}",
        assistant_content=assistant_content,
        calls=native_calls,
        assistant_reasoning_content=assistant_reasoning_content,
        assistant_provider_state=(
            ()
            if invalid_detail or (
                rejection is not None
                and rejection.reason == "native_assistant_transaction_invalid"
            )
            else assistant_provider_state
        ),
    )
    return _PreparedNativeBatch(
        transaction_number=transaction_number,
        transaction=transaction,
        native_calls=native_calls,
        batch_rejection=rejection,
        invalid_assistant_detail=invalid_detail,
        terminal_error=terminal_error,
    )


@observed(kind="tool", inputs=("arguments",), input_layer="tool_arguments",
    label_field="native_call.name", attributes={"tool_call_id": "native_call.call_id"},
    capture_output=False)
async def _execute_one_native_call(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
    *,
    native_call: NativeToolCall,
    arguments: dict[str, Any],
    transaction_number: int,
    available_tools: set[str],
    reads_ready_before_step: bool,
    batch_rejection: ToolResultBatchOverCapacity | None,
    invalid_assistant_detail: str,
    staged_result: tuple[dict[str, Any], str] | None = None,
) -> tuple[NativeToolResult, ToolExecutionReceipt, tuple[str, ...] | None]:
    name = native_call.name
    if batch_rejection is not None:
        tool_result, pending_categories = (
            batch_rejection.model_error_result(name),
            None,
        )
    elif invalid_assistant_detail:
        tool_result, pending_categories = ({
            "tool": name,
            "status": "error",
            "detail": invalid_assistant_detail,
            "data": {"reason": "native_assistant_transaction_invalid"},
        }, None)
    elif staged_result is not None:
        tool_result, pending_categories = staged_result[0], None
    elif name in WRITE_TOOLS and not reads_ready_before_step:
        tool_result, pending_categories = ({
            "tool": name,
            "status": "denied",
            "detail": (
                "写入前必须先完成一次真实业务读取，并让读取结果进入下一模型步骤；"
                "不得在同一个模型步骤并列决定读取和写入。"
            ),
            "data": {
                "reason": "read_required",
                "required_next_step": (
                    "Read the exact target, then decide the write in the next model step."
                ),
            },
        }, None)
    else:
        tool_result, pending_categories = await _execute_domain_call(
            state,
            bindings,
            name,
            arguments,
            available_tools,
        )
    record_payload("tool_receipt", tool_result)
    record_tool_outcome(tool_result)
    tool_result = safe_creation_tool_result(name, tool_result)
    model_content = (
        staged_result[1] if staged_result is not None and batch_rejection is None
        and not invalid_assistant_detail
        else _tool_message_content(name, tool_result, arguments)
    )
    model_result = json.loads(model_content)
    state.tool_results.append(tool_result)
    if name != TOOL_CATEGORY_CONTROLLER:
        # A successful read whose full result was rejected by the model-visible
        # projection has not supplied evidence to the next model step.
        progress_result = (
            model_result if name in READ_TOOLS and model_result.get("status") == "error"
            else tool_result
        )
        completed = creation_tool_completed_event(name, arguments, progress_result)
        if str(progress_result.get("status") or "") not in {"ok", "running"}:
            detail = " ".join(str(progress_result.get("detail") or "").split())
            if detail:
                completed["message"] += f"：{detail[:140]}"
        await bindings.emit_progress(
            state.on_event,
            state.progress_events,
            completed["type"],
            completed["message"],
            completed["data"],
        )
    if name in WRITE_TOOLS:
        status = str(tool_result.get("status") or "")
        result_data = (
            tool_result.get("data")
            if isinstance(tool_result.get("data"), dict)
            else {}
        )
        boundary_reason = str(result_data.get("reason") or "")
        if status in CREATION_WRITE_SUCCESS_STATUSES:
            state.successful_write_count += 1
            state.write_results.append(tool_result)
        elif boundary_reason not in {
            "successful_write_limit", "failed_write_limit", "read_required",
        }:
            state.failed_write_count += 1
            if state.failed_write_count == CREATION_TURN_MAX_FAILED_WRITES:
                await bindings.emit_progress(
                    state.on_event,
                    state.progress_events,
                    "tool_completed",
                    "写入连续失败已达上限，本轮已停止自动重试",
                    {
                        "tool": name,
                        "status": "denied",
                        "turn_boundary": "failed_write_limit",
                        "failed_writes": state.failed_write_count,
                    },
                )
    if (
        name in READ_TOOLS
        and str(tool_result.get("status") or "") in {"ok", "warning"}
        and str(model_result.get("status") or "") in {"ok", "warning"}
    ):
        state.successful_read_count += 1
    native_result, receipt = build_creation_execution_receipt(
        session_id=str(state.session.id),
        turn_execution_id=state.turn_execution_id,
        transaction_number=transaction_number,
        call=native_call,
        result=tool_result,
        model_content=model_content,
        read_tools=READ_TOOLS,
        write_tools=WRITE_TOOLS,
        write_success_statuses=CREATION_WRITE_SUCCESS_STATUSES,
    )
    record_payload("model_visible_tool_result", {"tool_call_id": native_result.call_id,
        "content": native_result.content, "receipt": receipt.to_dict()})
    return native_result, receipt, pending_categories


async def _execute_native_calls(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
    calls: list[dict[str, Any]],
    *,
    arguments_by_call_id: dict[str, dict[str, Any]],
    assistant_content: str,
    assistant_reasoning_content: str,
    assistant_provider_state: tuple[dict[str, Any], ...],
) -> tuple[str, ...] | None:
    available = set(tool_names_for_categories(state.active_categories)) & CREATION_AGENT_TOOLS
    staged: dict[str, tuple[dict[str, Any], dict[str, Any], str]] = {}
    stageable_reads = bool(calls) and all(
        (tool := registry.get(str((call.get("function") or {}).get("name") or "")))
        is not None
        and tool.tool_type == "read"
        and tool.capacity_preflight_safe
        for call in calls
    )
    if stageable_reads:
        resolved = declared_model_results_for_tool_names(
            (str(call["function"]["name"]) for call in calls),
            resolve_tool=registry.get,
        )
        try:
            admit_native_assistant_transaction(
                _native_assistant_payload(
                    calls,
                    assistant_content=assistant_content,
                    assistant_reasoning_content=assistant_reasoning_content,
                    assistant_provider_state=assistant_provider_state,
                ),
                resolved,
                request_budget=state.provider_request_budget(),
            )
            # The ordinary pre-handler ceiling already fits; no staging needed.
            stageable_reads = False
        except ToolResultBatchOverCapacity as exc:
            # Only a pessimistic result declaration permits preflight reads.
            # Invalid or oversized assistant messages still fail before handlers.
            stageable_reads = exc.reason == "tool_result_batch_over_capacity"
        except ToolResultProjectionError:
            stageable_reads = False
    active_reads_before = set(state.active_read_calls)
    if stageable_reads:
        try:
            for call in calls:
                call_id = str(call["id"])
                name = str(call["function"]["name"])
                arguments = dict(arguments_by_call_id[call_id])
                result, pending_categories = await _execute_domain_call(
                    state, bindings, name, arguments, available,
                )
                if pending_categories is not None:
                    raise RuntimeError("纯读取工具不能切换工具类别")
                safe_result = safe_creation_tool_result(name, result)
                staged[call_id] = (
                    arguments,
                    result,
                    _tool_message_content(name, safe_result, arguments),
                )
        except Exception:
            state.active_read_calls = active_reads_before
            raise
    batch = _prepare_native_batch(
        state,
        calls,
        assistant_content=assistant_content,
        assistant_reasoning_content=assistant_reasoning_content,
        assistant_provider_state=assistant_provider_state,
        result_contents=(
            tuple(staged[str(call["id"])][2] for call in calls)
            if stageable_reads else None
        ),
    )
    if batch.batch_rejection is not None or batch.invalid_assistant_detail:
        state.active_read_calls = active_reads_before
    reads_ready_before_step = state.successful_read_count > 0
    transaction = batch.transaction
    receipts: list[ToolExecutionReceipt] = []
    for call_index, (call, native_call) in enumerate(
        zip(calls, batch.native_calls, strict=True)
    ):
        staged_call = staged.get(native_call.call_id)
        arguments = (
            staged_call[0] if staged_call is not None
            else dict(arguments_by_call_id[native_call.call_id])
        )
        native_result, receipt, pending_categories = await _execute_one_native_call(
            state,
            bindings,
            native_call=native_call,
            arguments=arguments,
            transaction_number=batch.transaction_number,
            available_tools=available,
            reads_ready_before_step=reads_ready_before_step,
            batch_rejection=batch.batch_rejection,
            invalid_assistant_detail=batch.invalid_assistant_detail,
            staged_result=(
                (staged_call[1], staged_call[2]) if staged_call is not None else None
            ),
        )
        tool_message = {
            "role": "tool",
            "tool_call_id": str(call.get("id") or ""),
            "content": native_result.content,
        }
        state.messages.append(tool_message)
        state.protocol_messages.append(tool_message)
        transaction = transaction.add_result(native_result)
        receipts.append(receipt)
        if pending_categories is not None:
            state.pending_transaction_receipts[transaction.transaction_id] = tuple(
                receipts
            )
            state.tool_transactions.append(transaction.mark_delivered())
            await state.persist_runtime_state(_durable_runtime_snapshot(state))
            return pending_categories
        if call_index < len(calls) - 1:
            # A tool may have committed a business mutation.  Persist the
            # partial server-authored transaction and receipt before invoking
            # the next handler so a process failure cannot erase that fact.
            await state.persist_runtime_state(_durable_runtime_snapshot(
                state,
                in_progress_transaction=transaction,
                in_progress_receipts=tuple(receipts),
            ))
    state.pending_transaction_receipts[transaction.transaction_id] = tuple(receipts)
    state.tool_transactions.append(transaction.mark_delivered())
    await state.persist_runtime_state(_durable_runtime_snapshot(state))
    if batch.terminal_error is not None:
        raise batch.terminal_error
    return None


async def _run_native_step(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
    iteration: int,
) -> bool:
    if creation_turn_writes_closed(
        successful_writes=state.successful_write_count,
        failed_writes=state.failed_write_count,
    ):
        # All tool-free completion uses _complete_reply and its output contract.
        return False
    requires_category_selection = not any(
        item.get("tool") == TOOL_CATEGORY_CONTROLLER and item.get("status") == "ok"
        for item in state.tool_results
    )
    await bindings.emit_progress(
        state.on_event,
        state.progress_events,
        "model_step_started",
        "正在判断需要哪些立项能力…" if iteration == 0 else "正在根据真实工具结果继续处理…",
        {"iteration": iteration + 1, "active_categories": list(state.active_categories)},
    )
    state.schemas = bindings.tool_schemas(state.active_categories)
    state.messages = await state.prepare_model_messages(
        system_prompt=state.system_prompt,
        current_tools=state.schemas,
        current_ledger=tuple(state.current_ledger),
        delivered_transactions=tuple(state.tool_transactions),
        extra_runtime_instruction=(
            CREATION_READ_ONLY_COMPLETION_INSTRUCTION
            if state.successful_read_count and not state.write_results else ""
        ),
    )

    async def report_resume(payload: dict[str, Any]) -> None:
        await _report_stream_resume(state, bindings, payload)

    result = await bindings.complete_tool_turn(
        messages=state.messages,
        tools=state.schemas,
        model=state.model,
        temperature=0.25,
        max_tokens=state.provider_max_tokens(),
        timeout=300,
        retry=0,
        resume=8,
        on_resume=report_resume,
        extra_body=state.extra_body,
        tool_choice="required" if requires_category_selection else "auto",
    )
    record_prompt_metric(
        state.prompt_metrics,
        iteration=iteration + 1,
        phase="native",
        active_categories=state.active_categories,
        messages=state.messages,
        schemas=state.schemas,
        result=result,
    )
    # Keep every complete transaction available to later steps in this turn.
    # The shared budget counts the whole request before any further execution.
    consumed_delivered = _consume_delivered_transactions(state)
    if consumed_delivered:
        await state.persist_runtime_state(_durable_runtime_snapshot(state))
    content = str(result.get("content") or "")
    reasoning_content = str(result.get("reasoning_content") or "")
    raw_provider_state = result.get("provider_state")
    provider_state = tuple(
        dict(item)
        for item in (
            raw_provider_state if isinstance(raw_provider_state, list) else ()
        )
        if isinstance(item, dict)
    )
    raw_calls = (
        result.get("tool_calls")
        if isinstance(result.get("tool_calls"), list)
        else []
    )
    calls, arguments_by_call_id = validate_native_call_batch(
        raw_calls,
        iteration=iteration,
        allowed_tool_names=frozenset(
            str((schema.get("function") or {}).get("name") or "")
            for schema in state.schemas
            if isinstance(schema, dict)
        ),
    )
    if not calls:
        if requires_category_selection:
            raise LLMError(
                "模型没有调用本步骤唯一开放的 set_tool_categories，"
                "本轮已终止，未接受模型伪造的等待或完成回复"
            )
        state.final_reply = content.strip()
        return False
    assistant_message = {"role": "assistant", "content": content, "tool_calls": calls}
    if reasoning_content:
        assistant_message["reasoning_content"] = reasoning_content
    if provider_state:
        assistant_message["provider_state"] = list(provider_state)
    state.messages.append(assistant_message)
    state.protocol_messages.append(assistant_message)
    pending_categories = await _execute_native_calls(
        state,
        bindings,
        calls,
        arguments_by_call_id=arguments_by_call_id,
        assistant_content=content,
        assistant_reasoning_content=reasoning_content,
        assistant_provider_state=provider_state,
    )
    if pending_categories is not None:
        state.active_categories = pending_categories
    return True


async def run_native_steps(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
) -> None:
    if state.tool_mode != "native":
        return
    for iteration in count():
        if not await _run_native_step(state, bindings, iteration):
            break


def _created_project_id(state: CreationTurnState) -> str | None:
    for item in reversed(state.tool_results):
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        if item.get("tool") == "finalize_creation_session" and item.get("status") == "ok":
            candidate = str(data.get("project_id") or "").strip()
            if candidate:
                return candidate
    state.db.expire_all()
    refreshed = state.db.get(type(state.session), state.session.id)
    candidate = str(getattr(refreshed, "created_project_id", "") or "").strip()
    return candidate or None


async def _complete_reply(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
    created_project_id: str | None,
) -> None:
    if created_project_id:
        state.reply_status = "project_created"
        state.final_reply = (
            "正式作品已创建并进入作品库。请点击下方按钮进入正式作品；"
            "进入后项目助手会自动展开，后续正文与项目资料都在那里继续。"
        )
        return
    if state.failed_write_count and not state.write_results:
        state.reply_status = "receipt_only"
        state.final_reply = creation_receipt_reply(
            state.tool_results, state.write_results, tool_mode=state.tool_mode,
        )
        return
    reply_error = creation_reply_error(state.final_reply) if state.final_reply else None
    if reply_error:
        await _reject_reply(state, bindings, reply_error)
        state.final_reply = ""
    if not state.final_reply and state.tool_results and state.tool_mode != "direct_mcp":
        await _summarize_reply(state, bindings)
    if not state.final_reply:
        state.reply_status = "receipt_only"
        state.final_reply = creation_receipt_reply(
            state.tool_results, state.write_results, tool_mode=state.tool_mode,
        )
        if state.reply_diagnostics:
            state.final_reply += CREATION_REPLY_FAILURE_NOTICE
    if state.reply_status == "model" and not state.write_results and any(
        item.get("status") == "ok" and item.get("tool") in READ_TOOLS
        for item in state.tool_results
    ):
        state.reply_status = "read_only"
        state.final_reply = CREATION_READ_ONLY_NOTICE + "\n\n" + state.final_reply


async def _reject_reply(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
    reason: str,
) -> None:
    diagnostic = {"reason": reason, "attempt": len(state.reply_diagnostics) + 1}
    state.reply_diagnostics.append(diagnostic)
    await bindings.emit_progress(
        state.on_event,
        state.progress_events,
        "reply_rejected",
        "模型总结未通过校验，已保留真实执行结果",
        diagnostic,
    )
    await state.persist_runtime_state(_durable_runtime_snapshot(state))


async def _summarize_reply(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
) -> None:
    state.schemas = []
    for _ in range(max(0, CREATION_REPLY_MAX_ATTEMPTS - len(state.reply_diagnostics))):
        instruction = CREATION_REPLY_INSTRUCTION
        if state.reply_diagnostics:
            instruction += CREATION_REPLY_REPAIR_INSTRUCTION
        await bindings.emit_progress(
            state.on_event, state.progress_events, "model_step_started",
            "正在根据真实执行结果整理回复…",
            {"phase": "summary", "attempt": len(state.reply_diagnostics) + 1},
        )
        state.messages = await state.prepare_model_messages(
            system_prompt=state.system_prompt,
            current_tools=(),
            current_ledger=tuple(state.current_ledger),
            delivered_transactions=tuple(state.tool_transactions),
            extra_runtime_instruction=instruction,
        )

        async def report_resume(payload: dict[str, Any]) -> None:
            await _report_stream_resume(state, bindings, payload)

        try:
            summary = await bindings.complete_tool_turn(
                messages=state.messages, tools=[], model=state.model,
                temperature=0.2, max_tokens=state.provider_max_tokens(),
                timeout=300, retry=0, resume=8, on_resume=report_resume,
                extra_body=state.extra_body, tool_choice=None,
            )
        except ConversationContextError:
            raise
        except Exception:
            await _reject_reply(state, bindings, "summary_request_failed")
            return
        record_prompt_metric(
            state.prompt_metrics, iteration=len(state.prompt_metrics) + 1,
            phase="summary", active_categories=state.active_categories,
            messages=state.messages, schemas=[], result=summary,
        )
        if _consume_delivered_transactions(state):
            await state.persist_runtime_state(_durable_runtime_snapshot(state))
        reason = creation_reply_error(summary.get("content"), summary.get("tool_calls"))
        if reason is None:
            state.final_reply = summary["content"].strip()
            return
        await _reject_reply(state, bindings, reason)


async def _present_active_run(state: CreationTurnState) -> dict[str, Any] | None:
    active_run = None
    for item in reversed(state.tool_results):
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        candidate = data.get("run") if isinstance(data.get("run"), dict) else None
        if candidate:
            active_run = candidate
            break
    if not active_run:
        return None
    run_id = str(active_run.get("id") or active_run.get("run_id") or "").strip()
    durable_run = state.db.get(NovelCreationStageRun, run_id) if run_id else None
    if durable_run and durable_run.status in {
        "waiting_user", "waiting_author", "completed", "failed",
        "cancelled", "interrupted", "superseded",
    }:
        from app.services.novel_creation_run_presentation import present_serialized_run

        return await present_serialized_run(
            state.db,
            run=durable_run,
            model=state.model,
            assistant_reply=state.final_reply,
            tool_results=state.tool_results,
        )
    return active_run


async def finish_creation_turn(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
) -> dict[str, Any]:
    created_project_id = _created_project_id(state)
    await _complete_reply(state, bindings, created_project_id)
    # A deterministic terminal receipt also closes delivery; no model is needed
    # after project creation or when summary generation is unavailable.
    _consume_delivered_transactions(state)
    _archive_consumed_transactions(state)
    await state.persist_runtime_state(_durable_runtime_snapshot(state, status="completed"))
    for offset in range(0, len(state.final_reply), 240):
        await bindings.emit_progress(
            state.on_event,
            state.progress_events,
            "reply_delta",
            "",
            {"delta": state.final_reply[offset:offset + 240]},
        )
    active_run = await _present_active_run(state)
    turn_messages: list[dict[str, Any]] = [
        {"role": "user", "content": state.message},
        *state.protocol_messages,
        {"role": "assistant", "content": state.final_reply},
    ]
    prompt_tokens = (
        sum(
            int(item["prompt_tokens"])
            for item in state.prompt_metrics
            if item.get("prompt_tokens") is not None
        )
        if any(item.get("prompt_tokens") is not None for item in state.prompt_metrics)
        else None
    )
    turn_trace = {
        "schema": CREATION_AGENT_TURN_SCHEMA,
        "session_id": str(state.session.id),
        "model": state.model,
        "tool_mode": state.tool_mode,
        "replayable": state.tool_mode == "native",
        "messages": turn_messages,
        "progress_events": state.progress_events,
        "prompt_metrics": state.prompt_metrics,
        "direct_mcp_calls": state.direct_mcp_calls,
        "reference_context": state.reference_context,
        "execution_receipts": [
            receipt.to_dict()
            for receipt in _all_execution_receipts(state)
        ],
        "compacted_tool_transactions": state.compacted_transactions,
        "pending_tool_transactions": [
            transaction.to_dict(include_native_payload=False)
            for transaction in state.tool_transactions
        ],
        "outcome": {
            "status": "completed",
            "reply_status": state.reply_status,
            "reply_diagnostics": list(state.reply_diagnostics),
            "tool_count": len(state.tool_results),
            "write_count": len(state.write_results),
            "created_project_id": created_project_id,
            "active_categories": list(state.active_categories),
            "prompt_tokens": prompt_tokens,
            "prompt_token_steps_reported": sum(
                1 for item in state.prompt_metrics if item.get("prompt_tokens") is not None
            ),
        },
    }
    return {
        "reply": state.final_reply,
        "tool_results": state.tool_results,
        "write_count": len(state.write_results),
        "run": active_run,
        "created_project_id": created_project_id,
        "_turn_trace": turn_trace,
    }


__all__ = [
    "CREATION_AGENT_TOOLS",
    "CreationExecutionBindings",
    "CreationTurnState",
    "REVISION_TOOLS",
    "SESSION_TOOLS",
    "WRITE_TOOLS",
    "finish_creation_turn",
    "run_native_steps",
]
