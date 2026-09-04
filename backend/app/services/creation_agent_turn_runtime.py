"""Detached, reconnectable SSE runtime for conversational creation turns."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session
from app.database.session import SessionLocal
from app.modules.assistant.application.system_conversations import SystemConversationStore
from app.modules.assistant.infrastructure.system_conversations import (
    SqlAlchemySystemConversationStore,
)
from app.modules.creation.interfaces.session_dependencies import novel_creation_session_store
from app.services.context_orchestrator import ContextOrchestrator
from app.services.conversation_context import (
    ConversationContextError,
    ConversationIdentity,
    ConversationKind,
    ModelToolCapability,
    ReferenceContext,
    ToolExecutionReceipt,
    ToolTransaction,
    prepare_conversation_context,
)
from app.services.conversation_context.canonical import canonical_sha256
from app.services.conversation_context.checkpoint_state import (
    safe_public_error_detail,
)
from app.services.creation_agent_turn_records import (
    creation_agent_turn_records,
    creation_checkpoint_source_turns,
    creation_current_user_context_message,
    creation_execution_ledger_from_conversation,
    creation_turns_as_context_turns,
    seal_creation_runtime_snapshot,
    validate_creation_runtime_snapshot,
)
from app.services.model_readiness import sanitize_readiness_message
from app.services.novel_creation_agent import run_creation_agent
from app.services.observability.run_events import classify_failure
from app.services.persistence.assistant_workspace import SqlAlchemyAssistantWorkspace
from app.services.workspace.registry import registry
from app.services.workspace.tool_result_projection import (
    max_model_visible_result_tokens_for_open_tool_schemas,
    max_native_tool_transaction_wrapper_tokens,
)

TurnPublisher = Callable[[dict[str, Any]], Awaitable[None]]
TurnProducer = Callable[[TurnPublisher], Awaitable[None]]

_TURN_RETENTION_SECONDS = 15 * 60
_HEARTBEAT_SECONDS = 10
logger = logging.getLogger(__name__)


class CreationTurnScopeError(RuntimeError):
    """A requested conversation is not owned by the creation session."""


class CreationTurnSuperseded(RuntimeError):
    """A newer durable Creation turn has replaced this producer."""


@dataclass(frozen=True)
class CreationAgentTurnInput:
    session_id: str
    message: str
    client_turn_id: str
    model: str | None
    conversation_id: str | None
    assistant_message_id: str | None
    local_cli_read_paths: tuple[str, ...]
    reference_context: ReferenceContext | None = None
    request_provider: Any = None


@dataclass
class _TurnContext:
    request: CreationAgentTurnInput
    db: Session
    conversations: SystemConversationStore
    conversation_id: str | None
    assistant_message_id: str | None
    conversation_detail: dict[str, Any] | None = None
    audit_events: list[dict[str, Any]] = field(default_factory=list)


def _reference_context_payload(
    request: CreationAgentTurnInput,
) -> dict[str, Any] | None:
    if request.reference_context is None:
        return None
    return request.reference_context.model_dump(mode="json")


def creation_agent_conversation(
    conversations: SystemConversationStore,
    *,
    session_id: str,
    conversation_id: str | None,
) -> dict[str, Any] | None:
    detail: dict[str, Any] | None = None
    if conversation_id:
        detail = conversations.get(conversation_id)
    else:
        listed = conversations.list(scope_type="creation", scope_id=session_id)
        items = listed.get("items") if isinstance(listed, dict) else []
        if isinstance(items, list) and items:
            candidate_id = str((items[0] or {}).get("id") or "").strip()
            if candidate_id:
                detail = conversations.get(candidate_id)
    if detail is None:
        return None
    conversation = detail.get("conversation") if isinstance(detail, dict) else None
    if (
        str((conversation or {}).get("scope_type") or "") != "creation"
        or str((conversation or {}).get("scope_id") or "") != session_id
    ):
        raise CreationTurnScopeError("系统助手对话不属于当前立项会话")
    return detail


def _persist_creation_agent_turn(
    context: _TurnContext,
    *,
    assistant_content: str,
    status: str,
    trace: dict[str, Any],
    run: dict[str, Any] | None,
    result: dict[str, Any],
) -> None:
    if not context.conversation_id or not context.assistant_message_id:
        raise CreationTurnScopeError("立项助手消息尚未绑定到系统对话")
    messages = (context.conversation_detail or {}).get("messages") or []
    if not any(
        isinstance(item, dict)
        and item.get("id") == context.assistant_message_id
        and item.get("role") == "assistant"
        for item in messages
    ):
        raise CreationTurnScopeError("立项助手消息不属于当前系统对话")
    payload: dict[str, Any] = {
        "creation_agent_turn": trace,
        "creation_agent_result": result,
        "creation_agent_runtime": seal_creation_runtime_snapshot({
            "session_id": context.request.session_id,
            "status": "completed",
            "tool_mode": trace.get("tool_mode"),
            "tool_results": list(result.get("tool_results") or ()),
            "execution_receipts": list(trace.get("execution_receipts") or ()),
            "compacted_tool_transactions": list(
                trace.get("compacted_tool_transactions") or ()
            ),
            "pending_tool_transactions": list(
                trace.get("pending_tool_transactions") or ()
            ),
            "client_turn_id": context.request.client_turn_id,
            "reference_context": _reference_context_payload(context.request),
        }),
    }
    reference_context = _reference_context_payload(context.request)
    if reference_context is not None:
        payload["creation_agent_reference_context"] = reference_context
    if run:
        payload["run"] = run
    context.conversations.finish_turn(
        context.conversation_id,
        context.assistant_message_id,
        {
            "assistant_content": assistant_content,
            "status": status,
            "creation_session_id": context.request.session_id,
            "scope_type": "creation",
            "scope_id": context.request.session_id,
            "run_id": (run or {}).get("id") or (run or {}).get("run_id"),
            "operation_id": (run or {}).get("operation_id"),
            "message_type": "operation" if run else "text",
            "payload": payload,
        },
    )


def safe_creation_agent_error(exc: Exception) -> tuple[str, dict[str, Any]]:
    if isinstance(exc, ConversationContextError):
        code = exc.code.value
        message = safe_public_error_detail(exc.code) or (
            "对话上下文处理失败，本次任务未执行。"
        )
        next_action = (
            "请缩小当前请求、检查模型容量或重试上下文整理；"
            "在上下文通过校验前不会执行任何业务工具。"
        )
        return message, {
            "error_type": "conversation_context",
            "failure_class": "conversation_context",
            "code": code,
            "message": message,
            "details": {"remediation": next_action},
            "next_action": next_action,
        }
    failure_class = classify_failure(str(exc)) or "unknown"
    message, next_action = {
        "quota_or_rate_limit": (
            "模型额度已耗尽或请求受限",
            "请等待额度恢复，或切换到有额度的模型后重试。",
        ),
        "auth": ("模型授权已失效", "请到模型设置重新登录或填写凭据，测试成功后重试。"),
        "timeout": ("模型响应超时", "后台状态已保留，可稍后重试或切换更快的模型。"),
        "network": ("模型网络连接中断", "请检查网络或本机模型进程后重试。"),
        "empty_response": ("模型没有返回有效内容", "请重试本轮或切换模型。"),
        "invalid_response": ("模型返回格式无法解析", "请重试本轮或切换模型。"),
    }.get(failure_class, ("立项助手处理失败", "请检查模型状态后重试本轮。"))
    return message, {
        "error_type": type(exc).__name__,
        "failure_class": failure_class,
        "next_action": next_action,
    }


async def _emit(
    context: _TurnContext,
    publish: TurnPublisher,
    event: dict[str, Any],
) -> None:
    safe_event = {
        "type": str(event.get("type") or ""),
        "message": str(event.get("message") or "")[:500],
        "data": dict(event.get("data") or {}),
    }
    context.audit_events.append(safe_event)
    await publish(safe_event)


async def _recover_existing_turn(
    context: _TurnContext,
    publish: TurnPublisher,
) -> bool:
    interrupted_message: dict[str, Any] | None = None
    for stored_message in reversed((context.conversation_detail or {}).get("messages") or []):
        if not isinstance(stored_message, dict) or stored_message.get("role") != "assistant":
            continue
        stored_payload = stored_message.get("payload")
        trace = (
            stored_payload.get("creation_agent_turn")
            if isinstance(stored_payload, dict)
            else None
        )
        if (
            isinstance(trace, dict)
            and trace.get("client_turn_id") == context.request.client_turn_id
        ):
            stored_result = stored_payload.get("creation_agent_result")
            if isinstance(stored_result, dict):
                await _emit(context, publish, {
                    "type": "complete",
                    "message": "已恢复本轮最终结果",
                    "data": stored_result,
                })
                return True
        marker = (
            stored_payload.get("creation_agent_client_turn_id")
            if isinstance(stored_payload, dict)
            else None
        )
        stored_error = (
            stored_payload.get("creation_agent_error")
            if isinstance(stored_payload, dict)
            else None
        )
        if (
            marker == context.request.client_turn_id
            and isinstance(stored_error, dict)
            and stored_error.get("error_type") == "conversation_context"
        ):
            try:
                persisted_context_error = ConversationContextError(
                    str(stored_error.get("code") or ""),
                    "persisted context error",
                )
            except (TypeError, ValueError):
                persisted_context_error = ConversationContextError(
                    "conversation_protocol_invalid",
                    "invalid persisted context error",
                )
            public_message, public_data = safe_creation_agent_error(
                persisted_context_error
            )
            await _emit(context, publish, {
                "type": "error",
                "message": public_message,
                "data": public_data,
            })
            return True
        if marker == context.request.client_turn_id and interrupted_message is None:
            interrupted_message = stored_message
    if interrupted_message is None:
        return False
    interrupted_payload = interrupted_message.get("payload")
    runtime_snapshot = validate_creation_runtime_snapshot(
        (
            interrupted_payload.get("creation_agent_runtime")
            if isinstance(interrupted_payload, dict)
            else None
        ),
        session_id=context.request.session_id,
    )
    receipts = (
        runtime_snapshot.get("execution_receipts")
        if isinstance(runtime_snapshot, dict)
        and isinstance(runtime_snapshot.get("execution_receipts"), list)
        else []
    )
    committed_receipts = [
        receipt
        for receipt in receipts
        if isinstance(receipt, dict) and receipt.get("write_committed") is True
    ]
    recovery_message = (
        (
            "上次服务进程中断前已有写入完成并留下服务器回执；"
            if committed_receipts
            else "上次服务进程在本轮完成前中断；"
        )
        + "为避免重复写入，本次不会重新执行。请检查当前立项资料后发送一条新消息。"
    )
    recovery_data = {
        "error_type": "turn_recovery_required",
        "failure_class": "interrupted",
        "next_action": "检查当前 revision 和已保存资料，然后使用新的请求继续。",
        "runtime_receipt_count": len(receipts),
        "committed_write_count": len(committed_receipts),
        "runtime_source_hash": (
            runtime_snapshot.get("source_hash")
            if isinstance(runtime_snapshot, dict)
            else None
        ),
        "result_refs": [
            str(receipt.get("result_ref") or "")
            for receipt in receipts
            if isinstance(receipt, dict) and receipt.get("result_ref")
        ],
    }
    conversation = (context.conversation_detail or {}).get("conversation") or {}
    conversation_id = str(conversation.get("id") or "")
    assistant_id = str(interrupted_message.get("id") or "")
    if conversation_id and assistant_id:
        context.conversations.finish_turn(conversation_id, assistant_id, {
            "assistant_content": recovery_message,
            "status": "error",
            "creation_session_id": context.request.session_id,
            "scope_type": "creation",
            "scope_id": context.request.session_id,
            "payload": {
                "creation_agent_client_turn_id": context.request.client_turn_id,
                "creation_agent_error": recovery_data,
                "creation_agent_recovery": recovery_data,
            },
        })
        commit_session(context.db)
    await _emit(context, publish, {
        "type": "error",
        "message": recovery_message,
        "data": recovery_data,
    })
    return True


def _bind_pending_turn(context: _TurnContext) -> None:
    request = context.request
    if context.assistant_message_id and context.conversation_id:
        messages = (context.conversation_detail or {}).get("messages") or []
        if not any(
            isinstance(item, dict)
            and item.get("id") == context.assistant_message_id
            and item.get("role") == "assistant"
            for item in messages
        ):
            raise CreationTurnScopeError("立项助手消息不属于当前系统对话")
    else:
        if not context.conversation_id:
            context.conversation_id = str(
                ((context.conversation_detail or {}).get("conversation") or {}).get("id") or ""
            ).strip()
        if not context.conversation_id:
            created = context.conversations.create(
                request.message[:36],
                scope_type="creation",
                scope_id=request.session_id,
            )
            context.conversation_id = str((created.get("conversation") or {}).get("id") or "")
        started = context.conversations.start_turn(context.conversation_id, {
            "user_content": request.message,
            "creation_session_id": request.session_id,
            "scope_type": "creation",
            "scope_id": request.session_id,
            "payload": {
                "creation_agent_client_turn_id": request.client_turn_id,
                "creation_agent_reference_context": _reference_context_payload(request),
            },
        })
        messages = started.get("messages") if isinstance(started, dict) else []
        context.assistant_message_id = str((messages or [{}, {}])[-1].get("id") or "")
        commit_session(context.db)
        context.conversation_detail = context.conversations.get(context.conversation_id)
    context.conversations.finish_turn(
        context.conversation_id,
        context.assistant_message_id,
        {
            "assistant_content": "",
            "status": "running",
            "creation_session_id": request.session_id,
            "scope_type": "creation",
            "scope_id": request.session_id,
            "payload": {
                "creation_agent_client_turn_id": request.client_turn_id,
                "creation_agent_reference_context": _reference_context_payload(request),
            },
        },
    )
    commit_session(context.db)


@dataclass
class _CreationModelContextRuntime:
    context: _TurnContext
    publish: TurnPublisher
    output_reserve_tokens: int | None = None

    def require_current_turn(self) -> None:
        context = self.context
        if not context.conversation_id or not context.assistant_message_id:
            raise CreationTurnScopeError("立项助手消息尚未绑定到系统对话")
        context.db.expire_all()
        detail = context.conversations.get(context.conversation_id)
        current = next(
            (
                message
                for message in (detail.get("messages") or [])
                if isinstance(message, dict)
                and str(message.get("id") or "") == context.assistant_message_id
            ),
            None,
        )
        if current is None or str(current.get("status") or "") != "running":
            raise CreationTurnSuperseded("立项任务已被更新的作者消息替换")
        context.conversation_detail = detail

    def load_projection(self):
        context = self.context
        request = context.request
        if not context.conversation_id or not context.assistant_message_id:
            raise CreationTurnScopeError("立项助手消息尚未绑定到系统对话")
        context.db.expire_all()
        detail = context.conversations.get(context.conversation_id)
        context.conversation_detail = detail
        current_user = creation_current_user_context_message(
            detail,
            assistant_message_id=context.assistant_message_id,
            expected_content=request.message,
        )
        if current_user is None:
            raise CreationTurnScopeError("无法从系统对话恢复本轮完整作者消息")
        turns = creation_turns_as_context_turns(creation_agent_turn_records(
            detail,
            session_id=request.session_id,
            exclude_assistant_message_id=context.assistant_message_id,
        ))
        execution_ledger = creation_execution_ledger_from_conversation(
            detail,
            session_id=request.session_id,
        )
        return current_user, turns, execution_ledger

    async def emit_context_event(self, event: str, payload: dict[str, Any]) -> None:
        await _emit(self.context, self.publish, {
            "type": event,
            "message": (
                "正在整理较早的完整对话…"
                if event == "conversation_checkpoint"
                else "已按当前模型容量准备对话上下文"
            ),
            "data": payload,
        })

    @staticmethod
    def _budget_tool_schemas(
        *,
        protocol: str,
        current_tools: Sequence[Mapping[str, Any]],
        provider_protocol_state: Any,
    ) -> tuple[Mapping[str, Any], ...]:
        if protocol != "direct_mcp":
            return tuple(current_tools)
        if not isinstance(provider_protocol_state, Mapping):
            raise CreationTurnScopeError("Direct MCP 缺少可验证的协议状态")
        if provider_protocol_state.get("protocol") != "direct_mcp":
            raise CreationTurnScopeError("Direct MCP 协议状态不匹配")
        schemas = provider_protocol_state.get("tool_schemas")
        if not isinstance(schemas, (list, tuple)) or not all(
            isinstance(schema, Mapping) for schema in schemas
        ):
            raise CreationTurnScopeError("Direct MCP 工具 Schema 无法验证")
        return tuple(schemas)

    async def prepare_model_messages(
        self,
        *,
        model: str | None,
        protocol: str,
        system_prompt: str,
        current_tools: Sequence[Mapping[str, Any]] = (),
        current_ledger: Sequence[ToolExecutionReceipt] = (),
        delivered_transactions: Sequence[ToolTransaction] = (),
        provider_protocol_state: Any = None,
        provider_state: Any = None,
        extra_runtime_instruction: str = "",
    ) -> list[dict[str, Any]]:
        context = self.context
        request = context.request
        current_user, turns, execution_ledger = self.load_projection()
        budget_tool_schemas = self._budget_tool_schemas(
            protocol=protocol,
            current_tools=current_tools,
            provider_protocol_state=provider_protocol_state,
        )
        result_token_reserve = max_model_visible_result_tokens_for_open_tool_schemas(
            budget_tool_schemas,
            resolve_tool=registry.get,
        )
        conversation = ConversationIdentity(
            kind=ConversationKind.CREATION,
            id=str(context.conversation_id),
            revision=current_user.sequence_no,
            creation_session_id=request.session_id,
        )

        def reload_turns():
            context.db.expire_all()
            detail = context.conversations.get(str(context.conversation_id))
            return creation_checkpoint_source_turns(
                detail,
                session_id=request.session_id,
                before_sequence=current_user.sequence_no,
            )

        prepared = await prepare_conversation_context(
            store=SqlAlchemyAssistantWorkspace(context.db),
            orchestrator=ContextOrchestrator(context.db),
            conversation=conversation,
            owner_id=request.session_id,
            turns=turns,
            current_user_message=current_user,
            model=model,
            task_type="new_project",
            protocol=protocol,
            system_prompt=system_prompt,
            current_tools=tuple(current_tools),
            reload_turns=reload_turns,
            active_tool_category_hash=(
                canonical_sha256(list(budget_tool_schemas))
                if protocol == "direct_mcp"
                else None
            ),
            current_ledger=tuple(current_ledger),
            delivered_transactions=tuple(delivered_transactions),
            trusted_execution_ledger=execution_ledger.entries,
            execution_source_hashes=execution_ledger.source_hashes,
            provider_protocol_state=provider_protocol_state,
            provider_state=provider_state,
            extra_runtime_instruction=extra_runtime_instruction,
            max_model_visible_result_tokens_for_open_tools=result_token_reserve,
            next_step_wrapper=(
                max_native_tool_transaction_wrapper_tokens()
                if protocol == "native" and current_tools
                else 0
            ),
            model_capability=ModelToolCapability(
                supports_native_tool_calling=protocol == "native",
                direct_mcp_validated=protocol == "direct_mcp",
            ),
            event_sink=self.emit_context_event,
        )
        # Persist the shared context phase (including its latest budget
        # snapshot) before expiring the identity map to observe a concurrent
        # superseding author turn.  Otherwise ``expire_all`` can silently
        # discard the freshly prepared budget record.
        commit_session(context.db)
        self.require_current_turn()
        output_reserve = getattr(
            getattr(prepared, "budget", None),
            "output_reserve_tokens",
            None,
        )
        if (
            not isinstance(output_reserve, int)
            or isinstance(output_reserve, bool)
            or output_reserve <= 0
        ):
            raise CreationTurnScopeError("共享上下文未返回可验证的模型输出预算")
        self.output_reserve_tokens = output_reserve
        return prepared.provider_messages

    async def persist_runtime_state(self, snapshot: dict[str, Any]) -> None:
        context = self.context
        request = context.request
        validated = validate_creation_runtime_snapshot(
            snapshot,
            session_id=request.session_id,
        )
        if validated is None:
            raise CreationTurnScopeError("立项工具事务快照校验失败")
        self.require_current_turn()
        context.conversations.finish_turn(
            str(context.conversation_id),
            str(context.assistant_message_id),
            {
                "assistant_content": "",
                "status": "running",
                "creation_session_id": request.session_id,
                "scope_type": "creation",
                "scope_id": request.session_id,
                "payload": {
                    "creation_agent_client_turn_id": request.client_turn_id,
                    "creation_agent_runtime": validated,
                    "creation_agent_reference_context": (
                        _reference_context_payload(request)
                    ),
                },
            },
        )
        commit_session(context.db)
        context.conversation_detail = context.conversations.get(
            str(context.conversation_id)
        )


async def _execute_agent(
    context: _TurnContext,
    source_session: Any,
    publish: TurnPublisher,
) -> None:
    request = context.request
    await _emit(context, publish, {
        "type": "turn_started",
        "message": "立项对话已绑定，正在调用模型…",
        "data": {
            "session_id": request.session_id,
            "conversation_id": context.conversation_id,
            "assistant_message_id": context.assistant_message_id,
        },
    })
    if not context.conversation_id or not context.assistant_message_id:
        raise CreationTurnScopeError("立项助手消息尚未绑定到系统对话")
    model_context = _CreationModelContextRuntime(context, publish)

    async def invoke() -> dict[str, Any]:
        return await run_creation_agent(
            context.db,
            session=source_session,
            message=request.message,
            model=request.model,
            prepare_model_messages=model_context.prepare_model_messages,
            persist_runtime_state=model_context.persist_runtime_state,
            local_cli_read_paths=list(request.local_cli_read_paths),
            reference_context=request.reference_context,
            turn_execution_id=request.client_turn_id,
            provider_max_tokens=lambda: model_context.output_reserve_tokens,
            on_event=lambda event: _emit(context, publish, event),
            direct_mcp_turn_guard={
                "kind": "creation",
                "session_id": request.session_id,
                "conversation_id": str(context.conversation_id),
                "assistant_message_id": str(context.assistant_message_id),
            },
        )

    if request.request_provider is None:
        result = await invoke()
    else:
        from app.modules.model_runtime.application.request_override import use_request_provider

        with use_request_provider(request.request_provider):
            result = await invoke()
    model_context.require_current_turn()
    trace = result.pop("_turn_trace")
    trace["client_turn_id"] = request.client_turn_id
    trace["progress_events"] = [
        *context.audit_events,
        {"type": "complete", "message": "本轮立项处理完成", "data": {}},
    ]
    run = result.get("run") if isinstance(result.get("run"), dict) else None
    outcome = trace.get("outcome") if isinstance(trace, dict) else None
    message_status = (
        "error"
        if isinstance(outcome, dict) and outcome.get("status") == "protocol_error"
        else "running"
        if run and run.get("status") in {"queued", "running"}
        else "completed"
    )
    result.update({
        "message_status": message_status,
        "conversation_id": context.conversation_id,
        "assistant_message_id": context.assistant_message_id,
        "turn_persisted": True,
    })
    _persist_creation_agent_turn(
        context,
        assistant_content=str(result.get("reply") or ""),
        status=message_status,
        trace=trace,
        run=run,
        result=result,
    )
    commit_session(context.db)
    await publish({
        "type": "complete",
        "message": "本轮立项处理完成",
        "data": result,
    })


async def _persist_turn_error(
    context: _TurnContext,
    publish: TurnPublisher,
    exc: Exception,
) -> None:
    context.db.rollback()
    safe_message, safe_error_data = safe_creation_agent_error(exc)
    error_id = uuid4().hex
    safe_error_data["error_id"] = error_id
    if safe_error_data.get("failure_class") == "unknown":
        safe_message = f"{safe_message}；错误编号：{error_id}"
    logger.error(
        "Creation turn failed error_id=%s error_type=%s detail=%s",
        error_id, type(exc).__name__, sanitize_readiness_message(exc, limit=2000),
    )
    if context.conversation_id and context.assistant_message_id:
        try:
            context.conversations.finish_turn(
                context.conversation_id,
                context.assistant_message_id,
                {
                    "assistant_content": safe_message,
                    "status": "error",
                    "creation_session_id": context.request.session_id,
                    "scope_type": "creation",
                    "scope_id": context.request.session_id,
                    "payload": {
                        "creation_agent_client_turn_id": context.request.client_turn_id,
                        "creation_agent_reference_context": (
                            _reference_context_payload(context.request)
                        ),
                        "creation_agent_error": {
                            "type": type(exc).__name__,
                            "message": safe_message,
                            **safe_error_data,
                            "client_turn_id": context.request.client_turn_id,
                            "replayable": False,
                        },
                        "creation_agent_progress": [
                            *context.audit_events,
                            {"type": "error", "message": safe_message, "data": safe_error_data},
                        ],
                    },
                },
            )
            commit_session(context.db)
        except Exception:
            context.db.rollback()
    await publish({"type": "error", "message": safe_message, "data": safe_error_data})


async def produce_creation_agent_turn(
    request: CreationAgentTurnInput,
    publish: TurnPublisher,
) -> None:
    source_db = SessionLocal()
    context = _TurnContext(
        request=request,
        db=source_db,
        conversations=SqlAlchemySystemConversationStore(source_db),
        conversation_id=request.conversation_id,
        assistant_message_id=request.assistant_message_id,
    )
    await _emit(context, publish, {
        "type": "turn_started",
        "message": "已接收请求，正在准备立项上下文…",
        "data": {"session_id": request.session_id},
    })
    try:
        source_session = novel_creation_session_store(source_db).session(request.session_id)
        if not source_session:
            raise RuntimeError("立项草稿不存在")
        context.conversation_detail = creation_agent_conversation(
            context.conversations,
            session_id=request.session_id,
            conversation_id=context.conversation_id,
        )
        if await _recover_existing_turn(context, publish):
            return
        _bind_pending_turn(context)
        await _execute_agent(context, source_session, publish)
    except CreationTurnSuperseded:
        await publish(
            {
                "type": "superseded",
                "message": "本轮已被更新的作者消息替换，未继续执行模型或业务工具。",
                "data": {
                    "conversation_id": context.conversation_id,
                    "assistant_message_id": context.assistant_message_id,
                },
            }
        )
    except Exception as exc:
        await _persist_turn_error(context, publish, exc)
    finally:
        source_db.close()


@dataclass
class _CreationTurnExecution:
    client_turn_id: str
    request_fingerprint: str
    sequence_base: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[Any] | None = None
    done: bool = False
    last_publish_at: float = field(default_factory=time.monotonic)
    started_at: float = field(default_factory=time.monotonic)

    async def publish(self, event: dict[str, Any]) -> None:
        async with self.condition:
            payload = {
                **event,
                "client_turn_id": self.client_turn_id,
                "sequence": self.sequence_base + len(self.events) + 1,
            }
            self.events.append(payload)
            self.last_publish_at = time.monotonic()
            self.condition.notify_all()

    async def finish(self) -> None:
        async with self.condition:
            self.done = True
            self.condition.notify_all()


_EXECUTIONS: dict[str, _CreationTurnExecution] = {}
_EXECUTIONS_LOCK = asyncio.Lock()


async def _run_execution(execution: _CreationTurnExecution, producer: TurnProducer) -> None:
    heartbeat_task: asyncio.Task[Any] | None = None

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_SECONDS)
            if execution.done:
                return
            if time.monotonic() - execution.last_publish_at < _HEARTBEAT_SECONDS:
                continue
            elapsed = int(time.monotonic() - execution.started_at)
            await execution.publish({
                "type": "heartbeat",
                "message": f"模型仍在响应，已等待 {elapsed} 秒",
                "data": {"elapsed_seconds": elapsed},
            })

    try:
        heartbeat_task = asyncio.create_task(
            heartbeat(),
            name=f"creation-turn-heartbeat-{execution.client_turn_id}",
        )
        await producer(execution.publish)
    except asyncio.CancelledError:
        await execution.publish({
            "type": "cancelled",
            "message": "本轮处理已取消",
            "data": {},
        })
        raise
    except Exception as exc:
        if not execution.events or execution.events[-1].get("type") not in {"error", "complete"}:
            await execution.publish({
                "type": "error",
                "message": "立项助手执行中断，请稍后重试",
                "data": {"error_type": type(exc).__name__},
            })
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        await execution.finish()
        asyncio.create_task(
            _expire_execution(execution.client_turn_id, execution),
            name=f"creation-turn-expire-{execution.client_turn_id}",
        )


async def _expire_execution(client_turn_id: str, execution: _CreationTurnExecution) -> None:
    await asyncio.sleep(_TURN_RETENTION_SECONDS)
    async with _EXECUTIONS_LOCK:
        if _EXECUTIONS.get(client_turn_id) is execution:
            _EXECUTIONS.pop(client_turn_id, None)


async def creation_agent_turn_stream(
    *,
    client_turn_id: str,
    request_fingerprint: str,
    after_sequence: int,
    producer: TurnProducer,
) -> AsyncIterator[str]:
    """Start or reattach to one idempotent creation turn."""

    conflict_payload: dict[str, Any] | None = None
    async with _EXECUTIONS_LOCK:
        execution = _EXECUTIONS.get(client_turn_id)
        if execution is not None and execution.request_fingerprint != request_fingerprint:
            sequence = max(int(after_sequence or 0), 0) + 1
            conflict_payload = {
                "client_turn_id": client_turn_id,
                "sequence": sequence,
                "type": "error",
                "message": "client_turn_id 已绑定到另一条立项消息",
                "data": {"error_type": "client_turn_conflict"},
            }
        elif execution is None:
            execution = _CreationTurnExecution(
                client_turn_id,
                request_fingerprint,
                sequence_base=max(int(after_sequence or 0), 0),
            )
            _EXECUTIONS[client_turn_id] = execution
            execution.task = asyncio.create_task(
                _run_execution(execution, producer),
                name=f"creation-agent-turn-{client_turn_id}",
            )

    if conflict_payload is not None:
        sequence = int(conflict_payload["sequence"])
        yield f"id: {sequence}\ndata: {json.dumps(conflict_payload, ensure_ascii=False)}\n\n"
        return
    assert execution is not None

    sent = max(int(after_sequence or 0), 0)
    while True:
        async with execution.condition:
            pending = [
                event
                for event in execution.events
                if int(event.get("sequence") or 0) > sent
            ]
            if not pending and not execution.done:
                await execution.condition.wait()
                continue
            done = execution.done
        for event in pending:
            sequence = int(event.get("sequence") or 0)
            sent = max(sent, sequence)
            yield f"id: {sequence}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        if done and not any(int(event.get("sequence") or 0) > sent for event in execution.events):
            break


__all__ = ["creation_agent_turn_stream"]
