"""Persistence and presentation helpers for project-assistant turns."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session
from app.core.utils import utc_isoformat
from app.services.operation_runtime import record_operation_signal
from app.services.workspace.assistant_public_projection import (
    public_message_content,
    public_message_payload,
    public_tool_log,
)
from app.services.workspace.run_log import mark_assistant_run, run_payload

_SUCCESS_TOOL_STATUSES = frozenset({"ok", "completed", "success", "succeeded"})
_TERMINAL_DRAFT_TOOLS = frozenset({
    "save_external_chapter_draft",
    "save_external_outline_draft",
})
_TERMINAL_CONTEXT_PREREQUISITES = frozenset({
    "prepare_task_context",
    "search_task_context",
    "submit_context_evidence",
})


@dataclass(frozen=True)
class WorkspaceFailureResolution:
    unresolved: list[dict]
    recovered: list[dict]
    terminal_draft_tool: str | None = None


def _durable_terminal_draft_tool(applied_actions: list[dict]) -> str | None:
    """Return the terminal tool only when its durable draft receipt is present."""
    for action in reversed(applied_actions):
        tool = str(action.get("tool") or "")
        status = str(action.get("status") or "").lower()
        data = action.get("data")
        if (
            tool in _TERMINAL_DRAFT_TOOLS
            and status in _SUCCESS_TOOL_STATUSES
            and isinstance(data, dict)
            and str(data.get("draft_id") or "").strip()
            and str(data.get("draft_status") or "").lower() == "pending"
        ):
            return tool
    return None


def _resolve_workspace_failures(
    tool_logs: list[dict],
    applied_actions: list[dict],
) -> WorkspaceFailureResolution:
    """Separate still-relevant errors from prerequisites proven by a saved draft.

    A successful terminal draft tool ends the model turn. Its durable receipt,
    together with a context manifest, proves that the context gate completed.
    Earlier failures in that exact prerequisite chain therefore describe a
    corrected attempt rather than an uncertain final write.
    """
    failed = [
        log for log in tool_logs
        if str(log.get("status") or "").lower() in {"error", "needs_confirmation"}
    ]
    terminal_tool = _durable_terminal_draft_tool(applied_actions)
    if not terminal_tool:
        return WorkspaceFailureResolution(unresolved=failed, recovered=[])

    terminal_action = next(
        action
        for action in reversed(applied_actions)
        if str(action.get("tool") or "") == terminal_tool
        and str(action.get("status") or "").lower() in _SUCCESS_TOOL_STATUSES
        and isinstance(action.get("data"), dict)
        and str(action["data"].get("draft_id") or "").strip()
        and str(action["data"].get("draft_status") or "").lower() == "pending"
    )
    has_context_receipt = bool(
        str(terminal_action["data"].get("context_manifest_id") or "").strip()
    )
    recovered_tools = {terminal_tool}
    if has_context_receipt:
        recovered_tools.update(_TERMINAL_CONTEXT_PREREQUISITES)
    recovered = [log for log in failed if str(log.get("tool") or "") in recovered_tools]
    unresolved = [log for log in failed if log not in recovered]
    return WorkspaceFailureResolution(
        unresolved=unresolved,
        recovered=recovered,
        terminal_draft_tool=terminal_tool,
    )


def _append_workspace_failure_notice(
    reply: str,
    resolution: WorkspaceFailureResolution,
) -> str:
    notices: list[str] = []
    terminal_label = (
        "章节草稿"
        if resolution.terminal_draft_tool == "save_external_chapter_draft"
        else "大纲草稿"
    )
    recovered_length_checks = [
        log
        for log in resolution.recovered
        if str(log.get("tool") or "") == "save_external_chapter_draft"
        and str(log.get("status") or "").lower() == "needs_confirmation"
        and isinstance(log.get("remediation"), dict)
        and str(log["remediation"].get("code") or "") == "draft_below_minimum"
    ]
    other_recovered = [
        log for log in resolution.recovered if log not in recovered_length_checks
    ]
    if recovered_length_checks:
        notices.append(
            f"补充：本轮经过 {len(recovered_length_checks)} 次篇幅校验与补写，"
            f"最终{terminal_label}已达到要求并成功暂存。"
        )
    if other_recovered:
        notices.append(
            f"补充：本轮有 {len(other_recovered)} 次前序工具调用未通过，"
            f"后续流程已纠正；{terminal_label}已成功生成并暂存。"
        )
    if resolution.unresolved:
        failed_text = "；".join(
            f"{projected['tool']}: {projected['detail']}"
            for log in resolution.unresolved[:3]
            if (projected := public_tool_log(log))
        )
        if resolution.terminal_draft_tool:
            notices.append(
                f"注意：{terminal_label}已成功生成并暂存；本轮另有工具执行失败，"
                f"相关附加操作可能未完成：{failed_text}"
            )
        else:
            notices.append(
                f"注意：本轮有工具执行失败，相关数据可能未保存：{failed_text}"
            )
    if not notices:
        return reply
    notice_text = "\n\n".join(notices)
    return f"{reply}\n\n{notice_text}".strip()


def _workspace_result_summary(result: dict) -> str:
    projected = public_tool_log(result)
    tool = str(projected["tool"])
    status = str(projected["status"])
    detail = str(projected["detail"])
    prefix = f"{tool}（{status}）"
    return f"{prefix}：{detail}" if detail else prefix


def _build_workspace_final_reply(
    final_reply: str,
    *,
    applied_actions: list[dict],
    tool_logs: list[dict],
    searched_context: list[dict],
) -> str:
    reply = str(final_reply or "").strip()
    if reply:
        return reply

    if applied_actions:
        lines = [
            f"本轮已执行 {len(applied_actions)} 个工具操作，但模型没有给出最终文字回复。",
            "",
            "执行结果：",
        ]
        lines.extend(f"- {_workspace_result_summary(action)}" for action in applied_actions[:5])
        if len(applied_actions) > 5:
            lines.append(f"- 另有 {len(applied_actions) - 5} 个结果已省略")
        return "\n".join(lines)

    if tool_logs:
        lines = ["本轮已调用工具，但模型没有给出最终文字回复。", "", "工具结果："]
        lines.extend(f"- {_workspace_result_summary(log)}" for log in tool_logs[:5])
        if len(tool_logs) > 5:
            lines.append(f"- 另有 {len(tool_logs) - 5} 条工具日志已省略")
        return "\n".join(lines)

    if searched_context:
        lines = ["本轮已读取相关资料，但模型没有给出最终文字回复。", "", "已读取："]
        for item in searched_context[:5]:
            projected = public_tool_log(item)
            tool = str(projected["tool"])
            detail = str(projected["detail"])
            data = item.get("data")
            count = len(data) if isinstance(data, list) else 0
            suffix = detail or (f"{count} 条结果" if count else "有结果")
            lines.append(f"- {tool}：{suffix}")
        if len(searched_context) > 5:
            lines.append(f"- 另有 {len(searched_context) - 5} 条检索上下文已省略")
        lines.extend(
            [
                "",
                (
                    "请重试一次；如果连续出现，建议在系统设置里测试"
                    "当前模型/CLI 的流式输出和工具调用能力。"
                ),
            ]
        )
        return "\n".join(lines)

    return (
        "我没有收到模型的文字回复，也没有执行任何工具。请重试一次，"
        "或在系统设置里测试当前模型/CLI 是否支持项目助手的流式输出和工具调用。"
    )


def _workspace_outcome(
    raw_reply: str,
    *,
    applied_actions: list[dict],
    tool_logs: list[dict],
    searched_context: list[dict],
    failed_logs: list[dict] | None = None,
) -> str:
    """Return a stable user-facing outcome for an assistant turn."""
    if failed_logs:
        return "partial_success" if applied_actions else "failed"
    if str(raw_reply or "").strip():
        return "completed_with_reply"
    if applied_actions or tool_logs or searched_context:
        return "completed_with_tools"
    return "empty_response"


def _assistant_conversation_to_dict(
    conversation: Any,
    message_count: int | None = None,
) -> dict:
    return {
        "id": conversation.id,
        "project_id": conversation.project_id,
        "title": conversation.title,
        "scope": conversation.scope,
        "model": conversation.model,
        "message_count": message_count,
        "created_at": utc_isoformat(conversation.created_at),
        "updated_at": utc_isoformat(conversation.updated_at),
    }


def _assistant_message_to_dict(message: Any) -> dict:
    raw_payload = None
    if message.payload_json:
        try:
            raw_payload = json.loads(message.payload_json)
        except Exception:
            raw_payload = None
    payload = public_message_payload(raw_payload)
    if payload is not None and str(message.status or "").lower() in {
        "error",
        "aborted",
        "cancelled",
        "interrupted",
    }:
        # Legacy/pre-release rows may have copied a raw provider diagnostic to
        # ``payload.reply`` even though their durable message status is an
        # error.  The stable public error projection is authoritative here.
        payload.pop("reply", None)
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sequence_no": message.sequence_no,
        "role": message.role,
        "content": public_message_content(message, payload),
        "payload": payload,
        "status": message.status,
        "created_at": utc_isoformat(message.created_at),
        "updated_at": utc_isoformat(message.updated_at),
    }


@dataclass
class WorkspaceTurnTelemetry:
    last_operation_report_at: float = 0.0

    def report_model_activity(
        self,
        assistant_run: Any,
        text: str,
        *,
        signal: str = "output",
        message: str = "模型正在生成回复",
    ) -> None:
        if not assistant_run or not assistant_run.operation_id:
            return
        now = time.monotonic()
        if now - self.last_operation_report_at < 2:
            return
        self.last_operation_report_at = now
        record_operation_signal(
            assistant_run.operation_id,
            signal,
            {"output_chars": len(text or "")},
            message=message,
        )


def finalize_workspace_assistant_turn(
    db: Session,
    *,
    assistant_run: Any,
    assistant_message: Any,
    conversation: Any,
    final_reply: str,
    applied_actions: list[dict],
    tool_logs: list[dict],
    searched_context: list[dict],
    final_model: str,
    final_usage: Any,
) -> dict[str, Any]:
    existing_payload: dict[str, Any] = {}
    if assistant_message.payload_json:
        try:
            decoded_payload = json.loads(assistant_message.payload_json)
            if isinstance(decoded_payload, dict):
                existing_payload = decoded_payload
        except Exception:
            existing_payload = {}
    reference_context_audit = existing_payload.get("reference_context_audit")
    failure_resolution = _resolve_workspace_failures(tool_logs, applied_actions)
    failed_logs = failure_resolution.unresolved
    persisted_model_error = next(
        (
            f"{code}: {str(log.get('detail') or '')[:500]}"
            for log in failed_logs
            if (code := str(log.get("error_code") or ""))
            and code.startswith("model_")
            and re.fullmatch(r"[a-z0-9_]{1,100}", code)
        ),
        None,
    )
    final_reply_for_save = _build_workspace_final_reply(
        final_reply,
        applied_actions=applied_actions,
        tool_logs=tool_logs,
        searched_context=searched_context,
    )
    final_reply_for_save = _append_workspace_failure_notice(
        final_reply_for_save,
        failure_resolution,
    )
    outcome = _workspace_outcome(
        final_reply,
        applied_actions=applied_actions,
        tool_logs=tool_logs,
        searched_context=searched_context,
        failed_logs=failed_logs,
    )
    private_result: dict[str, Any] = {
        "reply": final_reply_for_save,
        "outcome": outcome,
        "actions": [],
        "applied_actions": applied_actions,
        "tool_logs": tool_logs,
        "searched_context": searched_context,
        "scope": "project",
        "model": final_model,
        "usage": final_usage,
    }
    if isinstance(reference_context_audit, dict):
        private_result["reference_context_audit"] = reference_context_audit
    if assistant_run:
        private_result["run"] = run_payload(assistant_run)
    response_payload = public_message_payload(private_result) or {
        "reply": final_reply_for_save,
        "tool_logs": [],
        "applied_actions": [],
        "actions": [],
    }
    assistant_message.content = response_payload["reply"]
    assistant_message.payload_json = json.dumps(response_payload, ensure_ascii=False)
    assistant_message.status = "completed"
    assistant_message.updated_at = datetime.utcnow()
    conversation.updated_at = datetime.utcnow()
    commit_session(db)
    mark_assistant_run(
        db,
        assistant_run,
        status="error" if outcome == "failed" else "completed",
        phase="error" if outcome == "failed" else outcome,
        error=persisted_model_error if outcome == "failed" else None,
        final_reply=final_reply_for_save,
        outcome=outcome,
    )
    if assistant_run:
        db.refresh(assistant_run)
        response_payload["run"] = run_payload(assistant_run)
        assistant_message.payload_json = json.dumps(response_payload, ensure_ascii=False)
        commit_session(db)
    db.refresh(assistant_message)
    db.refresh(conversation)
    response_payload["message"] = _assistant_message_to_dict(assistant_message)
    response_payload["conversation"] = _assistant_conversation_to_dict(conversation)
    return response_payload
