"""Regression tests for unified long-running operation state."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, ModelDownloadTask, OperationRun, Project
from app.modules.operations.infrastructure.service import SqlAlchemyOperationService
from app.services.context_orchestrator import ContextOrchestrator
from app.services.external_agent.run_service import (
    add_event as add_agent_event,
)
from app.services.external_agent.run_service import (
    create_run as create_agent_run,
)
from app.services.external_agent.run_service import (
    update_run_status as update_agent_run_status,
)
from app.services.external_agent.write_requests import confirm_write, request_write
from app.services.local_runtime.model_jobs import _set_task
from app.services.operation_runtime import (
    ensure_operation,
    input_snapshot_hash,
    invoke_operation_action,
    mark_interrupted_operations,
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


def test_operation_service_deletes_only_terminal_records():
    _engine, Session, db = _db()
    completed = ensure_operation(
        db,
        source_kind="test",
        source_id="delete-completed",
        title="Delete completed",
        status="completed",
    )
    running = ensure_operation(
        db,
        source_kind="test",
        source_id="keep-running",
        title="Keep running",
        status="running",
    )
    db.commit()
    completed_id = completed.id
    running_id = running.id
    db.close()

    service = SqlAlchemyOperationService()
    with patch("app.modules.operations.infrastructure.service.SessionLocal", Session):
        assert service.delete(running_id) == "not_terminal"
        assert service.delete(completed_id) == "ok"
        assert service.delete(completed_id) == "not_found"

    with Session() as verify:
        assert verify.get(OperationRun, running_id) is not None
        assert verify.get(OperationRun, completed_id) is None


def test_operation_service_filters_project_and_source_for_embedded_status_messages():
    _engine, Session, db = _db()
    project_a = Project(title="Project A", description="test")
    project_b = Project(title="Project B", description="test")
    db.add_all([project_a, project_b])
    db.flush()
    matching = ensure_operation(
        db,
        source_kind="cataloging",
        source_id="project-a-cataloging",
        project_id=project_a.id,
        title="Project A cataloging",
    )
    ensure_operation(
        db,
        source_kind="cataloging",
        source_id="project-b-cataloging",
        project_id=project_b.id,
        title="Project B cataloging",
    )
    ensure_operation(
        db,
        source_kind="assistant",
        source_id="project-a-assistant",
        project_id=project_a.id,
        title="Project A assistant",
    )
    db.commit()
    matching_id = matching.id
    project_a_id = project_a.id
    db.close()

    service = SqlAlchemyOperationService()
    with patch("app.modules.operations.infrastructure.service.SessionLocal", Session):
        items = service.list(
            active_only=False,
            limit=100,
            project_id=project_a_id,
            source_kind="cataloging",
        )

    assert [item["id"] for item in items] == [matching_id]


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


def test_operation_projects_legacy_status_and_exposes_attention_and_result():
    _engine, _Session, db = _db()
    operation = ensure_operation(
        db,
        source_kind="test",
        source_id="waiting",
        title="Waiting test",
        status="waiting_confirmation",
        message="内容已生成",
        attention={
            "kind": "confirmation",
            "title": "等待作者确认",
            "action_url": "/novel-creation?session=test",
        },
        result={
            "summary": "阶段内容已保存",
            "completed": ["生成阶段内容"],
            "incomplete": ["作者确认"],
        },
        outcome="waiting_user",
    )
    db.commit()

    payload = serialize_operation(operation, include_events=True)

    assert payload["status"] == "waiting_user"
    assert payload["outcome"] == "waiting_user"
    assert payload["attention"]["kind"] == "confirmation"
    assert payload["result_summary"] == "阶段内容已保存"
    assert payload["result"]["incomplete"] == ["作者确认"]
    assert payload["created_at"].endswith("+00:00")
    assert payload["events"][0]["created_at"].endswith("+00:00")


def test_waiting_user_operation_survives_restart_as_author_attention():
    _engine, _Session, db = _db()
    waiting = ensure_operation(
        db,
        source_kind="novel_creation",
        source_id="waiting-restart",
        title="等待确认",
        status="waiting_user",
        attention={"kind": "confirmation", "title": "请确认文风与世界观"},
        result={
            "summary": "阶段内容已保存",
            "completed": ["生成文风与世界观"],
            "incomplete": ["作者确认"],
        },
        outcome="waiting_user",
    )
    running = ensure_operation(
        db,
        source_kind="test",
        source_id="running-restart",
        title="运行中的任务",
        status="running",
    )
    db.commit()

    assert mark_interrupted_operations(db) == 1
    db.refresh(waiting)
    db.refresh(running)

    assert serialize_operation(waiting)["status"] == "waiting_user"
    assert serialize_operation(waiting)["attention"]["kind"] == "confirmation"
    assert serialize_operation(running)["status"] == "interrupted"


def test_completed_operation_without_reply_or_changes_is_not_generic_success():
    _engine, _Session, db = _db()
    operation = ensure_operation(
        db,
        source_kind="test",
        source_id="empty",
        title="Empty test",
        status="completed",
        message="调用结束",
    )
    db.commit()

    payload = serialize_operation(operation)

    assert payload["status"] == "completed"
    assert payload["outcome"] == "empty_response"


def test_heartbeat_and_process_samples_do_not_forge_semantic_activity():
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
    assert operation.last_activity_at == previous_activity

    operation.last_activity_at = datetime.utcnow() - timedelta(minutes=31)
    operation.heartbeat_at = datetime.utcnow()
    assert serialize_operation(operation)["health_status"] == "suspected_stall"


def test_stream_output_keeps_one_live_snapshot_without_growing_the_event_log():
    _engine, _Session, db = _db()
    operation = ensure_operation(
        db,
        source_kind="novel_creation",
        source_id="live-output",
        title="Live model output",
    )
    db.commit()
    initial_event_count = len(operation.events)

    record_operation_signal(
        operation.id,
        "stream_output",
        {
            "kind": "model_output",
            "output_chars": 1200,
            "output_preview": "first preview",
        },
        message="模型正在生成 · 已输出 1,200 字",
        db=db,
    )
    record_operation_signal(
        operation.id,
        "stream_output",
        {
            "kind": "model_output",
            "output_chars": 2400,
            "output_preview": "latest preview",
        },
        message="模型正在生成 · 已输出 2,400 字",
        db=db,
    )

    payload = serialize_operation(operation, include_events=True)
    assert len(operation.events) == initial_event_count
    assert payload["process_metrics"]["output_chars"] == 2400
    assert payload["process_metrics"]["output_preview"] == "latest preview"
    assert payload["last_output_at"] is not None


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
    assert payload["outcome"] == "completed_with_tools"
    assert payload["progress"]["current"] == 1
    assert payload["progress"]["total"] == 1
    assert any(event["event_type"] == "checkpoint" for event in payload["events"])


def test_external_agent_write_request_projects_waiting_attention_and_clears_it_after_confirmation():
    _engine, _Session, db = _db()
    project = Project(title="Agent project", description="test")
    db.add(project)
    db.commit()
    run = create_agent_run(db, project.id, title="Agent write")

    requested = request_write(db, run.id, "create_character", "创建角色林澈")
    db.expire_all()
    operation = db.query(OperationRun).filter(OperationRun.id == run.operation_id).one()
    waiting = serialize_operation(operation)

    assert requested["status"] == "ok"
    assert waiting["status"] == "waiting_user"
    assert waiting["outcome"] == "waiting_user"
    assert waiting["attention"]["action_label"] == "查看写入请求"

    confirmed = confirm_write(db, run.id, requested["request_id"])
    db.expire_all()
    operation = db.query(OperationRun).filter(OperationRun.id == run.operation_id).one()
    resumed = serialize_operation(operation)

    assert confirmed["status"] == "ok"
    assert resumed["status"] == "running"
    assert resumed["attention"] is None
    assert resumed["outcome"] is None


def test_agent_run_projection_releases_sqlite_writer_lock_before_cli_wait():
    with TemporaryDirectory() as temp_dir:
        database_path = os.path.join(temp_dir, "agent-worker.db")
        engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"timeout": 0.2, "check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        worker_db = Session()
        plan_db = Session()
        try:
            project = Project(title="Concurrent writer", description="before")
            worker_db.add(project)
            worker_db.commit()
            run = create_agent_run(worker_db, project.id, title="Local CLI")

            add_agent_event(worker_db, run.id, "cli_started", message="Started opencode")

            concurrent_project = plan_db.query(Project).filter(Project.id == project.id).one()
            concurrent_project.description = "plan step committed"
            plan_db.commit()

            update_agent_run_status(worker_db, run.id, "running", current_step="writing")
            concurrent_project.description = "plan wait committed"
            plan_db.commit()

            plan_db.refresh(concurrent_project)
            assert concurrent_project.description == "plan wait committed"
        finally:
            plan_db.close()
            worker_db.close()
            engine.dispose()


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
