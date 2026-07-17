"""Transactional content mirror outbox tests."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Chapter, ContentSyncJob, Project
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
