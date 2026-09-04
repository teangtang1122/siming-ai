"""Transactional content mirror outbox tests."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Chapter,
    ContentSyncJob,
    OutlineNode,
    Project,
    RagChunk,
    WorldbuildingEntry,
)
from app.database.session import Base
from app.modules.story.domain.content_sync import ContentSyncIntent, ContentSyncTarget
from app.modules.story.infrastructure.content_sync import (
    ContentSyncProcessor,
    enqueue_content_sync,
    ensure_chapter_mirror,
)


def _database(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'story.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _disable_auto_dispatch(session) -> None:
    session.info["siming_skip_content_sync_dispatch"] = True


def test_rolled_back_story_write_does_not_leave_sync_job(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMING_CONTENT_ROOT", str(tmp_path / "mirrors"))
    engine, Session = _database(tmp_path)
    try:
        with Session() as session:
            _disable_auto_dispatch(session)
            project = Project(title="Rollback Story")
            session.add(project)
            session.flush()
            enqueue_content_sync(
                session,
                ContentSyncIntent(
                    project_id=project.id,
                    target=ContentSyncTarget.PROJECT,
                ),
            )
            session.rollback()

        with Session() as session:
            assert session.query(Project).count() == 0
            assert session.query(ContentSyncJob).count() == 0
        assert not (tmp_path / "mirrors").exists()
    finally:
        engine.dispose()


def test_failed_mirror_does_not_rollback_chapter_and_can_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMING_CONTENT_ROOT", str(tmp_path / "mirrors"))
    engine, Session = _database(tmp_path)
    try:
        with Session() as session:
            _disable_auto_dispatch(session)
            project = Project(title="Durable Story")
            session.add(project)
            session.flush()
            chapter = Chapter(
                project_id=project.id,
                title="Chapter One",
                content="The database remains authoritative.",
            )
            session.add(chapter)
            session.flush()
            job = enqueue_content_sync(
                session,
                ContentSyncIntent(
                    project_id=project.id,
                    target=ContentSyncTarget.CHAPTER,
                    entity_id=chapter.id,
                ),
            )
            project_id, chapter_id, job_id = project.id, chapter.id, job.id
            session.commit()

        def fail_projection(_session, _job):
            raise OSError("simulated mirror failure")

        report = ContentSyncProcessor(Session, projection=fail_projection).process([job_id])
        assert report[0]["status"] == "failed"

        with Session() as session:
            stored = session.query(Chapter).filter(Chapter.id == chapter_id).one()
            failed_job = session.query(ContentSyncJob).filter(ContentSyncJob.id == job_id).one()
            assert stored.content == "The database remains authoritative."
            assert failed_job.status == "failed"
            assert "simulated mirror failure" in failed_job.last_error

        retry = ContentSyncProcessor(Session).process([job_id])
        assert retry[0]["status"] == "completed"
        with Session() as session:
            project = session.query(Project).filter(Project.id == project_id).one()
            chapter = session.query(Chapter).filter(Chapter.id == chapter_id).one()
            assert chapter.content_file_path
            assert (Path(project.folder_path) / chapter.content_file_path).is_file()
    finally:
        engine.dispose()


def test_file_delete_runs_only_after_database_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMING_CONTENT_ROOT", str(tmp_path / "mirrors"))
    engine, Session = _database(tmp_path)
    try:
        with Session() as session:
            _disable_auto_dispatch(session)
            project = Project(title="Versioned Story")
            session.add(project)
            session.flush()
            chapter = Chapter(project_id=project.id, title="Chapter One", content="Draft")
            session.add(chapter)
            session.flush()
            sync_job = enqueue_content_sync(
                session,
                ContentSyncIntent(
                    project_id=project.id,
                    target=ContentSyncTarget.CHAPTER,
                    entity_id=chapter.id,
                ),
            )
            chapter_id, sync_job_id = chapter.id, sync_job.id
            session.commit()

        ContentSyncProcessor(Session).process([sync_job_id])
        with Session() as session:
            _disable_auto_dispatch(session)
            chapter = session.query(Chapter).filter(Chapter.id == chapter_id).one()
            project = session.query(Project).filter(Project.id == chapter.project_id).one()
            mirror_path = Path(project.folder_path) / chapter.content_file_path
            assert mirror_path.is_file()
            delete_job = enqueue_content_sync(
                session,
                ContentSyncIntent(
                    project_id=project.id,
                    target=ContentSyncTarget.FILE_DELETE,
                    entity_id=chapter.id,
                    payload={
                        "folder_path": project.folder_path,
                        "relative_path": chapter.content_file_path,
                    },
                ),
            )
            delete_job_id = delete_job.id
            session.delete(chapter)
            assert mirror_path.is_file()
            session.commit()
            assert mirror_path.is_file()

        ContentSyncProcessor(Session).process([delete_job_id])
        assert not mirror_path.exists()
        with Session() as session:
            assert session.query(Chapter).filter(Chapter.id == chapter_id).first() is None
    finally:
        engine.dispose()


def test_ensuring_a_mirror_does_not_commit_the_callers_pending_changes(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("SIMING_CONTENT_ROOT", str(tmp_path / "mirrors"))
    engine, Session = _database(tmp_path)
    try:
        with Session() as session:
            project = Project(title="Committed Story")
            session.add(project)
            session.flush()
            chapter = Chapter(project_id=project.id, title="Chapter One", content="Draft")
            session.add(chapter)
            session.commit()
            project_id, chapter_id = project.id, chapter.id

        with Session() as session:
            project = session.get(Project, project_id)
            chapter = session.get(Chapter, chapter_id)
            session.add(Project(title="Must Roll Back"))
            _folder, mirror_path = ensure_chapter_mirror(session, project, chapter)
            assert mirror_path.is_file()
            session.rollback()

        with Session() as session:
            titles = {row.title for row in session.query(Project).all()}
            assert titles == {"Committed Story"}
    finally:
        engine.dispose()


def test_project_sync_refreshes_stale_worldbuilding_discovery_index(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMING_CONTENT_ROOT", str(tmp_path / "mirrors"))
    engine, Session = _database(tmp_path)
    try:
        with Session() as session:
            _disable_auto_dispatch(session)
            project = Project(title="Indexed Story")
            session.add(project)
            session.flush()
            entry = WorldbuildingEntry(
                project_id=project.id,
                dimension="culture",
                title="通信签收表",
                content="旧索引错误：18:38值班员口头报时。",
                status="active",
            )
            session.add(entry)
            session.flush()
            first = enqueue_content_sync(
                session,
                ContentSyncIntent(
                    project_id=project.id,
                    target=ContentSyncTarget.PROJECT,
                    source="cataloging_workspace_tool",
                ),
            )
            project_id, entry_id, first_id = project.id, entry.id, first.id
            session.commit()

        first_report = ContentSyncProcessor(Session).process([first_id])
        assert first_report[0]["status"] == "completed"
        with Session() as session:
            indexed = session.query(RagChunk).filter(
                RagChunk.project_id == project_id,
                RagChunk.source_type == "worldbuilding",
                RagChunk.source_id == entry_id,
            ).all()
            assert indexed
            assert "18:38值班员口头报时" in "".join(row.content for row in indexed)

        with Session() as session:
            _disable_auto_dispatch(session)
            entry = session.query(WorldbuildingEntry).filter_by(id=entry_id).one()
            entry.content = "当前权威内容：呼叫栏18:50；旧结论已经撤回。"
            second = enqueue_content_sync(
                session,
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.PROJECT,
                    source="cataloging_workspace_tool",
                ),
            )
            second_id = second.id
            session.commit()

        second_report = ContentSyncProcessor(Session).process([second_id])
        assert second_report[0]["status"] == "completed"
        with Session() as session:
            refreshed = session.query(RagChunk).filter(
                RagChunk.project_id == project_id,
                RagChunk.source_type == "worldbuilding",
                RagChunk.source_id == entry_id,
            ).all()
            assert refreshed
            content = "".join(row.content for row in refreshed)
            assert "呼叫栏18:50" in content
            assert "18:38值班员口头报时" not in content
    finally:
        engine.dispose()


def test_retired_worldbuilding_is_removed_from_local_agent_mirror(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMING_CONTENT_ROOT", str(tmp_path / "mirrors"))
    engine, Session = _database(tmp_path)
    try:
        with Session() as session:
            _disable_auto_dispatch(session)
            project = Project(title="Current Context Only")
            session.add(project)
            session.flush()
            entry = WorldbuildingEntry(
                project_id=project.id,
                dimension="history",
                title="旧版错误登记体系",
                content="不得进入后续本机 Agent 上下文。",
                status="active",
            )
            session.add(entry)
            session.flush()
            first = enqueue_content_sync(
                session,
                ContentSyncIntent(
                    project_id=project.id,
                    target=ContentSyncTarget.WORLD_BUILDING,
                    entity_id=entry.id,
                ),
            )
            project_id, entry_id, first_id = project.id, entry.id, first.id
            session.commit()

        assert ContentSyncProcessor(Session).process([first_id])[0]["status"] == "completed"
        with Session() as session:
            project = session.get(Project, project_id)
            entry = session.get(WorldbuildingEntry, entry_id)
            mirror_path = Path(project.folder_path) / entry.content_file_path
            assert mirror_path.is_file()

            _disable_auto_dispatch(session)
            entry.status = "superseded"
            second = enqueue_content_sync(
                session,
                ContentSyncIntent(
                    project_id=project.id,
                    target=ContentSyncTarget.WORLD_BUILDING,
                    entity_id=entry.id,
                ),
            )
            second_id = second.id
            session.commit()

        assert ContentSyncProcessor(Session).process([second_id])[0]["status"] == "completed"
        assert not mirror_path.exists()
        with Session() as session:
            entry = session.get(WorldbuildingEntry, entry_id)
            assert entry.status == "superseded"
            assert entry.content_file_path is None
            assert entry.content_hash is None
            assert session.query(RagChunk).filter_by(
                project_id=project_id,
                source_type="worldbuilding",
                source_id=entry_id,
            ).count() == 0
    finally:
        engine.dispose()


def test_full_project_mirror_excludes_worldbuilding_already_retired(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMING_CONTENT_ROOT", str(tmp_path / "mirrors"))
    engine, Session = _database(tmp_path)
    try:
        with Session() as session:
            _disable_auto_dispatch(session)
            project = Project(title="Historical Rows Stay In Database")
            session.add(project)
            session.flush()
            current = WorldbuildingEntry(
                project_id=project.id,
                dimension="history",
                title="当前权威流程",
                content="可供后续 Agent 使用。",
                status="active",
            )
            retired = WorldbuildingEntry(
                project_id=project.id,
                dimension="history",
                title="已撤回旧流程",
                content="只供数据库审计与项目导出。",
                status="superseded",
            )
            session.add_all([current, retired])
            session.flush()
            job = enqueue_content_sync(
                session,
                ContentSyncIntent(
                    project_id=project.id,
                    target=ContentSyncTarget.PROJECT,
                ),
            )
            project_id = project.id
            current_id, retired_id, job_id = current.id, retired.id, job.id
            session.commit()

        assert ContentSyncProcessor(Session).process([job_id])[0]["status"] == "completed"
        with Session() as session:
            project = session.get(Project, project_id)
            current = session.get(WorldbuildingEntry, current_id)
            retired = session.get(WorldbuildingEntry, retired_id)
            assert current.content_file_path
            assert (Path(project.folder_path) / current.content_file_path).is_file()
            assert retired.content_file_path is None
            mirror_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (Path(project.folder_path) / "worldbuilding").rglob("*.json")
            )
            assert current_id in mirror_text
            assert retired_id not in mirror_text
            assert "已撤回旧流程" not in mirror_text
    finally:
        engine.dispose()


def test_outline_sync_replaces_stale_summary_index(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMING_CONTENT_ROOT", str(tmp_path / "mirrors"))
    engine, Session = _database(tmp_path)
    try:
        with Session() as session:
            _disable_auto_dispatch(session)
            project = Project(title="Outline Index Story")
            session.add(project)
            session.flush()
            node = OutlineNode(
                project_id=project.id,
                node_type="chapter",
                title="谁拟谁核",
                planned_summary="计划摘要",
                actual_summary="test",
            )
            session.add(node)
            session.flush()
            first = enqueue_content_sync(
                session,
                ContentSyncIntent(
                    project_id=project.id,
                    target=ContentSyncTarget.OUTLINE,
                ),
            )
            project_id, node_id, first_id = project.id, node.id, first.id
            session.commit()

        first_report = ContentSyncProcessor(Session).process([first_id])
        assert first_report[0]["status"] == "completed"
        with Session() as session:
            indexed = session.query(RagChunk).filter(
                RagChunk.project_id == project_id,
                RagChunk.source_type == "outline",
                RagChunk.source_id == node_id,
            ).all()
            assert indexed
            assert "test" in "".join(row.content for row in indexed)

        with Session() as session:
            _disable_auto_dispatch(session)
            node = session.query(OutlineNode).filter_by(id=node_id).one()
            node.actual_summary = "作者审定摘要：旧占位内容已经撤回。"
            second = enqueue_content_sync(
                session,
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.OUTLINE,
                ),
            )
            second_id = second.id
            session.commit()

        second_report = ContentSyncProcessor(Session).process([second_id])
        assert second_report[0]["status"] == "completed"
        with Session() as session:
            refreshed = session.query(RagChunk).filter(
                RagChunk.project_id == project_id,
                RagChunk.source_type == "outline",
                RagChunk.source_id == node_id,
            ).all()
            assert refreshed
            content = "".join(row.content for row in refreshed)
            assert "作者审定摘要" in content
            assert "\ntest\n" not in f"\n{content}\n"
    finally:
        engine.dispose()
