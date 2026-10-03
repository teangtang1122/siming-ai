"""Persistence and operation projection for novel-creation stage runs."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, object_session

from app.database.models import (
    NovelCreationSession,
    NovelCreationStageEvent,
    NovelCreationStageRun,
    OperationRun,
)
from app.services.novel_creation_contract import STAGE_LABELS, STAGE_ORDER
from app.services.novel_creation_failures import build_stage_failure
from app.services.observability.run_events import classify_failure
from app.services.operation_runtime import ensure_operation, input_snapshot_hash, update_operation
from app.modules.operations.application.trace_capture import correlate


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def invalidate_run_card_presentation(run: NovelCreationStageRun) -> None:
    """Drop a cached display verdict whenever durable run state changes."""
    if not isinstance(run.result_json, dict) or "card_presentation" not in run.result_json:
        return
    result = deepcopy(run.result_json)
    result.pop("card_presentation", None)
    run.result_json = result


def create_run(
    db: Session,
    session: NovelCreationSession,
    stage: str,
    request: dict[str, Any],
    *,
    claim_id: str | None = None,
    idempotency_key: str | None = None,
    frozen_input_snapshot: dict[str, Any] | None = None,
    frozen_input_revision: int | None = None,
) -> NovelCreationStageRun:
    model = _text(request.get("model")) or None
    draft = session.draft_json if isinstance(session.draft_json, dict) else {}
    input_snapshot = deepcopy(
        frozen_input_snapshot if isinstance(frozen_input_snapshot, dict) else draft
    )
    revision = int(
        frozen_input_revision
        if frozen_input_revision is not None
        else session.revision or 0
    )
    snapshot_hash = input_snapshot_hash(input_snapshot)
    run = NovelCreationStageRun(
        session_id=session.id,
        stage=stage,
        operation=_text(request.get("operation"), "generate")[:30],
        status="running",
        model_source=model,
        tool_mode="session_stage",
        storage_target="session_draft",
        context_manifest_id=_text(request.get("context_manifest_id")) or None,
        request_json=deepcopy(request),
        current_message=f"正在生成{STAGE_LABELS.get(stage, stage)}",
        input_revision=revision,
        input_snapshot_hash=snapshot_hash,
        claim_id=claim_id,
        idempotency_key=idempotency_key,
    )
    db.add(run)
    db.flush()
    operation = ensure_operation(
        db,
        source_kind="novel_creation",
        source_id=run.id,
        title=f"新书立项 · {STAGE_LABELS.get(stage, stage)}",
        status="running",
        phase=stage,
        message=run.current_message,
        model_source=model,
        tool_mode="session_stage",
        resume_url=f"/novel-creation?session={session.id}&run={run.id}",
        can_pause=True,
        can_cancel=True,
        can_retry=False,
        input_revision=revision,
        snapshot_hash=snapshot_hash,
    )
    run.operation_id = operation.id
    correlate(run_id=run.id, operation_id=operation.id)
    if claim_id:
        from app.database.models import NovelCreationRunClaim

        claim = db.get(NovelCreationRunClaim, claim_id)
        if claim:
            claim.run_id = run.id
            claim.operation_id = operation.id
    request_copy = deepcopy(request)
    request_copy["input_revision"] = revision
    request_copy["input_snapshot_hash"] = snapshot_hash
    request_copy["input_snapshot"] = input_snapshot
    request_copy["operation_id"] = operation.id
    run.request_json = request_copy
    add_run_event(
        db,
        run,
        "started",
        "running",
        run.current_message,
        {"model_source": model, "storage_target": "session_draft"},
    )
    return run


def add_run_event(
    db: Session,
    run: NovelCreationStageRun,
    event_type: str,
    status: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> NovelCreationStageEvent:
    sequence = len(run.events or []) + 1
    event = NovelCreationStageEvent(
        run_id=run.id,
        sequence=sequence,
        event_type=event_type,
        status=status,
        message=message,
        payload_json=deepcopy(payload) if payload else None,
    )
    db.add(event)
    db.flush()
    if run.operation_id:
        operation = db.query(OperationRun).filter(OperationRun.id == run.operation_id).first()
        if operation:
            if isinstance(payload, dict) and payload.get("model_source"):
                operation.model_source = str(payload["model_source"])
            progress_current = None
            progress_total = None
            progress_mode = None
            if isinstance(payload, dict) and payload.get("stage"):
                stage_name = str(payload["stage"])
                if stage_name in STAGE_ORDER:
                    progress_current = STAGE_ORDER.index(stage_name) + (
                        1 if event_type == "stage_completed" else 0
                    )
                    progress_total = len(STAGE_ORDER)
                    progress_mode = "determinate" if run.stage == "all" else "indeterminate"
            update_operation(
                db,
                operation,
                phase=str((payload or {}).get("stage") or run.stage),
                message=message,
                event_type=event_type,
                payload=payload,
                progress_current=progress_current,
                progress_total=progress_total,
                progress_mode=progress_mode,
                checkpoint=event_type == "stage_completed",
                health_status="active",
            )
    return event


def _complete_claim(
    db: Session,
    run: NovelCreationStageRun,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    status: str | None = None,
) -> None:
    if not run.claim_id:
        return
    from app.services.novel_creation_claims import complete_creation_claim

    complete_creation_claim(db, run.claim_id, result=result, error=error, status=status)


def complete_run(db: Session, run: NovelCreationStageRun, result: dict[str, Any]) -> None:
    # Saving generated output is a successful checkpoint, but the author still
    # owns the decision to accept it. Keep that state distinct from a failure.
    run.status = "waiting_user"
    run.result_json = deepcopy(result)
    run.current_message = "阶段结果已保存到立项草稿，等待作者确认"
    run.next_action = "审阅并确认本阶段，或编辑后重新生成"
    run.completed_at = datetime.utcnow()
    add_run_event(
        db,
        run,
        "waiting_user",
        "waiting_user",
        run.current_message,
        {"storage_target": run.storage_target, "next_action": run.next_action},
    )
    if not run.operation_id:
        _complete_claim(db, run, result=result)
        return
    operation = db.query(OperationRun).filter(OperationRun.id == run.operation_id).first()
    if not operation:
        _complete_claim(db, run, result=result)
        return
    attention_stage = run.session.current_stage if run.stage == "all" else run.stage
    update_operation(
        db,
        operation,
        status="waiting_user",
        health_status="active",
        message=run.current_message,
        next_action=run.next_action,
        checkpoint=True,
        attention={
            "kind": "confirmation",
            "title": "阶段内容等待确认",
            "message": run.next_action,
            "action_label": "审阅阶段内容",
            "action_url": f"/novel-creation?session={run.session_id}&stage={attention_stage}",
            "blocking": True,
        },
        result={
            "summary": run.current_message,
            "completed": [f"{STAGE_LABELS.get(attention_stage, attention_stage)}内容已生成并保存到立项草稿"],
            "incomplete": ["阶段尚未由作者确认"],
        },
        outcome="awaiting_confirmation",
    )
    operation.can_pause = False
    operation.can_cancel = False
    operation.can_retry = True
    _complete_claim(db, run, result=result)


def confirm_run(db: Session, run: NovelCreationStageRun) -> bool:
    """Complete the exact generated run after an author confirmation."""
    if run.status == "completed":
        return True
    if run.status not in {"waiting_user", "waiting_author"}:
        return False

    invalidate_run_card_presentation(run)

    run.status = "completed"
    run.current_message = "阶段内容已由作者确认"
    run.next_action = "继续处理下一阶段"
    run.completed_at = datetime.utcnow()
    add_run_event(
        db,
        run,
        "author_confirmed",
        "completed",
        run.current_message,
        {"next_action": run.next_action},
    )
    return True


def fail_run(
    db: Session,
    run: NovelCreationStageRun,
    exc: Exception,
    *,
    failed_stage: str | None = None,
) -> None:
    from app.modules.creation.domain.generation_contract import CreationGenerationError

    message = _text(exc, "阶段生成失败")
    failure_class = _text(getattr(exc, "failure_class", "")) or classify_failure(message) or "unknown"
    retry_stage = failed_stage or run.stage
    retry_label = STAGE_LABELS.get(retry_stage, retry_stage)
    advice, failure_payload = build_stage_failure(
        failure_class=failure_class,
        message=message,
        run_id=run.id,
        failed_stage=retry_stage,
        failed_stage_label=retry_label,
    )
    if isinstance(exc, CreationGenerationError):
        diagnostic = exc.tool_result("generate_creation_artifact")["data"]
        failure_payload.update(diagnostic)
        run.result_json = {"status": "error", **diagnostic, "attempt": exc.attempt}
    if failure_class == "revision_conflict":
        advice = "保留当前人工修改，检查草稿后重新生成本阶段。"
        failure_payload["next_action"] = advice
        failure_payload["retryable"] = True
        candidate_artifact = _text(getattr(exc, "candidate_artifact", ""))
        candidate_data = getattr(exc, "candidate_data", None)
        if candidate_artifact and isinstance(candidate_data, dict):
            run.result_json = {
                "status": "conflict",
                "candidate_artifact": candidate_artifact,
                "candidate_data": deepcopy(candidate_data),
                "input_revision": run.input_revision,
                "current_revision": int(run.session.revision or 0),
            }
            failure_payload["candidate_available"] = True
            failure_payload["candidate_artifact"] = candidate_artifact
    run.status = "failed"
    run.failure_class = failure_class
    run.current_message = message[:1000]
    run.next_action = advice
    run.completed_at = datetime.utcnow()
    run.session.last_error_json = failure_payload
    add_run_event(db, run, "failed", "error", message, failure_payload)
    _complete_claim(db, run, error=message, status="failed")
    if not run.operation_id:
        return
    operation = db.query(OperationRun).filter(OperationRun.id == run.operation_id).first()
    if operation:
        update_operation(
            db,
            operation,
            status="failed",
            health_status="stalled" if "卡住" in message else operation.health_status,
            message=message,
            failure_class=failure_class,
            next_action=advice,
        )
        operation.can_cancel = False
        operation.can_retry = True


def serialize_run(run: NovelCreationStageRun, include_events: bool = True) -> dict[str, Any]:
    result = deepcopy(run.result_json) if isinstance(run.result_json, dict) else None
    data = {
        "run_id": run.id,
        "id": run.id,
        "session_id": run.session_id,
        "stage": run.stage,
        "operation": run.operation,
        "status": "waiting_user" if run.status == "waiting_author" else run.status,
        "model_source": run.model_source,
        "tool_mode": run.tool_mode,
        "failure_class": run.failure_class,
        "storage_target": run.storage_target,
        "context_manifest_id": run.context_manifest_id,
        "operation_id": run.operation_id,
        "input_revision": run.input_revision,
        "input_snapshot_hash": run.input_snapshot_hash,
        "next_action": run.next_action,
        "result": result,
        "attempt": int((result or {}).get("attempt") or 0),
        "result_mode": (result or {}).get("result_mode"),
        "warning": (result or {}).get("warning"),
        "diagnostic_count": len(run.diagnostics_json or []),
        "current_message": run.current_message,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }
    session = object_session(run)
    operation = (
        session.get(OperationRun, run.operation_id)
        if session and run.operation_id
        else None
    )
    stream_progress = (
        deepcopy(operation.process_metrics_json)
        if operation and isinstance(operation.process_metrics_json, dict)
        else None
    )
    if stream_progress and stream_progress.get("kind") == "model_output":
        data["stream_progress"] = stream_progress
    card_presentation = (result or {}).get("card_presentation")
    normalized_status = "waiting_user" if run.status == "waiting_author" else run.status
    if (
        isinstance(card_presentation, dict)
        and _text(card_presentation.get("raw_status")) == normalized_status
    ):
        data["card_presentation"] = deepcopy(card_presentation)
    if include_events:
        data["events"] = [
            {
                "sequence": event.sequence,
                "event_type": event.event_type,
                "status": event.status,
                "message": event.message,
                "payload": deepcopy(event.payload_json),
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in run.events
        ]
    return data


def interrupt_novel_creation_run(
    db: Session,
    run: NovelCreationStageRun,
    *,
    message: str = "立项模型连接已中断，本阶段未完成",
    next_action: str = "检查已保存草稿后重新生成本阶段",
) -> bool:
    """Finish an abandoned producer and its operation as one durable state."""

    if run.status not in {"queued", "running"}:
        return False
    invalidate_run_card_presentation(run)
    now = datetime.utcnow()
    run.status = "interrupted"
    run.failure_class = "interrupted"
    run.current_message = message
    run.next_action = next_action
    run.completed_at = now
    run.updated_at = now
    payload = {
        "failure_class": "interrupted",
        "next_action": next_action,
        "retryable": True,
    }
    sequence = max([int(event.sequence or 0) for event in run.events] or [0]) + 1
    db.add(NovelCreationStageEvent(
        run_id=run.id,
        sequence=sequence,
        event_type="interrupted",
        status="interrupted",
        message=message,
        payload_json=deepcopy(payload),
        created_at=now,
    ))
    _complete_claim(db, run, error=message, status="interrupted")

    operation = db.get(OperationRun, run.operation_id) if run.operation_id else None
    if operation:
        update_operation(
            db,
            operation,
            status="interrupted",
            health_status="disconnected",
            message=message,
            event_type="interrupted",
            payload=payload,
            failure_class="interrupted",
            next_action=next_action,
            attention={
                "kind": "recovery",
                "title": "立项生成已中断",
                "message": next_action,
                "blocking": True,
            },
            result={
                "summary": message,
                "completed": [],
                "incomplete": [next_action],
            },
            outcome="interrupted",
        )
        operation.can_retry = True
    return True


def mark_interrupted_novel_creation_runs(db: Session) -> int:
    """Release stage runs whose in-process producer disappeared on restart."""

    runs = (
        db.query(NovelCreationStageRun)
        .filter(NovelCreationStageRun.status == "running")
        .all()
    )
    now = datetime.utcnow()
    for run in runs:
        # Startup has no surviving producer. A saved result remains available
        # for author review; every other active producer becomes recoverable.
        if isinstance(run.result_json, dict):
            run.status = "waiting_user"
            run.failure_class = None
            run.current_message = "阶段结果已保存，等待作者确认"
            run.next_action = "审阅并确认本阶段，或编辑后重新生成"
            event_type = "recovered_waiting_user"
            event_status = "waiting_user"
        else:
            run.status = "interrupted"
            run.failure_class = "interrupted"
            run.current_message = "应用上次关闭或服务重启时，本阶段尚未完成"
            run.next_action = "检查已保存草稿后重新生成本阶段"
            event_type = "interrupted"
            event_status = "interrupted"
        run.completed_at = now
        run.updated_at = now
        sequence = max([int(event.sequence or 0) for event in run.events] or [0]) + 1
        db.add(
            NovelCreationStageEvent(
                run_id=run.id,
                sequence=sequence,
                event_type=event_type,
                status=event_status,
                message=run.current_message,
                payload_json={
                    "failure_class": run.failure_class,
                    "next_action": run.next_action,
                    "retryable": run.status == "interrupted",
                },
                created_at=now,
            )
        )
        _complete_claim(
            db,
            run,
            result=deepcopy(run.result_json) if isinstance(run.result_json, dict) else None,
            error=None if run.status == "waiting_user" else run.current_message,
            status="completed" if run.status == "waiting_user" else "interrupted",
        )
    if runs:
        db.flush()
    return sum(1 for run in runs if run.status == "interrupted")
