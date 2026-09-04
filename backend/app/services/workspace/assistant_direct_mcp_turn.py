"""Direct-MCP model step for local workspace assistant providers."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from app.architecture.tool_categories import TOOL_CATEGORY_CONTROLLER, normalize_tool_categories
from app.services.conversation_context import (
    ConversationContextError,
    ConversationContextErrorCode,
)
from app.services.tool_category_state import (
    activate_tool_categories,
    bind_tool_category_turn_guard,
    read_tool_category_state,
)
from app.services.workspace.assistant_public_projection import public_tool_log
from app.services.workspace.assistant_public_errors import public_model_failure
from app.services.workspace.assistant_turn_state import WorkspaceAssistantTurnState
from app.services.workspace.assistant_turn_support import workspace_category_result
from app.services.workspace.direct_mcp_run_log import issue_workspace_direct_mcp_lease
from app.services.workspace.terminal_draft_detection import local_cli_terminal_draft
from app.services.workspace.turn_control import terminal_reply as terminal_tool_reply

logger = logging.getLogger(__name__)

_MANAGED_DIRECT_MCP_TIMEOUT_SECONDS = 1_800


@dataclass
class DirectMcpCapture:
    content: list[str] = field(default_factory=list)
    resume_notices: list[dict[str, Any]] = field(default_factory=list)
    stream_error: Exception | None = None

    @property
    def raw_content(self) -> str:
        return "".join(self.content)


class WorkspaceDirectMcpTurn:
    def __init__(self, state: WorkspaceAssistantTurnState, gateway: Any) -> None:
        self.state = state
        self.gateway = gateway

    async def run(
        self,
        *,
        messages: list[dict[str, Any]],
        iteration: int,
    ) -> AsyncGenerator[str, None]:
        self._bind_run_iteration(iteration)
        capture = DirectMcpCapture()
        async for event in self._collect(capture, messages):
            yield event
        # The CLI may have spent minutes inside its isolated MCP process.  A
        # newer author message can supersede this durable run during that
        # interval; never let its trailing text or draft probe overwrite the
        # newer turn's status after the MCP dispatch guard has closed tools.
        self.state.require_current_run()
        self._collect_tool_failures(iteration)
        if self._apply_category_change(iteration):
            for event in self.state.pending_native_events:
                yield event
            self.state.pending_native_events = []
            self.state.loop_action = "continue"
            return
        if (
            self.state.local_cli_mcp_enabled
            and not self.state.category_selected
            and capture.stream_error is None
        ):
            raise ConversationContextError(
                ConversationContextErrorCode.PROTOCOL_INVALID,
                "本机 CLI 没有调用临时 MCP 中唯一开放的 set_tool_categories，"
                "本轮已终止，未接受 CLI 返回的等待或完成文字",
                details={"reason": "missing_tool_category_controller"},
            )
        terminal_event = self._detect_terminal_draft(iteration)
        if terminal_event is not None:
            yield terminal_event
            yield self.state.event(
                {
                    "type": "iteration_end",
                    "iteration": iteration,
                    "message": "检测到新的待审草稿，本轮立即结束",
                }
            )
            self.state.loop_action = "break"
            return
        if capture.stream_error is not None:
            self._complete_interrupted(capture.stream_error)
            yield self.state.event(
                {
                    "type": "iteration_end",
                    "iteration": iteration,
                    "message": "模型输出中断，本轮已停止执行",
                }
            )
            self.state.loop_action = "break"
            return
        self._complete_text(capture.raw_content)
        yield self.state.event(
            {
                "type": "iteration_end",
                "iteration": iteration,
                "message": (
                    "本机 CLI 已通过原生 MCP 完成本轮并返回文字结果"
                    if self.state.local_cli_mcp_enabled
                    else "模型执行器已完成本轮并返回文字结果"
                ),
            }
        )
        self.state.loop_action = "break"

    def _collect_tool_failures(self, iteration: int) -> None:
        """Carry persisted MCP failures into the shared turn finalizer."""
        state = self.state
        if not state.local_cli_mcp_enabled:
            return
        steps = list(state.workspace.run_steps(str(state.assistant_run.id)))
        successful_statuses = {"ok", "completed", "success", "succeeded"}
        for position, step in enumerate(steps):
            if (
                str(step.project_id) != state.project_id
                or int(step.iteration or 0) != iteration
                or str(step.status or "").lower() not in {
                    "error", "failed", "denied", "blocked_rebuild", "skipped", "needs_confirmation",
                }
            ):
                continue
            # A schema/precondition rejection is an expected self-correction
            # path for an agent. If the same tool succeeds later in this model
            # step, keep the rejected attempt in the durable RunStep audit but
            # do not tell the author that finished work is only partial. Real
            # execution errors and unresolved skips remain visible.
            if str(step.status or "").lower() == "skipped" and any(
                str(later.project_id) == state.project_id
                and int(later.iteration or 0) == iteration
                and str(later.tool) == str(step.tool)
                and str(later.status or "").lower() in successful_statuses
                for later in steps[position + 1 :]
            ):
                continue
            # Provider/tool diagnostics stay in the durable audit record, not
            # in the public final reply. A CLI's prose cannot erase a failure.
            raw_result: Any = None
            try:
                raw_result = json.loads(str(getattr(step, "result_json", None) or ""))
            except (TypeError, json.JSONDecodeError):
                raw_result = None
            if isinstance(raw_result, dict):
                projected = public_tool_log(raw_result)
                if (
                    projected.get("tool") == str(step.tool)
                    and projected.get("status") == str(step.status or "").lower()
                ):
                    state.tool_logs.append(projected)
                    continue
            state.tool_logs.append({
                "tool": str(step.tool),
                "status": "error",
                "detail": "本机 CLI 工具未完成，请检查工具记录、前置条件与当前项目状态。",
            })

    def _bind_run_iteration(self, iteration: int) -> None:
        state = self.state
        if not state.local_cli_mcp_enabled or not state.tool_category_state_file:
            return
        normalized_iteration = max(0, int(iteration))
        lease_token = issue_workspace_direct_mcp_lease(
            state.db,
            state.assistant_run,
            iteration=normalized_iteration,
        )
        state.local_cli_extra_body["local_cli_mcp_lease_token"] = lease_token
        state.local_cli_extra_body["local_cli_terminal_draft_run_id"] = str(
            state.assistant_run.id
        )
        state.local_cli_extra_body["local_cli_terminal_draft_iteration"] = normalized_iteration
        bind_tool_category_turn_guard(
            state.tool_category_state_file,
            {
                "kind": "workspace",
                "project_id": state.project_id,
                "conversation_id": str(state.conversation.id),
                "run_id": str(state.assistant_run.id),
                "iteration": normalized_iteration,
            },
        )

    async def _collect(
        self,
        capture: DirectMcpCapture,
        messages: list[dict[str, Any]],
    ) -> AsyncGenerator[str, None]:
        state = self.state

        def on_resume(info: dict[str, Any]) -> None:
            capture.resume_notices.append(dict(info))

        stream = self.gateway.stream_chat_completion(
            messages=messages,
            model=state.payload.model,
            temperature=state.payload.temperature or 0.3,
            max_tokens=state.payload.max_tokens,
            # One managed CLI step includes many real MCP reads plus prose
            # generation. Keep a finite deadline without treating that entire
            # workflow as a single short completion. Stall/cancel monitors and
            # the no-replay rule remain active throughout.
            timeout=(
                _MANAGED_DIRECT_MCP_TIMEOUT_SECONDS
                if state.local_cli_mcp_enabled
                else 300
            ),
            retry=0 if state.local_cli_mcp_enabled else 1,
            resume=0 if state.local_cli_mcp_enabled else 8,
            on_resume=on_resume,
            extra_body=state.local_cli_extra_body,
        )
        try:
            async for chunk in stream:
                while capture.resume_notices:
                    capture.resume_notices.pop(0)
                    resumed_text = "模型连接中断，正在从已验证的文字检查点继续…"
                    state.turn_telemetry.report_model_activity(
                        state.assistant_run, resumed_text, message=resumed_text
                    )
                    yield state.event(
                        {"type": "status", "message": resumed_text, "tool": "stream_resume"}
                    )
                capture.content.append(chunk)
                state.turn_telemetry.report_model_activity(state.assistant_run, chunk)
                yield state.event({"type": "content_delta", "delta": chunk})
        except Exception as exc:
            capture.stream_error = exc
            error_id = uuid.uuid4().hex
            logger.error(
                "Workspace direct MCP stream failed error_id=%s run=%s type=%s",
                error_id,
                getattr(state.assistant_run, "id", None),
                type(exc).__name__,
            )
            yield state.event(
                {
                    "type": "status",
                    "message": "模型流式输出中断，本轮将按安全边界结束。",
                    "tool": "stream_error",
                }
            )

    def _apply_category_change(self, iteration: int) -> bool:
        state = self.state
        if not state.local_cli_mcp_enabled or not state.tool_category_state_file:
            return False
        category_state = read_tool_category_state(state.tool_category_state_file)
        next_version = int(category_state.get("version") or 0)
        if next_version <= state.observed_category_version:
            return False
        state.observed_category_version = next_version
        requested = normalize_tool_categories(category_state.get("requested_categories") or [])
        result, selected = workspace_category_result(
            {"enabled_categories": list(requested)}, state.authorized_tool_names
        )
        if selected is not None:
            state.active_categories = selected
            state.category_selected = True
            activate_tool_categories(state.tool_category_state_file)
        state.tool_logs.append(
            {
                "tool": TOOL_CATEGORY_CONTROLLER,
                "status": result.get("status") or "ok",
                "detail": result.get("detail") or "",
            }
        )
        state.pending_native_events = [
            state.event(
                {
                    "type": "tool_categories_changed",
                    "tool": TOOL_CATEGORY_CONTROLLER,
                    "result": public_tool_log(result),
                    "iteration": iteration,
                }
            ),
            state.event(
                {
                    "type": "iteration_end",
                    "iteration": iteration,
                    "message": "工具类别已切换，正在按新类别启动下一模型步骤",
                }
            ),
        ]
        return True

    def _detect_terminal_draft(self, iteration: int) -> str | None:
        state = self.state
        if not state.local_cli_mcp_enabled:
            return None
        state.db.expire_all()
        detected = local_cli_terminal_draft(
            state.db,
            state.project_id,
            str(state.assistant_run.id),
            iteration,
        )
        if detected is None:
            return None
        result, _message = detected
        state.turn_terminal_result = result
        state.applied_actions.append(result)
        state.tool_logs.append(
            {
                "tool": str(result["tool"]),
                "status": "ok",
                "detail": result["detail"],
            }
        )
        state.final_reply = terminal_tool_reply(result)
        state.final_model = state.payload.model or ""
        state.final_usage = None
        return state.event(
            {
                "type": "write_result",
                "tool": str(result["tool"]),
                "result": public_tool_log(result),
                "iteration": iteration,
            }
        )

    def _complete_interrupted(self, error: Exception) -> None:
        state = self.state
        failure = public_model_failure(error)
        state.tool_logs.append(
            {
                "tool": "stream_error",
                "status": "error",
                "detail": failure.message,
                "error_code": failure.code,
                "failure_class": failure.failure_class,
            }
        )
        state.final_reply = (
            f"{failure.message} 为避免重复执行可能已经提交的 MCP 写入，"
            "系统没有自动重启该进程；已提交结果以当前项目数据为准，"
            "下次请求会从真实项目状态继续。"
            if state.local_cli_mcp_enabled
            else f"{failure.message} 本轮未执行写入。"
        )
        state.final_model = state.payload.model or ""
        state.final_usage = None

    def _complete_text(self, content: str) -> None:
        self.state.final_reply = content.strip()
        self.state.final_model = self.state.payload.model or ""
        self.state.final_usage = None
