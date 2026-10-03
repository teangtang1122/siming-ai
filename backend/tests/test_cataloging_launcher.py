"""Regression tests for author-triggered canonical cataloging."""
from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base,
    CatalogingChapterRun,
    CatalogingFact,
    CatalogingJob,
    Chapter,
    OperationRun,
    Project,
)
from app.services.cataloging.launcher import (
    CHAPTER_SAVE_SOURCE,
    create_and_queue_cataloging_job,
    find_blocking_chapter_cataloging_job,
    mark_cataloging_worker_failure,
    mark_interrupted_cataloging_jobs,
)
from app.services.cataloging.job_control import complete_cataloging_job, refresh_job_progress


def _database():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _chapter(db):
    db.add_all([
        Project(id="project-1", title="Test Novel"),
        Chapter(
            id="chapter-1",
            project_id="project-1",
            title="第一章",
            content="正文",
            word_count=2,
            cataloging_required=True,
        ),
    ])
    db.commit()


def test_author_trigger_creates_canonical_job_without_implicit_worker_for_external_backend():
    engine, db = _database()
    try:
        _chapter(db)
        job, launch = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source=CHAPTER_SAVE_SOURCE,
            run_now=True,
        )

        assert job.execution_backend == "external_agent"
        assert job.model_source == "chapter_save:external_agent"
        assert launch["worker_queued"] is False
        assert db.query(CatalogingChapterRun).filter_by(job_id=job.id).one().chapter_id == "chapter-1"
        operation = db.get(OperationRun, job.operation_id)
        assert operation.title == "《第一章》章节建档"
        assert operation.tool_mode == "chapter_save:external_agent"
        assert "作者已启动建档" in operation.current_message
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_new_author_trigger_supersedes_only_same_chapter_job():
    engine, db = _database()
    try:
        _chapter(db)
        first, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source=CHAPTER_SAVE_SOURCE,
            run_now=False,
        )
        chapter = db.get(Chapter, "chapter-1")
        chapter.current_version = 2
        db.commit()
        second, launch = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source=CHAPTER_SAVE_SOURCE,
            run_now=False,
        )

        db.refresh(first)
        assert first.status == "cancelled"
        assert second.status == "queued"
        assert launch["superseded_job_ids"] == [first.id]
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_successful_retry_closes_older_failed_job_and_task_center_pause():
    engine, db = _database()
    try:
        _chapter(db)
        old_job, _ = create_and_queue_cataloging_job(
            db, "project-1", ["chapter-1"],
            backend_override="external_agent", trigger_source="manual", run_now=False,
        )
        old_run = db.query(CatalogingChapterRun).filter_by(job_id=old_job.id).one()
        old_run.status = "failed"
        old_job.status = "paused_on_failure"
        refresh_job_progress(db, old_job)
        db.commit()

        retry_job, _ = create_and_queue_cataloging_job(
            db, "project-1", ["chapter-1"],
            backend_override="external_agent", trigger_source="manual", run_now=False,
        )
        retry_run = db.query(CatalogingChapterRun).filter_by(job_id=retry_job.id).one()
        retry_run.status = "completed"
        complete_cataloging_job(db, retry_job)
        db.commit()

        assert old_job.status == "cancelled"
        assert db.get(OperationRun, old_job.operation_id).status == "cancelled"
        assert retry_job.status == "completed"
        assert db.get(Chapter, "chapter-1").cataloging_required is False
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_successful_retry_keeps_old_job_when_another_chapter_is_unresolved():
    engine, db = _database()
    try:
        _chapter(db)
        db.add(Chapter(
            id="chapter-2", project_id="project-1", title="第二章",
            content="正文", word_count=2, cataloging_required=True,
        ))
        db.commit()
        old_job, _ = create_and_queue_cataloging_job(
            db, "project-1", ["chapter-1", "chapter-2"],
            backend_override="external_agent", trigger_source="manual", run_now=False,
        )
        old_run = db.query(CatalogingChapterRun).filter_by(
            job_id=old_job.id, chapter_id="chapter-1",
        ).one()
        old_run.status = "failed"
        old_job.status = "paused_on_failure"
        refresh_job_progress(db, old_job)
        db.commit()

        retry_job, _ = create_and_queue_cataloging_job(
            db, "project-1", ["chapter-1"],
            backend_override="external_agent", trigger_source="manual", run_now=False,
        )
        retry_run = db.query(CatalogingChapterRun).filter_by(job_id=retry_job.id).one()
        retry_run.status = "completed"
        complete_cataloging_job(db, retry_job)
        db.commit()

        assert old_job.status == "paused_on_failure"
        assert db.get(OperationRun, old_job.operation_id).status == "paused"
        assert db.get(Chapter, "chapter-2").cataloging_required is True
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_repeated_start_reuses_active_same_chapter_version():
    engine, db = _database()
    try:
        _chapter(db)
        first, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source="manual",
            run_now=False,
        )

        second, launch = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source="manual",
            run_now=False,
        )

        assert second.id == first.id
        assert launch["idempotent_reuse"] is True
        assert launch["in_progress_chapter_ids"] == ["chapter-1"]
        assert launch["queued_chapter_ids"] == []
        assert db.query(CatalogingJob).count() == 1
        assert db.query(CatalogingChapterRun).count() == 1
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_http_resume_requeues_a_paused_managed_cataloging_job():
    engine, db = _database()
    try:
        _chapter(db)
        job, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="local_cli_agent",
            provider_override="opencode_cli",
            model_override="opencode_cli:opencode/big-pickle",
            trigger_source="manual",
            run_now=False,
        )
        job.status = "paused"
        db.commit()

        from app.routers.cataloging import resume_cataloging_job

        with patch("app.routers.cataloging.queue_managed_cataloging_job", return_value=True) as queued:
            response = asyncio.run(
                resume_cataloging_job("project-1", job.id, db)
            )

        db.refresh(job)
        assert response.code == 0
        assert job.status == "running"
        queued.assert_called_once_with(job)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_http_retry_requeues_managed_job_and_reports_receipt():
    from app.routers.cataloging import retry_current_cataloging_chapter

    engine, db = _database()
    try:
        _chapter(db)
        job, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="local_cli_agent",
            provider_override="opencode_cli",
            model_override="opencode_cli:opencode/big-pickle",
            trigger_source="manual",
            run_now=False,
        )
        run = db.query(CatalogingChapterRun).filter_by(job_id=job.id).one()
        run.status = "failed"
        run.error = "candidate failure"
        job.status = "paused_on_failure"
        job.blocked_chapter_id = run.chapter_id
        db.commit()

        with patch(
            "app.routers.cataloging.queue_managed_cataloging_job",
            return_value=True,
        ) as queued:
            response = asyncio.run(
                retry_current_cataloging_chapter("project-1", job.id, db)
            )

        db.refresh(run)
        assert response.code == 0
        assert response.data["worker_queued"] is True
        assert response.message == "当前章节已重置并开始重试"
        assert run.status == "pending"
        queued.assert_called_once_with(job)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_http_retry_schedules_worker_from_the_route_event_loop():
    from app.routers.cataloging import retry_current_cataloging_chapter

    engine, db = _database()
    try:
        _chapter(db)
        job, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="local_cli_agent",
            provider_override="opencode_cli",
            model_override="opencode_cli:opencode/big-pickle",
            trigger_source="manual",
            run_now=False,
        )
        run = db.query(CatalogingChapterRun).filter_by(job_id=job.id).one()
        run.status = "failed"
        job.status = "paused_on_failure"
        job.blocked_chapter_id = run.chapter_id
        db.commit()

        async def exercise():
            with patch(
                "app.services.cataloging.launcher.run_cataloging_job",
                new_callable=AsyncMock,
            ) as worker:
                response = await retry_current_cataloging_chapter(
                    "project-1", job.id, db
                )
                await asyncio.sleep(0)
                assert response.data["worker_queued"] is True
                worker.assert_awaited_once_with(job.id)

        asyncio.run(exercise())
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_http_full_retry_accepts_paused_extracting_unit():
    from app.routers.cataloging import retry_current_cataloging_chapter

    engine, db = _database()
    try:
        _chapter(db)
        job, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="local_cli_agent",
            provider_override="opencode_cli",
            model_override="opencode_cli:opencode/big-pickle",
            trigger_source="manual",
            run_now=False,
        )
        run = db.query(CatalogingChapterRun).filter_by(job_id=job.id).one()
        db.add(CatalogingFact(
            job_id=job.id,
            chapter_run_id=run.id,
            project_id="project-1",
            chapter_id="chapter-1",
            fact_type="chapter_overview",
            raw_payload=json.dumps({"summary": "discard me", "scenes": []}),
            status="active",
        ))
        run.status = "extracting"
        job.status = "paused"
        db.commit()

        with patch(
            "app.routers.cataloging.queue_managed_cataloging_job",
            return_value=True,
        ) as queued:
            response = asyncio.run(
                retry_current_cataloging_chapter("project-1", job.id, db)
            )

        db.refresh(run)
        assert response.code == 0
        assert response.data["worker_queued"] is True
        assert run.status == "pending"
        assert db.query(CatalogingFact).filter_by(chapter_run_id=run.id).count() == 0
        queued.assert_called_once_with(job)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_http_plan_repair_preserves_facts_and_requeues_managed_job():
    from app.routers.cataloging import repair_current_cataloging_plan

    engine, db = _database()
    try:
        _chapter(db)
        job, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="local_cli_agent",
            provider_override="opencode_cli",
            model_override="opencode_cli:opencode/big-pickle",
            trigger_source="manual",
            run_now=False,
        )
        run = db.query(CatalogingChapterRun).filter_by(job_id=job.id).one()
        fact = CatalogingFact(
            job_id=job.id,
            chapter_run_id=run.id,
            project_id="project-1",
            chapter_id="chapter-1",
            fact_type="chapter_overview",
            raw_payload=json.dumps({"summary": "kept", "scenes": []}),
            status="active",
        )
        db.add(fact)
        run.status = "failed"
        run.error = "candidate failure"
        job.status = "paused_on_failure"
        job.blocked_chapter_id = run.chapter_id
        db.commit()

        with patch(
            "app.routers.cataloging.queue_managed_cataloging_job",
            return_value=True,
        ) as queued:
            response = asyncio.run(
                repair_current_cataloging_plan("project-1", job.id, db)
            )

        db.refresh(run)
        assert response.code == 0
        assert response.data["worker_queued"] is True
        assert response.message == "已保留候选并开始修正本章计划"
        assert run.status == "extracting"
        assert db.query(CatalogingFact).filter_by(id=fact.id).count() == 1
        queued.assert_called_once_with(job)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_http_plan_repair_can_repair_a_completed_projection():
    from app.routers.cataloging import repair_current_cataloging_plan

    engine, db = _database()
    try:
        _chapter(db)
        job, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="local_cli_agent",
            provider_override="opencode_cli",
            model_override="opencode_cli:opencode/big-pickle",
            trigger_source="manual",
            run_now=False,
        )
        run = db.query(CatalogingChapterRun).filter_by(job_id=job.id).one()
        fact = CatalogingFact(
            job_id=job.id,
            chapter_run_id=run.id,
            project_id="project-1",
            chapter_id="chapter-1",
            fact_type="chapter_overview",
            raw_payload=json.dumps({"summary": "kept", "scenes": []}),
            status="active",
        )
        derived_fact = CatalogingFact(
            job_id=job.id,
            chapter_run_id=run.id,
            project_id="project-1",
            chapter_id="chapter-1",
            fact_type="section_scene_state",
            raw_payload=json.dumps({"scene_number": 7, "title": "旧错误场景"}),
            status="active",
        )
        db.add_all([fact, derived_fact])
        run.status = "completed"
        run.completed_at = datetime.utcnow()
        job.status = "completed"
        job.completed_chapters = 1
        job.completed_at = datetime.utcnow()
        db.commit()
        derived_fact_id = derived_fact.id

        with patch(
            "app.routers.cataloging.queue_managed_cataloging_job",
            return_value=True,
        ) as queued:
            response = asyncio.run(
                repair_current_cataloging_plan("project-1", job.id, db)
            )

        db.refresh(run)
        db.refresh(job)
        assert response.code == 0
        assert response.data["worker_queued"] is True
        assert run.status == "extracting"
        assert run.completed_at is None
        assert job.status == "running"
        assert job.completed_chapters == 0
        assert job.completed_at is None
        assert db.query(CatalogingFact).filter_by(id=fact.id).count() == 1
        assert db.query(CatalogingFact).filter_by(id=derived_fact_id).count() == 1
        queued.assert_called_once_with(job)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()




