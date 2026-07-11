"""Agent run service — CRUD for runs and events."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import AgentRun, AgentRunEvent
from app.services.observability.run_events import merge_event_metadata

logger = logging.getLogger(__name__)

# Payload size limits from spec
_MAX_MESSAGE = 500
_MAX_ARGS_SUMMARY = 300
_MAX_DETAIL = 2000
_MAX_PAYLOAD = 10000

_TERMINAL_STATES = {"completed", "failed", "cancelled"}

# Secret patterns to redact
_SECRET_PATTERNS = ["api_key", "secret", "credential", "token", "password"]


def _truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _redact_secrets(text: str | None) -> str | None:
    if text is None:
        return None
    import re
    for pattern in _SECRET_PATTERNS:
        text = re.sub(
            rf'("{pattern}"\s*:\s*")[^"]*(")',
            r'\1[REDACTED]\2',
            text,
            flags=re.IGNORECASE,
        )
    return text


def create_run(
    db: Session,
    project_id: str,
    *,
    source: str = "mcp",
    client_name: str | None = None,
    title: str | None = None,
) -> AgentRun:
    """Create a new Agent run."""
    run = AgentRun(
        project_id=project_id,
        source=source,
        client_name=client_name,
        title=title,
        status="created",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    logger.info("Agent run created: %s (project=%s)", run.id, project_id)
    return run


def get_run(db: Session, run_id: str) -> AgentRun | None:
    """Get a run by ID."""
    return db.query(AgentRun).filter(AgentRun.id == run_id).first()


def list_runs(
    db: Session,
    project_id: str,
    *,
    limit: int = 50,
    status: str | None = None,
) -> list[AgentRun]:
    """List runs for a project."""
    q = db.query(AgentRun).filter(AgentRun.project_id == project_id)
    if status:
        q = q.filter(AgentRun.status == status)
    return q.order_by(AgentRun.created_at.desc()).limit(limit).all()


def update_run_status(
    db: Session,
    run_id: str,
    status: str,
    *,
    current_step: str | None = None,
    summary: str | None = None,
) -> AgentRun | None:
    """Update run status and optional fields."""
    run = get_run(db, run_id)
    if not run:
        return None

    if run.status in _TERMINAL_STATES:
        logger.warning("Cannot update terminal run %s (status=%s)", run_id, run.status)
        return run

    run.status = status
    if current_step is not None:
        run.current_step = _truncate(current_step, 200)
    if summary is not None:
        run.summary = _truncate(summary, 1000)
    if status in _TERMINAL_STATES:
        run.completed_at = datetime.utcnow()
    run.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(run)
    return run


def add_event(
    db: Session,
    run_id: str,
    event_type: str,
    *,
    status: str = "ok",
    message: str | None = None,
    payload_json: str | None = None,
    model_source: str | None = None,
    tool_mode: str | None = None,
    failure_class: str | None = None,
    checkpoint_id: str | None = None,
    storage_target: str | None = None,
    next_action: str | None = None,
) -> AgentRunEvent | None:
    """Add an event to a run.

    Returns None if the run is in a terminal state.
    """
    run = get_run(db, run_id)
    if not run:
        return None

    if run.status in _TERMINAL_STATES:
        logger.warning("Cannot add event to terminal run %s", run_id)
        return None

    # Get next sequence number
    last_event = (
        db.query(AgentRunEvent)
        .filter(AgentRunEvent.run_id == run_id)
        .order_by(AgentRunEvent.sequence.desc())
        .first()
    )
    next_seq = (last_event.sequence + 1) if last_event else 1

    # Truncate and redact
    message = _truncate(message, _MAX_MESSAGE)
    payload_json = merge_event_metadata(
        payload_json,
        event_type=event_type,
        status=status,
        message=message,
        model_source=model_source,
        tool_mode=tool_mode,
        failure_class=failure_class,
        checkpoint_id=checkpoint_id,
        storage_target=storage_target,
        next_action=next_action,
    )
    payload_json = _truncate(payload_json, _MAX_PAYLOAD)
    payload_json = _redact_secrets(payload_json)

    event = AgentRunEvent(
        run_id=run_id,
        sequence=next_seq,
        event_type=event_type,
        status=status,
        message=message,
        payload_json=payload_json,
    )
    db.add(event)

    # Update run status based on event type
    if event_type == "run_finished":
        run.status = "completed"
        run.completed_at = datetime.utcnow()
    elif event_type == "error":
        run.status = "failed"
        run.completed_at = datetime.utcnow()
    elif run.status == "created":
        run.status = "running"

    run.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(event)
    return event


def get_events(
    db: Session,
    run_id: str,
    *,
    after_sequence: int = 0,
    limit: int = 500,
) -> list[AgentRunEvent]:
    """Get events for a run, optionally after a given sequence number."""
    return (
        db.query(AgentRunEvent)
        .filter(
            AgentRunEvent.run_id == run_id,
            AgentRunEvent.sequence > after_sequence,
        )
        .order_by(AgentRunEvent.sequence)
        .limit(limit)
        .all()
    )


def cancel_run(db: Session, run_id: str) -> AgentRun | None:
    """Cancel a run."""
    run = get_run(db, run_id)
    if not run:
        return None
    if run.status in _TERMINAL_STATES:
        return run

    add_event(db, run_id, "run_finished", status="ok", message="Run cancelled by user")
    run.status = "cancelled"
    run.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(run)
    return run
