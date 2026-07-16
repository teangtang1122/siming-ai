"""Regression tests for unified long-running operation state."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, ModelDownloadTask, OperationRun, Project
from app.services.context_orchestrator import ContextOrchestrator
from app.services.local_runtime.model_jobs import _set_task
from app.services.operation_runtime import (
    ensure_operation,
    input_snapshot_hash,
    invoke_operation_action,
    record_operation_signal,
    register_operation_actions,
    serialize_operation,
    unregister_operation_actions,
    update_operation,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return engine, Session, Session()


def test_health_is_derived_from_heartbeat_activity_and_output_independently():
    _engine, _Session, db = _db()
    operation = ensure_operation(
        db,
        source_kind="test",
        source_id="health",
        title="Health test",
        status="running",
    )
    db.commit()
    now = datetime.utcnow()

    operation.heartbeat_at = now
    operation.last_activity_at = now
    operation.last_output_at = now - timedelta(minutes=11)
    assert serialize_operation(operation)["health_status"] == "quiet"

    operation.heartbeat_at = now
    operation.last_activity_at = now - timedelta(minutes=31)
    assert serialize_operation(operation)["health_status"] == "suspected_stall"

    operation.heartbeat_at = now - timedelta(seconds=61)
    assert serialize_operation(operation)["health_status"] == "disconnected"


def test_operation_events_keep_monotonic_sequences_before_commit():
    _engine, _Session, db = _db()
    operation = ensure_operation(
        db,
        source_kind="test",
        source_id="events",
        title="Event test",
    )
    update_operation(db, operation, event_type="phase", message="phase one")
    update_operation(db, operation, event_type="checkpoint", message="checkpoint", checkpoint=True)
    db.commit()

    assert [event.sequence for event in operation.events] == [1, 2, 3]
    assert input_snapshot_hash({"b": 2, "a": 1}) == input_snapshot_hash({"a": 1, "b": 2})


def test_heartbeat_preserves_quiet_health_and_real_activity_timestamp():
    _engine, _Session, db = _db()
    operation = ensure_operation(db, source_kind="test", source_id="heartbeat", title="Heartbeat test")
    operation.health_status = "quiet"
    operation.last_activity_at = datetime.utcnow() - timedelta(minutes=12)
    previous_activity = operation.last_activity_at
    db.commit()

    record_operation_signal(operation.id, "heartbeat", {"alive": True}, db=db)
    assert operation.health_status == "quiet"
    assert operation.last_activity_at == previous_activity

    record_operation_signal(operation.id, "process", {"cpu_seconds": 10}, db=db)
    assert operation.health_status == "active"
    assert operation.last_activity_at > previous_activity


def test_operation_actions_only_run_registered_handlers():
    calls: list[str] = []
    register_operation_actions("operation-1", pause=lambda: calls.append("pause"))
    try:
        assert asyncio.run(invoke_operation_action("operation-1", "pause")) is True
        assert asyncio.run(invoke_operation_action("operation-1", "cancel")) is False
    finally:
        unregister_operation_actions("operation-1")
    assert calls == ["pause"]


def test_context_rebuild_projects_progress_and_checkpoints_to_operation_center():
    _engine, _Session, db = _db()
    project = Project(title="Context project", description="test")
    db.add(project)
    db.commit()
    orchestrator = ContextOrchestrator(db)
    job = orchestrator.create_rebuild_job(requested_by="test", project_ids=[project.id])
    db.commit()

    with patch("app.services.context_orchestrator.reindex_project", return_value={"total_chunks": 4}), patch.object(
        orchestrator,
        "build_semantic_embeddings",
        return_value={"indexed": 3},
    ):
        orchestrator.run_rebuild_job(job)

    operation = db.query(OperationRun).filter(OperationRun.id == job.operation_id).one()
    payload = serialize_operation(operation, include_events=True)
    assert job.status == "completed"
    assert payload["status"] == "completed"
    assert payload["progress"]["current"] == 1
    assert payload["progress"]["total"] == 1
    assert any(event["event_type"] == "checkpoint" for event in payload["events"])


def test_download_byte_progress_is_projected_without_fake_percentage():
    _engine, Session, db = _db()
    task = ModelDownloadTask(
        kind="model",
        target_key="test-model",
        destination_path="test.gguf",
        status="queued",
    )
    db.add(task)
    db.flush()
    operation = ensure_operation(
        db,
        source_kind="download",
        source_id=task.id,
        title="Download test",
        status="queued",
    )
    task.operation_id = operation.id
    db.commit()

    with patch("app.services.local_runtime.model_jobs.SessionLocal", Session):
        _set_task(task.id, status="downloading", downloaded_bytes=25, total_bytes=100)

    db.expire_all()
    operation = db.query(OperationRun).filter(OperationRun.id == operation.id).one()
    payload = serialize_operation(operation)
    assert payload["status"] == "running"
    assert payload["progress"] == {"mode": "determinate", "current": 25, "total": 100, "percent": 25}