def test_repeated_start_reuses_completed_same_chapter_version():
    engine, db = _database()
    try:
        _chapter(db)
        first, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source="manual",
            run_now=False,
        )
        run = db.query(CatalogingChapterRun).filter_by(job_id=first.id).one()
        run.status = "completed"
        first.status = "completed"
        db.commit()

        second, launch = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source="manual",
            run_now=False,
        )

        assert second.id == first.id
        assert launch["idempotent_reuse"] is True
        assert launch["already_cataloged_chapter_ids"] == ["chapter-1"]
        assert launch["next_action"] == "already_cataloged"
        assert db.query(CatalogingJob).count() == 1
        assert db.query(CatalogingChapterRun).count() == 1
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_concurrent_same_version_starts_create_one_job(tmp_path):
    database_path = tmp_path / "cataloging-launch.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)
    with SessionFactory() as db:
        _chapter(db)

    def launch():
        with SessionFactory() as db:
            job, receipt = create_and_queue_cataloging_job(
                db,
                "project-1",
                ["chapter-1"],
                backend_override="external_agent",
                trigger_source="manual",
                run_now=False,
            )
            return job.id, receipt["idempotent_reuse"]

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: launch(), range(2)))

        with SessionFactory() as db:
            assert len({job_id for job_id, _reused in results}) == 1
            assert sorted(reused for _job_id, reused in results) == [False, True]
            assert db.query(CatalogingJob).count() == 1
            assert db.query(CatalogingChapterRun).count() == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_http_workspace_and_external_cli_share_same_version_receipt():
    from app.routers.cataloging import start_cataloging
    from app.schemas.cataloging import CatalogingStartRequest
    from app.services.workspace.tools.cataloging import start_cataloging_job
    from app.services.workspace.tools.external_cataloging import (
        start_external_cataloging_job,
    )

    engine, db = _database()
    try:
        _chapter(db)
        external = asyncio.run(start_external_cataloging_job(
            db,
            "project-1",
            {"chapter_ids": ["chapter-1"]},
        ))
        workspace = asyncio.run(start_cataloging_job(
            db,
            "project-1",
            {"chapter_ids": ["chapter-1"], "run_now": False},
        ))
        http = asyncio.run(start_cataloging(
            "project-1",
            CatalogingStartRequest(chapter_ids=["chapter-1"]),
            db,
        ))

        job_id = external["data"]["job_id"]
        assert external["data"]["idempotent_reuse"] is False
        assert workspace["data"]["id"] == job_id
        assert workspace["data"]["idempotent_reuse"] is True
        assert http.data["id"] == job_id
        assert http.data["idempotent_reuse"] is True
        assert http.message == "当前章节版本正在建档，已复用现有任务"
        assert db.query(CatalogingJob).count() == 1
        assert db.query(CatalogingChapterRun).count() == 1
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_chapter_cataloging_endpoint_accepts_completed_same_version_reuse():
    from app.routers.chapters import start_chapter_cataloging

    engine, db = _database()
    try:
        _chapter(db)
        chapter = db.get(Chapter, "chapter-1")
        assert chapter is not None
        first, _launch = create_and_queue_cataloging_job(
            db,
            "project-1",
            [chapter.id],
            backend_override="external_agent",
            trigger_source=CHAPTER_SAVE_SOURCE,
            run_now=False,
        )
        run = db.query(CatalogingChapterRun).filter_by(job_id=first.id).one()
        run.status = "completed"
        first.status = "completed"
        db.commit()

        class Workspace:
            @staticmethod
            def detail(project_id: str, chapter_id: str) -> dict:
                assert project_id == "project-1"
                assert chapter_id == chapter.id
                return {"id": chapter.id, "word_count": 20}

        response = asyncio.run(start_chapter_cataloging(
            "project-1",
            chapter.id,
            None,
            Workspace(),
            db,
        ))

        assert response.message == "当前章节版本已完成建档，已复用现有结果"
        assert response.data["cataloging_job"]["id"] == first.id
        assert response.data["cataloging_job"]["idempotent_reuse"] is True
        assert db.query(CatalogingJob).count() == 1
        assert db.query(CatalogingChapterRun).count() == 1
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_nonterminal_author_job_blocks_other_chapter_but_allows_same_chapter_rewrite():
    engine, db = _database()
    try:
        _chapter(db)
        job, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source=CHAPTER_SAVE_SOURCE,
            run_now=False,
        )

        assert find_blocking_chapter_cataloging_job(db, "project-1").id == job.id
        assert find_blocking_chapter_cataloging_job(
            db,
            "project-1",
            allow_chapter_id="chapter-1",
        ) is None
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_worker_failure_is_persisted_as_retryable_pause_with_operation_attention():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)
    db = SessionFactory()
    try:
        _chapter(db)
        job, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source=CHAPTER_SAVE_SOURCE,
            run_now=False,
        )
        job_id = job.id
        operation_id = job.operation_id
        db.close()

        with patch("app.services.cataloging.launcher.SessionLocal", SessionFactory):
            assert mark_cataloging_worker_failure(
                job_id,
                "database is locked",
                failure_class="DatabaseWriteLockTimeout",
            )

        db = SessionFactory()
        failed = db.get(CatalogingJob, job_id)
        operation = db.get(OperationRun, operation_id)
        run = db.query(CatalogingChapterRun).filter_by(job_id=job_id).one()
        assert failed.status == "paused_on_failure"
        assert run.status == "failed"
        assert operation.status == "paused"
        assert operation.failure_class == "DatabaseWriteLockTimeout"
        assert operation.attention_json["blocking"] is True
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_startup_recovery_marks_orphaned_internal_job_interrupted():
    engine, db = _database()
    try:
        _chapter(db)
        job, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source=CHAPTER_SAVE_SOURCE,
            run_now=False,
        )
        job.execution_backend = "internal_llm"
        db.commit()

        assert mark_interrupted_cataloging_jobs(db) == 1
        db.commit()
        db.refresh(job)
        operation = db.get(OperationRun, job.operation_id)
        assert job.status == "paused_on_failure"
        assert operation.failure_class == "interrupted"
        assert operation.status == "paused"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
