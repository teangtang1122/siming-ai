"""Native function-calling step for the workspace assistant."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from app.architecture.tool_categories import TOOL_CATEGORY_CONTROLLER
from app.architecture.tool_definition import ToolDef
from app.core.exceptions import LLMError
from app.services.conversation_context import (
    ConversationContextError,
    ConversationContextErrorCode,
    NativeToolCall,
    NativeToolResult,
    ToolTransaction,
)
from app.services.workspace.assistant_public_errors import safe_tool_execution_failure
from app.services.workspace.assistant_public_projection import public_tool_log
from app.services.workspace.assistant_turn_state import WorkspaceAssistantTurnState
from app.services.workspace.assistant_turn_support import workspace_category_result
from app.services.workspace.native_tool_batch import (
    NativeToolBatchValidationError,
    validate_workspace_native_tool_batch,
)
from app.services.workspace.run_log import finish_run_step, start_run_step
from app.services.workspace.run_recovery import generate_idempotency_key
from app.services.workspace.tool_result_projection import (
    ToolResultBatchOverCapacity,
    ToolResultProjectionError,
    admit_native_assistant_transaction,
    declared_model_results_for_tool_names,
    model_tool_result_projector,
)
from app.services.workspace.turn_control import is_terminal_tool_result
from app.services.workspace.turn_control import (
    terminal_reply as terminal_tool_reply,
)

_CATEGORY_DEFINITION = ToolDef(
    name=TOOL_CATEGORY_CONTROLLER,
    description="Select the server-authorized workspace tool categories.",
    input_schema={"type": "object", "properties": {}},
    tool_type="control",
)
logger = logging.getLogger(__name__)


@dataclass
class NativeStepCapture:
    content: list[str] = field(default_factory=list)
    calls: dict[int, dict[str, str]] = field(default_factory=dict)
    reasoning: str = ""
    provider_state: list[dict[str, Any]] = field(default_factory=list)
    resume_notices: list[dict[str, Any]] = field(default_factory=list)

    @property
    def reply_text(self) -> str:
        return "".join(self.content)


class WorkspaceNativeTurn:
    def __init__(self, state: WorkspaceAssistantTurnState, gateway: Any, registry: Any) -> None:
        self.state = state
        self.gateway = gateway
        self.registry = registry

    async def run(
        self,
        *,
        messages: list[dict[str, Any]],
        iteration: int,
        tool_schemas: list[dict[str, Any]],
        tool_choice: str,
    ) -> AsyncGenerator[str, None]:
        capture = NativeStepCapture()
        async for event in self._collect(capture, messages, iteration, tool_schemas, tool_choice):
            yield event
        # Provider streaming is an unbounded concurrency window.  Revalidate
        # the durable run before consuming protocol state or admitting calls.
        self.state.require_current_run()
        tool_calls = self._validated_tool_calls(capture, iteration, tool_schemas)
        transaction, admission_error = self._admit(capture, tool_calls, iteration)
        if admission_error is not None:
            async for event in self._persist_batch_denial(
                capture, tool_calls, transaction, admission_error, iteration
            ):
                yield event
            return
        async for event in self._execute_calls(capture, tool_calls, transaction, iteration):
            yield event

    async def _collect(
        self,
        capture: NativeStepCapture,
        messages: list[dict[str, Any]],
        iteration: int,
        tool_schemas: list[dict[str, Any]],
        tool_choice: str,
    ) -> AsyncGenerator[str, None]:
        state = self.state

        def on_resume(info: dict[str, Any]) -> None:
            capture.resume_notices.append(dict(info))

        try:
            stream = self.gateway.stream_chat_completion_with_tools(
                messages=messages,
                model=state.payload.model,
                temperature=state.payload.temperature or 0.3,
                max_tokens=state.payload.max_tokens,
                timeout=300,
                retry=1,
                resume=8,
                on_resume=on_resume,
                extra_body=state.local_cli_extra_body,
                tools=tool_schemas,
                tool_choice=tool_choice,
            )
            async for chunk in stream:
                for event in self._capture_chunk(capture, chunk, iteration):
                    yield event
        except Exception:
            yield state.event(
                {
                    "type": "status",
                    "message": "模型原生工具响应未完成，本轮已停止。",
                    "tool": "native_tool_protocol_error",
                }
            )
            raise

    def _capture_chunk(
        self,
        capture: NativeStepCapture,
        chunk: dict[str, Any],
        iteration: int,
    ) -> list[str]:
        state = self.state
        events: list[str] = []
        while capture.resume_notices:
            notice = capture.resume_notices.pop(0)
            resumed_text = (
                "模型连接中断，正在从已验证的文字检查点继续…"
                if max(0, int(notice.get("checkpoint_chars") or 0))
                else "模型工具响应中断，正在重新获取完整工具调用…"
            )
            state.turn_telemetry.report_model_activity(
                state.assistant_run, resumed_text, message=resumed_text
            )
            events.append(
                state.event({"type": "status", "message": resumed_text, "tool": "stream_resume"})
            )
        kind = chunk["type"]
        if kind == "content_delta":
            capture.content.append(chunk["delta"])
            state.turn_telemetry.report_model_activity(state.assistant_run, chunk["delta"])
            events.append(state.event({"type": "content_delta", "delta": chunk["delta"]}))
        elif kind == "reasoning_delta":
            capture.reasoning += chunk["delta"]
            state.turn_telemetry.report_model_activity(
                state.assistant_run, chunk["delta"], message="模型正在思考"
            )
        elif kind == "tool_call_delta":
            events.extend(self._capture_tool_delta(capture, chunk))
        elif kind == "done":
            events.extend(self._capture_done(capture, chunk, iteration))
        return events

    def _capture_tool_delta(
        self,
        capture: NativeStepCapture,
        chunk: dict[str, Any],
    ) -> list[str]:
        state = self.state
        state.turn_telemetry.report_model_activity(
            state.assistant_run,
            chunk.get("name") or chunk.get("arguments_delta") or "",
            signal="tool",
            message="模型正在准备工具调用",
        )
        index = int(chunk["index"])
        buffer = capture.calls.setdefault(
            index,
            {"id": str(chunk.get("id") or ""), "name": "", "arguments": ""},
        )
        if chunk.get("id"):
            buffer["id"] = str(chunk["id"])
        events: list[str] = []
        if chunk.get("name"):
            buffer["name"] = str(chunk["name"])
            events.append(state.event({"type": "tool_call", "tool": chunk["name"]}))
        if chunk.get("arguments_delta"):
            buffer["arguments"] += str(chunk["arguments_delta"])
        return events

    def _capture_done(
        self,
        capture: NativeStepCapture,
        chunk: dict[str, Any],
        iteration: int,
    ) -> list[str]:
        if capture.reasoning:
            capture.provider_state = chunk.get("provider_state") or []
            return []
        capture.reasoning = str(chunk.get("reasoning_content") or "")
        capture.provider_state = chunk.get("provider_state") or []
        return []

    def _validated_tool_calls(
        self,
        capture: NativeStepCapture,
        iteration: int,
        schemas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        declared = {str(schema.get("function", {}).get("name") or "") for schema in schemas}
        raw_calls = [
            {
                "id": buffer.get("id"),
                "type": "function",
                "function": {
                    "name": buffer.get("name"),
                    "arguments": buffer.get("arguments"),
                },
            }
            for _, buffer in sorted(capture.calls.items())
        ]
        try:
            validated = validate_workspace_native_tool_batch(
                raw_calls,
                allowed_tool_names=declared,
                resolve_tool=self.registry.get,
                require_initial_controller=not self.state.category_selected,
            )
        except NativeToolBatchValidationError as exc:
            raise self._protocol_error(
                exc.message,
                iteration,
                **exc.details,
            ) from exc
        return list(validated.calls)

    def _admit(
        self,
        capture: NativeStepCapture,
        calls: list[dict[str, Any]],
        iteration: int,
    ) -> tuple[ToolTransaction | None, ToolResultBatchOverCapacity | None]:
        names = [str(call["function"]["name"]) for call in calls]
        provider_state = tuple(
            dict(item) for item in capture.provider_state if isinstance(item, dict)
        )
        payload: dict[str, Any] = {
            "role": "assistant",
            "content": capture.reply_text,
            "tool_calls": calls,
        }
        if capture.reasoning:
            payload["reasoning_content"] = capture.reasoning
        if provider_state:
            payload["provider_state"] = list(provider_state)
        error: ToolResultBatchOverCapacity | None = None
        try:
            declared = declared_model_results_for_tool_names(names, resolve_tool=self.registry.get)
            admit_native_assistant_transaction(payload, declared)
        except ToolResultBatchOverCapacity as exc:
            error = exc
        except ValueError as exc:
            raise self._protocol_error(
                "原生工具结果契约无效，整批未执行。",
                iteration,
                tools=names,
                reason="native_tool_contract_invalid",
            ) from exc
        if not calls:
            return None, error
        state = self.state
        return ToolTransaction(
            transaction_id=f"{state.assistant_run.id}:transaction:{iteration}",
            assistant_message_id=f"{state.assistant_message.id}:tool-assistant:{iteration}",
            assistant_content=capture.reply_text,
            assistant_reasoning_content=capture.reasoning,
            assistant_provider_state=provider_state,
            calls=tuple(
                NativeToolCall(
                    call_id=str(call["id"]),
                    name=str(call["function"]["name"]),
                    arguments_json=str(call["function"]["arguments"]),
                )
                for call in calls
            ),
        ), error

    async def _persist_batch_denial(
        self,
        capture: NativeStepCapture,
        calls: list[dict[str, Any]],
        transaction: ToolTransaction | None,
        error: ToolResultBatchOverCapacity,
        iteration: int,
    ) -> AsyncGenerator[str, None]:
        if transaction is None:
            raise AssertionError("rejected tool batch must not be empty")
        state = self.state
        self._mark_delivered_transactions_consumed()
        for call in calls:
            name = str(call["function"]["name"])
            denied = error.model_error_result(name)
            definition = self.registry.get(name)
            step_type = (
                "control"
                if name == TOOL_CATEGORY_CONTROLLER
                else (
                    "write"
                    if definition is not None and definition.tool_type == "write"
                    else "search"
                )
            )
            step = start_run_step(
                state.db,
                state.assistant_run,
                step_type=step_type,
                tool=name,
                iteration=iteration,
                request={
                    "native_call_id": str(call["id"]),
                    "arguments": json.loads(call["function"]["arguments"]),
                },
                detail="工具结果批次容量校验未通过，业务处理器未执行",
            )
            if step is None:
                raise LLMError("拒绝结果未能写入持久 RunStep，本轮已停止")
            finish_run_step(
                state.db,
                step,
                status="error",
                result=denied,
                detail=str(denied["detail"]),
                error=str(denied["detail"]),
            )
            transaction = transaction.add_result(
                NativeToolResult(
                    call_id=str(call["id"]),
                    content=json.dumps(
                        denied, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                    ),
                    result_ref=f"assistant_run_step:{step.id}",
                    persisted_step_id=str(step.id),
                )
            )
            state.tool_logs.append(
                {"tool": name, "status": "error", "detail": str(denied["detail"])}
            )
            yield state.event(
                {
                    "type": "tool_result_batch_rejected",
                    "tool": name,
                    "result": public_tool_log(denied),
                    "iteration": iteration,
                    "step_id": step.id,
                }
            )
        if error.reason != "tool_result_batch_over_capacity":
            raise self._protocol_error(
                "模型返回的原生 assistant 工具事务不符合容量协议；"
                "整批业务处理器未执行，本轮已终止。",
                iteration,
                tools=[call["function"]["name"] for call in calls],
                reason=error.reason,
                actual_bytes=error.declared_json_bytes,
                max_bytes=error.max_json_bytes,
            )
        state.tool_transactions.append(transaction.mark_delivered())
        state.loop_action = "continue"
        yield state.event(
            {
                "type": "iteration_end",
                "iteration": iteration,
                "message": "工具结果批次超过容量，整批未执行；模型将收到逐调用拒绝结果",
            }
        )

    async def _execute_calls(
        self,
        capture: NativeStepCapture,
        calls: list[dict[str, Any]],
        transaction: ToolTransaction | None,
        iteration: int,
    ) -> AsyncGenerator[str, None]:
        state = self.state
        # A newer user may arrive while the provider is streaming.  Re-check
        # durable run ownership before starting even the first business
        # handler; superseded calls are never inferred or replayed as text.
        state.require_current_run()
        write_names = {
            name
            for name in state.workspace_tool_name_set
            if (definition := self.registry.get(name)) is not None
            and definition.tool_type == "write"
        }
        search_names = state.workspace_tool_name_set - write_names - {TOOL_CATEGORY_CONTROLLER}
        yield state.event(
            {
                "type": "tool",
                "tool": "tool_batch",
                "status": "ok",
                "detail": self._batch_detail(iteration, calls, search_names, write_names),
            }
        )
        if not calls:
            self._complete_text_only(capture.reply_text)
            if state.loop_action != "synthesize":
                self._mark_delivered_transactions_consumed()
            yield state.event(
                {
                    "type": "iteration_end",
                    "iteration": iteration,
                    "message": (
                        "模型未返回文字，进入无工具补偿"
                        if state.loop_action == "synthesize"
                        else "Agent 判断任务完成"
                    ),
                }
            )
            return
        self._mark_delivered_transactions_consumed()
        if transaction is None:
            raise AssertionError("tool transaction must exist before execution")
        stop_reason = ""
        category_changed = False
        for call in calls:
            step_type, is_write, step = self._begin_call(call, iteration, write_names)
            yield state.event(
                {
                    "type": f"{step_type}_start",
                    "tool": call["function"]["name"],
                    "iteration": iteration,
                    "step_id": step.id if step else None,
                }
            )
            transaction, stop_reason, category_changed = await self._execute_one(
                call,
                transaction,
                iteration,
                is_write=is_write,
                step_type=step_type,
                step=step,
            )
            for event in state.pending_native_events:
                yield event
            state.pending_native_events = []
            if stop_reason or category_changed:
                break
        if transaction.complete:
            state.tool_transactions.append(transaction.mark_delivered())
        if category_changed:
            state.loop_action = "continue"
            yield state.event(
                {
                    "type": "iteration_end",
                    "iteration": iteration,
                    "message": "工具类别已切换，当前模型步骤结束",
                }
            )
            return
        if stop_reason:
            self._complete_terminal_result()
            state.loop_action = "break"
            yield state.event(
                {
                    "type": "iteration_end",
                    "iteration": iteration,
                    "message": "已到达服务端回合终止边界，不再调用模型",
                }
            )
            return
        state.loop_action = "continue"
        yield state.event(
            {
                "type": "iteration_end",
                "iteration": iteration,
                "message": f"第 {iteration} 轮完成，执行了 {len(calls)} 个工具",
            }
        )

    async def _execute_one(
        self,
        call: dict[str, Any],
        transaction: ToolTransaction,
        iteration: int,
        *,
        is_write: bool,
        step_type: str,
        step: Any,
    ) -> tuple[ToolTransaction, str, bool]:
        state = self.state
        name = str(call["function"]["name"])
        arguments = json.loads(str(call["function"]["arguments"]))
        if name == TOOL_CATEGORY_CONTROLLER:
            result, selected = workspace_category_result(arguments, state.authorized_tool_names)
            if selected is not None:
                state.active_categories = selected
                state.category_selected = True
            category_changed = True
        else:
            category_changed = False
            try:
                result = await state.execute_action(
                    state.db,
                    state.project_id,
                    {"tool": name, "arguments": arguments},
                    model=state.payload.model,
                    authorized_tool_names=state.workspace_tool_name_set,
                )
            except Exception as exc:
                error_id = uuid.uuid4().hex
                logger.exception(
                    "Workspace tool failed error_id=%s run=%s tool=%s type=%s",
                    error_id,
                    getattr(state.assistant_run, "id", None),
                    name,
                    type(exc).__name__,
                )
                result = {
                    "tool": name,
                    **safe_tool_execution_failure(error_id),
                }
        finish_run_step(
            state.db,
            step,
            status=str(result.get("status") or "ok"),
            result=result,
            detail=str(result.get("detail") or ""),
            error=str(result.get("detail") or "") if result.get("status") == "error" else None,
        )
        if step is None:
            raise LLMError("工具结果未能写入持久 RunStep，本轮已停止")
        projected_content, projection_event = self._project_result(name, result, step.id)
        transaction = transaction.add_result(
            NativeToolResult(
                call_id=str(call["id"]),
                content=projected_content,
                result_ref=f"assistant_run_step:{step.id}",
                persisted_step_id=str(step.id),
            )
        )
        log = {
            "tool": result.get("tool") or name,
            "status": result.get("status") or "ok",
            "detail": result.get("detail") or "",
        }
        state.tool_logs.append(
            public_tool_log(log) if str(log["status"]).lower() == "error" else log
        )
        result_event = state.event(
            {
                "type": f"{step_type}_result",
                "tool": name,
                "result": public_tool_log(result),
                "iteration": iteration,
                "step_id": step.id,
            }
        )
        state.pending_native_events = [*projection_event, result_event]
        stop_reason = self._record_terminal(name, result, is_write)
        return transaction, stop_reason, category_changed

    def _begin_call(
        self,
        call: dict[str, Any],
        iteration: int,
        write_names: set[str],
    ) -> tuple[str, bool, Any]:
        state = self.state
        name = str(call["function"]["name"])
        arguments = json.loads(str(call["function"]["arguments"]))
        is_write = name in write_names
        step_type = (
            "control" if name == TOOL_CATEGORY_CONTROLLER else ("write" if is_write else "search")
        )
        idempotency_key = (
            generate_idempotency_key(state.db, name, state.project_id, arguments)
            if is_write
            else None
        )
        step = start_run_step(
            state.db,
            state.assistant_run,
            step_type=step_type,
            tool=name,
            iteration=iteration,
            request=arguments,
            idempotency_key=idempotency_key,
        )
        return step_type, is_write, step

    def _project_result(
        self, name: str, result: dict[str, Any], step_id: str
    ) -> tuple[str, list[str]]:
        definition = (
            _CATEGORY_DEFINITION if name == TOOL_CATEGORY_CONTROLLER else self.registry.get(name)
        )
        if definition is None:
            raise LLMError(f"工具 {name} 缺少模型结果投影契约")
        try:
            return model_tool_result_projector.project(definition, result).content, []
        except ToolResultProjectionError as exc:
            return json.dumps(
                exc.model_error_result(), ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ), [
                self.state.event(
                    {
                        "type": "status",
                        "message": "工具结果无法安全投递给模型；已返回结构化拒绝结果。",
                        "tool": "tool_result_projection",
                        "step_id": step_id,
                    }
                )
            ]

    def _record_terminal(self, name: str, result: dict[str, Any], is_write: bool) -> str:
        del is_write
        if "cataloging" in name:
            self.state.turn_terminal_result = result
            return "cataloging_status"
        if is_terminal_tool_result(result):
            self.state.turn_terminal_result = result
            self.state.applied_actions.append(result)
            return "terminal_tool"
        return ""

    def _complete_terminal_result(self) -> None:
        result = self.state.turn_terminal_result
        if result and is_terminal_tool_result(result):
            self.state.final_reply = terminal_tool_reply(result)
        elif result:
            self.state.final_reply = str(result.get("detail") or "已查询建档状态，本轮结束。")
        else:
            self.state.final_reply = "本轮没有获得新的查询结果，已停止重复推理。"

    def _complete_text_only(self, reply_text: str) -> None:
        if not self.state.category_selected:
            raise ConversationContextError(
                ConversationContextErrorCode.PROTOCOL_INVALID,
                "模型没有调用本步骤唯一开放的 set_tool_categories，"
                "本轮已终止，未接受模型伪造的等待或完成回复",
                details={"reason": "missing_tool_category_controller"},
            )
        if not reply_text.strip():
            self.state.final_reply = ""
            self.state.loop_action = "synthesize"
            return
        self.state.final_reply = reply_text
        self.state.final_model = self.state.payload.model or ""
        self.state.final_usage = None
        self.state.loop_action = "break"

    def _mark_delivered_transactions_consumed(self) -> None:
        for index, transaction in enumerate(self.state.tool_transactions):
            if transaction.state.value == "delivered":
                self.state.tool_transactions[index] = transaction.mark_consumed().mark_compactable()

    @staticmethod
    def _batch_detail(
        iteration: int,
        calls: list[dict[str, Any]],
        search_names: set[str],
        write_names: set[str],
    ) -> str:
        searches = sum(call["function"]["name"] in search_names for call in calls)
        writes = sum(call["function"]["name"] in write_names for call in calls)
        return f"第 {iteration} 轮：{len(calls)} 个工具调用（{searches} 个搜索，{writes} 个写入）"

    @staticmethod
    def _protocol_error(message: str, iteration: int, **details: Any) -> ConversationContextError:
        return ConversationContextError(
            ConversationContextErrorCode.PROTOCOL_INVALID,
            message,
            details={"iteration": iteration, **details},
        )
