"""Single-turn execution state machine for the conversational Creation Agent."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from itertools import count
from typing import Any

from sqlalchemy.orm import Session

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
from app.services.creation_agent_turn_records import (
    CREATION_AGENT_TURN_SCHEMA,
    record_prompt_metric,
    seal_creation_runtime_snapshot,
)
from app.services.workspace.executor import execute_workspace_action
from app.services.workspace.registry import registry
from app.services.workspace.tool_result_projection import (
    MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES,
    TOOL_CATEGORY_CONTROLLER_RESULT_CONTRACT,
    ToolResultBatchOverCapacity,
    ToolResultOverCapacity,
    ToolResultProjectionError,
    admit_native_assistant_transaction,
    declared_model_results_for_tool_names,
    model_tool_result_projector,
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
ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
CompleteTurn = Callable[..., Awaitable[dict[str, Any]]]
EmitProgress = Callable[..., Awaitable[None]]
PersistRuntimeState = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class CreationTurnState:
    db: Session
    session: Any
    message: str
    model: str | None
    tool_mode: str
    system_prompt: str
    prepare_model_messages: Callable[..., Awaitable[list[dict[str, Any]]]]
    provider_max_tokens: Callable[[], int | None]
    persist_runtime_state: PersistRuntimeState
    messages: list[dict[str, Any]]
    schemas: list[dict[str, Any]]
    baseline_revision: int
    extra_body: dict[str, Any] | None
    on_event: ProgressCallback | None
    reference_context: dict[str, Any] | None = None
    turn_execution_id: str = ""
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    write_results: list[dict[str, Any]] = field(default_factory=list)
    protocol_messages: list[dict[str, Any]] = field(default_factory=list)
    seen_write_calls: set[str] = field(default_factory=set)
    active_read_calls: set[str] = field(default_factory=set)
    final_reply: str = ""
    progress_events: list[dict[str, Any]] = field(default_factory=list)
    prompt_metrics: list[dict[str, Any]] = field(default_factory=list)
    direct_mcp_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_transactions: list[ToolTransaction] = field(default_factory=list)
    pending_transaction_receipts: dict[
        str, tuple[ToolExecutionReceipt, ...]
    ] = field(default_factory=dict)
    current_ledger: list[ToolExecutionReceipt] = field(default_factory=list)
    compacted_transactions: list[dict[str, Any]] = field(default_factory=list)
    native_transaction_count: int = 0
    active_categories: tuple[str, ...] = ()
    successful_write_count: int = 0
    failed_write_count: int = 0
    successful_read_count: int = 0


@dataclass(frozen=True)
class CreationExecutionBindings:
    complete_tool_turn: CompleteTurn
    emit_progress: EmitProgress
    tool_schemas: Callable[[tuple[str, ...]], list[dict[str, Any]]]
    category_tool_result: Callable[
        [dict[str, Any]],
        tuple[dict[str, Any], tuple[str, ...] | None],
    ]


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


def _consume_delivered_transactions(state: CreationTurnState) -> None:
    """Replace model-consumed native batches with compact server receipts."""

    consumed_any = bool(state.tool_transactions)
    for transaction in state.tool_transactions:
        compactable = transaction.mark_consumed().mark_compactable()
        receipts = state.pending_transaction_receipts.pop(
            transaction.transaction_id,
            (),
        )
        state.current_ledger.extend(receipts)
        state.compacted_transactions.append(
            compactable.to_dict(include_native_payload=False)
        )
    state.tool_transactions.clear()
    if consumed_any:
        # A successful next provider response proves that the read result was
        # consumed.  The model may now safely re-read the same target if later
        # reasoning requires fresh state; writes remain permanently deduped.
        state.active_read_calls.clear()


def _all_execution_receipts(
    state: CreationTurnState,
) -> tuple[ToolExecutionReceipt, ...]:
    return (
        *state.current_ledger,
        *(
            receipt
            for transaction in state.tool_transactions
            for receipt in state.pending_transaction_receipts.get(
                transaction.transaction_id,
                (),
            )
        ),
    )


def _durable_runtime_snapshot(
    state: CreationTurnState,
    *,
    status: str = "running",
    in_progress_transaction: ToolTransaction | None = None,
    in_progress_receipts: tuple[ToolExecutionReceipt, ...] = (),
) -> dict[str, Any]:
    """Serialize only server state needed to audit/recover an interrupted turn."""

    return seal_creation_runtime_snapshot({
        "session_id": str(state.session.id),
        "status": status,
        "tool_mode": state.tool_mode,
        "tool_results": list(state.tool_results),
        "execution_receipts": [
            receipt.to_dict()
            for receipt in (
                *_all_execution_receipts(state),
                *in_progress_receipts,
            )
        ],
        "compacted_tool_transactions": list(state.compacted_transactions),
        "pending_tool_transactions": [
            transaction.to_dict()
            for transaction in (
                *state.tool_transactions,
                *(
                    (in_progress_transaction,)
                    if in_progress_transaction is not None
                    else ()
                ),
            )
        ],
        "successful_write_count": state.successful_write_count,
        "failed_write_count": state.failed_write_count,
        "successful_read_count": state.successful_read_count,
        "reference_context": state.reference_context,
        "turn_execution_id": state.turn_execution_id,
    })


@dataclass(frozen=True)
class _RuntimeResultTool:
    """Explicit contract for the controller and rejected unknown tool calls."""

    name: str
    model_result_contract: Any = TOOL_CATEGORY_CONTROLLER_RESULT_CONTRACT


def _tool_message_content(name: str, result: dict[str, Any]) -> str:
    """Apply the one declarative model-result projection path."""

    tool = registry.get(name) or _RuntimeResultTool(name=name)
    try:
        return model_tool_result_projector.project(
            tool,
            result,
            max_json_bytes=MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES,
        ).content
    except ToolResultOverCapacity as exc:
        return json.dumps(
            exc.model_error_result(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except ToolResultProjectionError:
        return json.dumps(
            safe_creation_tool_result(name, {
                "status": "error",
                "data": {"reason": "model_result_projection_failed"},
            }),
            ensure_ascii=False,
            separators=(",", ":"),
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


def _prepare_native_batch(
    state: CreationTurnState,
    calls: list[dict[str, Any]],
    *,
    assistant_content: str,
    assistant_reasoning_content: str,
    assistant_provider_state: tuple[dict[str, Any], ...],
) -> _PreparedNativeBatch:
    if state.tool_transactions:
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
    assistant_payload: dict[str, Any] = {
        "role": "assistant",
        "content": assistant_content,
        "tool_calls": calls,
    }
    if assistant_reasoning_content:
        assistant_payload["reasoning_content"] = assistant_reasoning_content
    if assistant_provider_state:
        assistant_payload["provider_state"] = list(assistant_provider_state)
    try:
        admit_native_assistant_transaction(assistant_payload, resolved)
    except ToolResultBatchOverCapacity as exc:
        rejection = exc
        if exc.reason != "tool_result_batch_over_capacity":
            terminal_error = ConversationContextError(
                ConversationContextErrorCode.PROTOCOL_INVALID,
                "模型返回的原生工具事务超过可验证协议容量，本批次未执行。",
                details={
                    "reason": exc.reason,
                    "call_count": exc.call_count,
                    "actual_bytes": exc.declared_json_bytes,
                    "max_bytes": exc.max_json_bytes,
                    "remediation": "减少单步工具调用、参数或模型推理状态后重试。",
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
    tool_result = safe_creation_tool_result(name, tool_result)
    state.tool_results.append(tool_result)
    if name != TOOL_CATEGORY_CONTROLLER:
        completed = creation_tool_completed_event(name, arguments, tool_result)
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
    ):
        state.successful_read_count += 1
    native_result, receipt = build_creation_execution_receipt(
        session_id=str(state.session.id),
        turn_execution_id=state.turn_execution_id,
        transaction_number=transaction_number,
        call=native_call,
        result=tool_result,
        model_content=_tool_message_content(name, tool_result),
        read_tools=READ_TOOLS,
        write_tools=WRITE_TOOLS,
        write_success_statuses=CREATION_WRITE_SUCCESS_STATUSES,
    )
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
    batch = _prepare_native_batch(
        state,
        calls,
        assistant_content=assistant_content,
        assistant_reasoning_content=assistant_reasoning_content,
        assistant_provider_state=assistant_provider_state,
    )
    available = set(tool_names_for_categories(state.active_categories)) & CREATION_AGENT_TOOLS
    reads_ready_before_step = state.successful_read_count > 0
    transaction = batch.transaction
    receipts: list[ToolExecutionReceipt] = []
    for call_index, (call, native_call) in enumerate(
        zip(calls, batch.native_calls, strict=True)
    ):
        arguments = dict(arguments_by_call_id[native_call.call_id])
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
    writes_closed = creation_turn_writes_closed(
        successful_writes=state.successful_write_count,
        failed_writes=state.failed_write_count,
    )
    # Once the deterministic mutation boundary closes, ask for the final text
    # with no tools. This prevents a compliant model from spending another
    # planning step on reads or downstream writes.
    state.schemas = [] if writes_closed else bindings.tool_schemas(state.active_categories)
    state.messages = await state.prepare_model_messages(
        system_prompt=state.system_prompt,
        current_tools=state.schemas,
        current_ledger=tuple(state.current_ledger),
        delivered_transactions=tuple(state.tool_transactions),
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
    # A successful provider response proves that the immediately preceding
    # delivered native batch was consumed. Replace it before adding any new
    # tool calls so raw assistant/tool payloads never accumulate by step.
    consumed_delivered = bool(state.tool_transactions)
    _consume_delivered_transactions(state)
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


def truthful_no_write_reply(state: CreationTurnState) -> str:
    failures = [
        str(item.get("detail") or "工具未完成")
        for item in state.tool_results
        if item.get("status") not in {"ok", "running"}
    ]
    reads = [
        item for item in state.tool_results
        if item.get("status") == "ok" and item.get("tool") not in WRITE_TOOLS
    ]
    if failures:
        return f"本轮没有保存任何修改：{failures[-1]}。请调整要求后重试。"
    if reads:
        return "本轮只完成了立项工具读取，没有保存任何修改。请明确要写入的对象和内容后重试。"
    if state.tool_mode == "direct_mcp":
        return "本轮没有获得可验证的 MCP 结果，因此无法确认读取或修改了立项数据。请重试。"
    if state.tool_results:
        return "本轮执行了立项工具，但没有产生可确认的写入。请调整要求后重试。"
    return "本轮未执行任何立项工具，因此没有读取或修改立项数据。请重试。"


async def _complete_reply(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
    created_project_id: str | None,
) -> None:
    if created_project_id:
        state.final_reply = (
            "正式作品已创建并进入作品库。请点击下方按钮进入正式作品；"
            "进入后项目助手会自动展开，后续正文与项目资料都在那里继续。"
        )
        return
    if not state.final_reply and state.tool_results and state.tool_mode != "direct_mcp":
        summary_instruction = (
            "请根据以上真实工具返回，用两到四句中文说明本轮实际修改了什么、"
            "哪些内容没有修改，并提出一个基于当前立项数据的后续问题。"
            "不得声称未成功的写入已经保存。"
        )
        state.messages = await state.prepare_model_messages(
            system_prompt=state.system_prompt,
            current_tools=(),
            current_ledger=tuple(state.current_ledger),
            delivered_transactions=tuple(state.tool_transactions),
            extra_runtime_instruction=summary_instruction,
        )

        async def report_resume(payload: dict[str, Any]) -> None:
            await _report_stream_resume(state, bindings, payload)

        try:
            summary = await bindings.complete_tool_turn(
                messages=state.messages,
                tools=[],
                model=state.model,
                temperature=0.2,
                max_tokens=state.provider_max_tokens(),
                timeout=300,
                retry=0,
                resume=8,
                on_resume=report_resume,
            )
            record_prompt_metric(
                state.prompt_metrics,
                iteration=len(state.prompt_metrics) + 1,
                phase="summary",
                active_categories=state.active_categories,
                messages=state.messages,
                schemas=[],
                result=summary,
            )
            consumed_delivered = bool(state.tool_transactions)
            _consume_delivered_transactions(state)
            if consumed_delivered:
                await state.persist_runtime_state(_durable_runtime_snapshot(state))
            state.final_reply = str(summary.get("content") or "").strip()
        except Exception:
            state.final_reply = ""
    if not state.final_reply:
        if state.write_results:
            details = [
                str(item.get("detail") or item.get("tool") or "已更新立项数据")
                for item in state.write_results[:3]
            ]
            state.final_reply = f"本轮已完成：{'；'.join(details)}。接下来你最想补充哪一部分？"
        else:
            state.final_reply = truthful_no_write_reply(state)


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
