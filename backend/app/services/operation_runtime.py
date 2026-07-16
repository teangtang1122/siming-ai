"""Durable progress and health projection for long-running operations."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Callable, Iterator, TypeVar

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database.models import OperationEvent, OperationRun
from ..database.session import SessionLocal
from .observability.run_events import classify_failure


ACTIVE_STATUSES = {"queued", "running", "waiting_user", "paused"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
HEALTH_VALUES = {"active", "quiet", "suspected_stall", "stalled", "disconnected"}
_CURRENT_OPERATION_ID: ContextVar[str | None] = ContextVar("siming_operation_id", default=None)
_ACTION_HANDLERS: dict[str, dict[str, Callable[[], Any]]] = {}
T = TypeVar("T")


def utcnow() -> datetime:
    return datetime.utcnow()


def input_snapshot_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def current_operation_id() -> str | None:
    return _CURRENT_OPERATION_ID.get()


@contextmanager
def activate_operation(operation_id: str | None) -> Iterator[None]:
    token = _CURRENT_OPERATION_ID.set(operation_id or None)
    try:
        yield
    finally:
        _CURRENT_OPERATION_ID.reset(token)


async def iterate_with_operation(operation_id: str | None, source: AsyncIterator[T]) -> AsyncIterator[T]:
    with activate_operation(operation_id):
        async for item in source:
            yield item


def ensure_operation(
    db: Session,
    *,
    source_kind: str,
    source_id: str,
    title: str,
    project_id: str | None = None,
    status: str = "running",
    phase: str | None = None,
    message: str | None = None,
    model_source: str | None = None,
    tool_mode: str | None = None,
    resume_url: str | None = None,
    can_pause: bool = False,
    can_cancel: bool = True,
    can_retry: bool = True,
    progress_mode: str = "indeterminate",
    progress_current: int | None = None,
    progress_total: int | None = None,
    input_revision: int | None = None,
    snapshot_hash: str | None = None,
) -> OperationRun:
    operation = (
        db.query(OperationRun)
        .filter(OperationRun.source_kind == source_kind, OperationRun.source_id == str(source_id))
        .first()
    )
    now = utcnow()
    if operation is None:
        operation = OperationRun(
            source_kind=source_kind,
            source_id=str(source_id),
            project_id=project_id,
            title=title[:300],
            status=status,
            health_status="active",
            phase=phase,
            current_message=message,
            model_source=model_source,
            tool_mode=tool_mode,
            resume_url=resume_url,
            can_pause=can_pause,
            can_cancel=can_cancel,
            can_retry=can_retry,
            progress_mode=progress_mode,
            progress_current=progress_current,
            progress_total=progress_total,
            input_revision=input_revision,
            input_snapshot_hash=snapshot_hash,
            heartbeat_at=now,
            last_activity_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(operation)
        db.flush()
        add_operation_event(db, operation, "started", status, message or title, {"phase": phase})
        return operation

    operation.title = title[:300] or operation.title
    operation.project_id = project_id or operation.project_id
    operation.status = status or operation.status
    operation.phase = phase or operation.phase
    operation.current_message = message or operation.current_message
    operation.model_source = model_source or operation.model_source
    operation.tool_mode = tool_mode or operation.tool_mode
    operation.resume_url = resume_url or operation.resume_url
    operation.can_pause = can_pause
    operation.can_cancel = can_cancel
    operation.can_retry = can_retry
    operation.progress_mode = progress_mode or operation.progress_mode
    operation.progress_current = progress_current if progress_current is not None else operation.progress_current
    operation.progress_total = progress_total if progress_total is not None else operation.progress_total
    operation.input_revision = input_revision if input_revision is not None else operation.input_revision
    operation.input_snapshot_hash = snapshot_hash or operation.input_snapshot_hash
    operation.updated_at = now
    return operation


def add_operation_event(
    db: Session,
    operation: OperationRun,
    event_type: str,
    status: str,
    message: str | None,
    payload: dict[str, Any] | None = None,
) -> OperationEvent:
    sequence = int(
        db.query(func.coalesce(func.max(OperationEvent.sequence), 0))
        .filter(OperationEvent.run_id == operation.id)
        .scalar()
        or 0
    ) + 1
    event = OperationEvent(
        run_id=operation.id,
        sequence=sequence,
        event_type=event_type,
        status=status,
        message=(message or "")[:2000] or None,
        payload_json=deepcopy(payload) if payload else None,
        created_at=utcnow(),
    )
    db.add(event)
    db.flush()
    return event


def update_operation(
    db: Session,
    operation: OperationRun,
    *,
    status: str | None = None,
    health_status: str | None = None,
    phase: str | None = None,
    message: str | None = None,
    event_type: str | None = None,
    payload: dict[str, Any] | None = None,
    progress_current: int | None = None,
    progress_total: int | None = None,
    progress_mode: str | None = None,
    failure_class: str | None = None,
    next_action: str | None = None,
    checkpoint: bool = False,
    output: bool = False,
    process_metrics: dict[str, Any] | None = None,
    activity: bool = False,
) -> OperationRun:
    now = utcnow()
    if status:
        operation.status = status
    if health_status in HEALTH_VALUES:
        operation.health_status = health_status
    if phase is not None:
        operation.phase = phase
    if message is not None:
        operation.current_message = message[:2000]
    if progress_current is not None:
        operation.progress_current = progress_current
    if progress_total is not None:
        operation.progress_total = progress_total
    if progress_mode is not None:
        operation.progress_mode = progress_mode
    if failure_class is not None:
        operation.failure_class = failure_class
    if next_action is not None:
        operation.next_action = next_action
    if process_metrics is not None:
        operation.process_metrics_json = deepcopy(process_metrics)
    operation.heartbeat_at = now
    operation.updated_at = now
    health_only_events = {"heartbeat", "quiet", "suspected_stall", "stalled", "disconnected"}
    if activity or (event_type is not None and event_type not in health_only_events) or output or checkpoint:
        operation.last_activity_at = now
    if output:
        operation.last_output_at = now
    if checkpoint:
        operation.last_checkpoint_at = now
    if operation.status in TERMINAL_STATUSES:
        operation.completed_at = operation.completed_at or now
        operation.can_cancel = False
        operation.can_pause = False
    elif operation.status in ACTIVE_STATUSES:
        operation.completed_at = None
    if event_type and event_type != "heartbeat":
        add_operation_event(db, operation, event_type, operation.status, message, payload)
    return operation


def record_operation_signal(
    operation_id: str | None,
    signal: str,
    payload: dict[str, Any] | None = None,
    message: str | None = None,
    *,
    db: Session | None = None,
) -> None:
    if not operation_id:
        return

    def _record(session: Session) -> None:
        operation = session.query(OperationRun).filter(OperationRun.id == operation_id).first()
        if operation is None or operation.status not in ACTIVE_STATUSES:
            return
        metrics = payload if signal in {"process", "quiet", "suspected_stall", "stalled"} else None
        health = (
            None
            if signal == "heartbeat"
            else signal if signal in {"quiet", "suspected_stall", "stalled", "disconnected"} else "active"
        )
        event_type = signal if signal in {
            "output", "tool", "checkpoint", "phase", "model_fallback", "error", "suspected_stall", "stalled", "disconnected"
        } else None
        update_operation(
            session,
            operation,
            status=(payload or {}).get("lifecycle_status") if isinstance(payload, dict) else None,
            health_status=health,
            phase=(payload or {}).get("phase") if isinstance(payload, dict) else None,
            message=message,
            event_type=event_type,
            payload=payload,
            progress_current=(payload or {}).get("progress_current") if isinstance(payload, dict) else None,
            progress_total=(payload or {}).get("progress_total") if isinstance(payload, dict) else None,
            progress_mode=(payload or {}).get("progress_mode") if isinstance(payload, dict) else None,
            output=signal == "output",
            checkpoint=signal == "checkpoint",
            process_metrics=metrics,
            activity=signal in {"output", "tool", "checkpoint", "phase", "model_fallback", "process", "error"},
        )
        session.commit()

    if db is not None:
        _record(db)
        return
    with SessionLocal() as session:
        _record(session)


def heartbeat_operation(
    operation_id: str | None,
    message: str | None = None,
    *,
    db: Session | None = None,
) -> None:
    if not operation_id:
        return

    def _heartbeat(session: Session) -> None:
        operation = session.query(OperationRun).filter(OperationRun.id == operation_id).first()
        if operation is None or operation.status not in ACTIVE_STATUSES:
            return
        operation.heartbeat_at = utcnow()
        operation.updated_at = operation.heartbeat_at
        if message:
            operation.current_message = message[:2000]
        session.commit()

    if db is not None:
        _heartbeat(db)
        return
    with SessionLocal() as session:
        _heartbeat(session)


async def heartbeat_loop(operation_id: str, *, interval_seconds: float = 5.0) -> None:
    while True:
        heartbeat_operation(operation_id)
        await asyncio.sleep(interval_seconds)


def finish_operation(
    operation_id: str | None,
    *,
    message: str,
    status: str = "completed",
    next_action: str | None = None,
    db: Session | None = None,
) -> None:
    if not operation_id:
        return

    def _finish(session: Session) -> None:
        operation = session.query(OperationRun).filter(OperationRun.id == operation_id).first()
        if operation is None:
            return
        update_operation(
            session,
            operation,
            status=status,
            health_status="active" if status == "completed" else operation.health_status,
            message=message,
            event_type=status,
            next_action=next_action,
        )
        session.commit()

    if db is not None:
        _finish(db)
        return
    with SessionLocal() as session:
        _finish(session)


def fail_operation(
    operation_id: str | None,
    error: BaseException | str,
    *,
    next_action: str | None = None,
    db: Session | None = None,
) -> None:
    message = str(error or "任务失败")
    if not operation_id:
        return

    def _fail(session: Session) -> None:
        operation = session.query(OperationRun).filter(OperationRun.id == operation_id).first()
        if operation is None:
            return
        update_operation(
            session,
            operation,
            status="failed",
            health_status="stalled" if "卡住" in message else operation.health_status,
            message=message,
            event_type="failed",
            failure_class=classify_failure(message) or "unknown",
            next_action=next_action,
        )
        session.commit()

    if db is not None:
        _fail(db)
        return
    with SessionLocal() as session:
        _fail(session)


def register_operation_actions(operation_id: str, **handlers: Callable[[], Any]) -> None:
    _ACTION_HANDLERS[operation_id] = {name: handler for name, handler in handlers.items() if callable(handler)}


def unregister_operation_actions(operation_id: str) -> None:
    _ACTION_HANDLERS.pop(operation_id, None)


async def invoke_operation_action(operation_id: str, action: str) -> bool:
    handler = _ACTION_HANDLERS.get(operation_id, {}).get(action)
    if handler is None:
        return False
    result = handler()
    if asyncio.iscoroutine(result):
        await result
    return True


def _derived_health(operation: OperationRun, now: datetime) -> str:
    if operation.status not in ACTIVE_STATUSES:
        return operation.health_status
    heartbeat = operation.heartbeat_at or operation.updated_at or operation.created_at
    if heartbeat and now - heartbeat > timedelta(seconds=60):
        return "disconnected"
    activity = operation.last_activity_at or operation.created_at
    if activity and now - activity > timedelta(minutes=30):
        return "suspected_stall"
    output = operation.last_output_at or operation.created_at
    if output and now - output > timedelta(minutes=10):
        return "quiet"
    return operation.health_status or "active"


def serialize_operation(operation: OperationRun, *, include_events: bool = False) -> dict[str, Any]:
    now = utcnow()
    elapsed = max(0, int(((operation.completed_at or now) - operation.created_at).total_seconds())) if operation.created_at else 0
    percent = None
    if operation.progress_mode == "determinate" and operation.progress_total:
        percent = max(0, min(100, round((operation.progress_current or 0) / operation.progress_total * 100)))
    data = {
        "id": operation.id,
        "source_kind": operation.source_kind,
        "source_id": operation.source_id,
        "project_id": operation.project_id,
        "title": operation.title,
        "status": operation.status,
        "health_status": _derived_health(operation, now),
        "phase": operation.phase,
        "current_message": operation.current_message,
        "progress": {
            "mode": operation.progress_mode,
            "current": operation.progress_current,
            "total": operation.progress_total,
            "percent": percent,
        },
        "model_source": operation.model_source,
        "tool_mode": operation.tool_mode,
        "failure_class": operation.failure_class,
        "next_action": operation.next_action,
        "resume_url": operation.resume_url,
        "can_pause": bool(operation.can_pause),
        "can_cancel": bool(operation.can_cancel),
        "can_retry": bool(operation.can_retry),
        "input_revision": operation.input_revision,
        "input_snapshot_hash": operation.input_snapshot_hash,
        "process_metrics": deepcopy(operation.process_metrics_json),
        "elapsed_seconds": elapsed,
        "heartbeat_at": operation.heartbeat_at.isoformat() if operation.heartbeat_at else None,
        "last_activity_at": operation.last_activity_at.isoformat() if operation.last_activity_at else None,
        "last_output_at": operation.last_output_at.isoformat() if operation.last_output_at else None,
        "last_checkpoint_at": operation.last_checkpoint_at.isoformat() if operation.last_checkpoint_at else None,
        "created_at": operation.created_at.isoformat() if operation.created_at else None,
        "updated_at": operation.updated_at.isoformat() if operation.updated_at else None,
        "completed_at": operation.completed_at.isoformat() if operation.completed_at else None,
    }
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
            for event in operation.events
        ]
    return data


def mark_interrupted_operations(db: Session) -> int:
    runs = db.query(OperationRun).filter(OperationRun.status.in_(["queued", "running", "waiting_user"])).all()
    now = utcnow()
    for operation in runs:
        operation.status = "interrupted"
        operation.health_status = "disconnected"
        operation.current_message = "司命上次关闭时任务仍在运行，可从最近检查点重试"
        operation.next_action = "重新打开原页面并重试当前单元"
        operation.completed_at = now
        operation.updated_at = now
        add_operation_event(db, operation, "interrupted", "interrupted", operation.current_message)
    if runs:
        db.commit()
    return len(runs)


def stall_seconds_from_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default
