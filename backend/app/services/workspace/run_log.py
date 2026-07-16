"""Durable execution log for workspace assistant runs."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import AssistantRun, AssistantRunStep
from ..operation_runtime import ensure_operation, fail_operation, finish_operation, record_operation_signal


MAX_JSON_CHARS = 80_000


def _safe_json(data: Any, *, max_chars: int = MAX_JSON_CHARS) -> str:
    try:
        text = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        text = json.dumps(str(data), ensure_ascii=False)
    if len(text) > max_chars:
        return text[:max_chars] + "...[truncated]"
    return text


def create_assistant_run(
    db: Session,
    *,
    project_id: str,
    conversation_id: str | None,
    user_message_id: str | None,
    assistant_message_id: str | None,
    scope: str,
    assistant_mode: str,
    model: str | None,
) -> AssistantRun:
    run = AssistantRun(
        project_id=project_id,
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        scope=scope,
        assistant_mode=assistant_mode,
        model=model,
        status="running",
        phase="setup",
        current_iteration=0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(run)
    db.flush()
    operation = ensure_operation(
        db,
        source_kind="assistant",
        source_id=run.id,
        project_id=project_id,
        title="作品助手任务",
        status="running",
        phase="setup",
        message="正在准备作品上下文",
        model_source=model,
        tool_mode=assistant_mode,
        resume_url=f"/project/{project_id}",
        can_pause=False,
        can_cancel=False,
        can_retry=False,
        progress_mode="indeterminate",
    )
    run.operation_id = operation.id
    db.commit()
    db.refresh(run)
    return run


def start_run_step(
    db: Session,
    run: AssistantRun | None,
    *,
    step_type: str,
    tool: str | None = None,
    iteration: int = 0,
    request: Any = None,
    detail: str | None = None,
    idempotency_key: str | None = None,
) -> AssistantRunStep | None:
    if not run:
        return None
    now = datetime.utcnow()
    run.phase = step_type
    run.current_iteration = max(run.current_iteration or 0, iteration or 0)
    run.updated_at = now
    step = AssistantRunStep(
        run_id=run.id,
        project_id=run.project_id,
        step_type=step_type,
        tool=tool,
        status="running",
        iteration=iteration or 0,
        request_json=_safe_json(request) if request is not None else None,
        detail=detail,
        idempotency_key=idempotency_key,
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(step)
    db.commit()
    db.refresh(step)
    record_operation_signal(
        run.operation_id,
        "tool" if step_type == "tool" else "phase",
        {"phase": step_type, "tool": tool, "iteration": iteration},
        message=detail or (f"正在执行 {tool}" if tool else f"正在进行 {step_type}"),
    )
    return step


def finish_run_step(
    db: Session,
    step: AssistantRunStep | None,
    *,
    status: str,
    result: Any = None,
    detail: str | None = None,
    error: str | None = None,
) -> None:
    if not step:
        return
    now = datetime.utcnow()
    step.status = status
    step.result_json = _safe_json(result) if result is not None else step.result_json
    step.detail = detail if detail is not None else step.detail
    step.error = error
    step.completed_at = now
    step.updated_at = now
    db.commit()
    run = step.run
    if run and run.operation_id:
        record_operation_signal(
            run.operation_id,
            "checkpoint" if status in {"completed", "ok"} else "tool",
            {"phase": step.step_type, "tool": step.tool, "step_status": status},
            message=detail or error or (f"{step.tool or step.step_type} 已完成" if status in {"completed", "ok"} else None),
        )


def mark_assistant_run(
    db: Session,
    run: AssistantRun | None,
    *,
    status: str,
    phase: str | None = None,
    error: str | None = None,
    final_reply: str | None = None,
    outcome: str | None = None,
) -> None:
    if not run:
        return
    now = datetime.utcnow()
    run.status = status
    if phase is not None:
        run.phase = phase
    run.error = error
    if final_reply is not None:
        run.final_reply = final_reply[:80_000]
    run.updated_at = now
    if status in {"completed", "error", "aborted", "cancelled"}:
        run.completed_at = now
    db.commit()
    if status == "completed":
        normalized = outcome or ("completed_with_reply" if str(final_reply or "").strip() else "empty_response")
        messages = {
            "completed_with_reply": "作品助手已回复",
            "completed_with_tools": "作品助手已完成工具操作",
            "partial_success": "作品助手完成了部分操作",
            "empty_response": "模型没有返回文字或工具结果",
            "skipped_preflight": "预检已跳过执行，等待补充信息",
            "waiting_user": "作品助手正在等待你的确认",
            "blocked": "作品助手任务已阻塞，等待你处理",
        }
        message = messages.get(normalized, "作品助手已返回结果")
        waiting = normalized in {"waiting_user", "blocked"}
        incomplete = [message] if normalized in {"partial_success", "empty_response", "skipped_preflight", "waiting_user", "blocked"} else []
        finish_operation(
            run.operation_id,
            message=message,
            status="waiting_user" if waiting else "completed",
            outcome=normalized,
            attention={
                "kind": "confirmation" if normalized == "waiting_user" else "recovery",
                "title": message,
                "message": "返回作品助手查看详情并继续。",
                "action_label": "返回作品助手",
                "action_url": f"/project/{run.project_id}",
                "blocking": True,
            } if waiting else None,
            result={
                "summary": message,
                "completed": [message] if normalized in {"completed_with_reply", "completed_with_tools", "partial_success"} else [],
                "incomplete": incomplete,
            },
        )
    elif status in {"error", "aborted"}:
        fail_operation(run.operation_id, error or "作品助手执行失败")
    elif status == "cancelled":
        finish_operation(run.operation_id, message="作品助手任务已取消", status="cancelled")


def run_payload(run: AssistantRun) -> dict:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "conversation_id": run.conversation_id,
        "assistant_message_id": run.assistant_message_id,
        "operation_id": run.operation_id,
        "status": run.status,
        "phase": run.phase,
        "scope": run.scope,
        "assistant_mode": run.assistant_mode,
        "model": run.model,
        "current_iteration": run.current_iteration or 0,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def step_payload(step: AssistantRunStep) -> dict:
    return {
        "id": step.id,
        "run_id": step.run_id,
        "step_type": step.step_type,
        "tool": step.tool,
        "status": step.status,
        "iteration": step.iteration or 0,
        "detail": step.detail,
        "error": step.error,
        "attempt_no": step.attempt_no or 1,
        "retry_of_step_id": step.retry_of_step_id,
        "resolved_step_id": step.resolved_step_id,
        "idempotency_key": step.idempotency_key,
        "started_at": step.started_at.isoformat() if step.started_at else None,
        "completed_at": step.completed_at.isoformat() if step.completed_at else None,
    }


def mark_interrupted_assistant_runs(db: Session) -> int:
    """Mark runs left running by a previous process as interrupted."""
    now = datetime.utcnow()
    runs = (
        db.query(AssistantRun)
        .filter(AssistantRun.status == "running")
        .all()
    )
    for run in runs:
        run.status = "interrupted"
        run.phase = run.phase or "interrupted"
        run.error = run.error or "应用上次关闭或服务重启时任务尚未完成"
        run.updated_at = now
        run.completed_at = now
    if runs:
        db.commit()
    return len(runs)
