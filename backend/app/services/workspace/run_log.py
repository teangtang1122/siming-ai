"""Durable execution log for workspace assistant runs."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import AssistantRun, AssistantRunStep


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


def mark_assistant_run(
    db: Session,
    run: AssistantRun | None,
    *,
    status: str,
    phase: str | None = None,
    error: str | None = None,
    final_reply: str | None = None,
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


def run_payload(run: AssistantRun) -> dict:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "conversation_id": run.conversation_id,
        "assistant_message_id": run.assistant_message_id,
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
