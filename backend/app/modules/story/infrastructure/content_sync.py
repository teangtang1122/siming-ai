"""Transactional outbox and post-commit content mirror projection runner."""
from __future__ import annotations

import logging
import shutil
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import event, or_
from sqlalchemy.orm import Session, sessionmaker

from ....architecture.uow import SqlAlchemyUnitOfWork
from ....database.session import SessionLocal
from ...story.domain.content_sync import ContentSyncIntent, ContentSyncTarget
from ..application.content_sync import ContentSyncRuntime
from ..application.ports import ContentSyncOutbox
from .models import ContentSyncJob

logger = logging.getLogger(__name__)
_SESSION_JOB_IDS = "siming_content_sync_job_ids"
_SKIP_AUTO_DISPATCH = "siming_skip_content_sync_dispatch"
_events_configured = False


def _commit(session: Session) -> None:
    with SqlAlchemyUnitOfWork.from_session(session) as uow:
        uow.commit()


def _remember_job(session: Session, job_id: str) -> None:
    job_ids = session.info.setdefault(_SESSION_JOB_IDS, [])
    if job_id not in job_ids:
        job_ids.append(job_id)


def enqueue_content_sync(session: Session, intent: ContentSyncIntent) -> ContentSyncJob:
    """Add or coalesce a mirror projection in the caller's transaction."""

    open_statuses = ("pending", "failed")
    job = (
        session.query(ContentSyncJob)
        .filter(
            ContentSyncJob.dedupe_key == intent.dedupe_key,
            ContentSyncJob.status.in_(open_statuses),
        )
        .order_by(ContentSyncJob.created_at.desc())
        .first()
    )
    if job is None:
        job = ContentSyncJob(
            project_id=intent.project_id,
            target=intent.target.value,
            entity_id=intent.entity_id,
            payload_json=dict(intent.payload),
            source=intent.source,
            dedupe_key=intent.dedupe_key,
            status="pending",
        )
        session.add(job)
        session.flush()
    else:
        job.entity_id = intent.entity_id
        job.payload_json = dict(intent.payload)
        job.source = intent.source
        job.status = "pending"
        job.attempt_count = 0
        job.last_error = None
        job.next_attempt_at = None
        session.flush()
    _remember_job(session, job.id)
    return job


def enqueue_many_content_sync(
    session: Session,
    intents: Iterable[ContentSyncIntent],
) -> list[ContentSyncJob]:
    return [enqueue_content_sync(session, intent) for intent in intents]


class SqlAlchemyContentSyncOutbox(ContentSyncOutbox):
    """SQLAlchemy adapter that joins mirror jobs to the active transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, intent: ContentSyncIntent) -> str:
        return enqueue_content_sync(self._session, intent).id


class SqlAlchemyContentSyncRuntime(ContentSyncRuntime):
    """Application port adapter for the SQLAlchemy outbox and file projector."""

    def enqueue(self, session: Session, intent: ContentSyncIntent) -> ContentSyncJob:
        return enqueue_content_sync(session, intent)

    def enqueue_project(
        self,
        session: Session,
        project_id: str,
        *,
        source: str,
    ) -> ContentSyncJob:
        return enqueue_project_sync(session, project_id, source=source)

    def ensure_chapter(
        self,
        session: Session,
        project: Any,
        chapter: Any,
        *,
        index: int = 0,
        source: str = "mirror_read",
    ) -> tuple[Path, Path]:
        return ensure_chapter_mirror(
            session,
            project,
            chapter,
            index=index,
            source=source,
        )

    def health(self, session: Session, project_id: str) -> dict[str, Any]:
        return content_sync_health(session, project_id)


def enqueue_project_sync(
    session: Session,
    project_id: str,
    *,
    source: str,
) -> ContentSyncJob:
    """Queue a complete project mirror rebuild."""

    return enqueue_content_sync(
        session,
        ContentSyncIntent(
            project_id=project_id,
            target=ContentSyncTarget.PROJECT,
            source=source,
        ),
    )


def _delete_file(payload: dict[str, Any], project_id: str) -> None:
    folder_value = str(payload.get("folder_path") or "").strip()
    relative_value = str(payload.get("relative_path") or "").strip()
    if not folder_value or not relative_value:
        return
    folder = Path(folder_value).expanduser().resolve()
    path = (folder / relative_value).resolve()
    try:
        path.relative_to(folder)
    except ValueError:
        raise ValueError("Mirror delete path escaped the project folder") from None
    if path.exists() and path.is_file():
        path.unlink()
    from ....services.hot_cache import invalidate_project

    invalidate_project(project_id)


def _delete_project(payload: dict[str, Any], project_id: str) -> None:
    folder_value = str(payload.get("folder_path") or "").strip()
    if not folder_value:
        return
    folder = Path(folder_value).expanduser().resolve()
    if folder.exists() and folder.is_dir():
        shutil.rmtree(folder)
    from ....services.hot_cache import invalidate_project

    invalidate_project(project_id)


def _apply_projection(session: Session, job: ContentSyncJob) -> str:
    from ....database.models import Chapter, Character, Project, WorldbuildingEntry
    from ....services.content_store import (
        sync_chapter_to_file,
        sync_character_to_file,
        sync_outline_to_file,
        sync_project_to_files,
        sync_relationships_to_file,
        sync_worldbuilding_relations_to_file,
        sync_worldbuilding_to_file,
        write_project_manifest,
    )

    payload = dict(job.payload_json or {})
    target = ContentSyncTarget(job.target)
    if target is ContentSyncTarget.FILE_DELETE:
        _delete_file(payload, job.project_id)
        return "deleted"
    if target is ContentSyncTarget.PROJECT_DELETE:
        _delete_project(payload, job.project_id)
        return "deleted"

    project = session.query(Project).filter(Project.id == job.project_id).first()
    if project is None:
        return "target_missing"
    if target is ContentSyncTarget.PROJECT:
        sync_project_to_files(session, project.id)
    elif target is ContentSyncTarget.PROJECT_MANIFEST:
        write_project_manifest(session, project)
    elif target is ContentSyncTarget.CHAPTER:
        chapter = session.query(Chapter).filter(
            Chapter.id == job.entity_id,
            Chapter.project_id == project.id,
        ).first()
        if chapter is None:
            return "target_missing"
        sync_chapter_to_file(
            session,
            project,
            chapter,
            index=int(payload.get("index") or 0),
        )
    elif target is ContentSyncTarget.CHARACTER:
        character = session.query(Character).filter(
            Character.id == job.entity_id,
            Character.project_id == project.id,
        ).first()
        if character is None:
            return "target_missing"
        sync_character_to_file(session, project, character)
    elif target is ContentSyncTarget.WORLD_BUILDING:
        entry = session.query(WorldbuildingEntry).filter(
            WorldbuildingEntry.id == job.entity_id,
            WorldbuildingEntry.project_id == project.id,
        ).first()
        if entry is None:
            return "target_missing"
        sync_worldbuilding_to_file(session, project, entry)
    elif target is ContentSyncTarget.OUTLINE:
        sync_outline_to_file(session, project)
    elif target is ContentSyncTarget.CHARACTER_RELATIONSHIPS:
        sync_relationships_to_file(session, project)
    elif target is ContentSyncTarget.WORLD_BUILDING_RELATIONSHIPS:
        sync_worldbuilding_relations_to_file(session, project)
    return "applied"


class ContentSyncProcessor:
    """Claim and execute committed mirror jobs in independent transactions."""

    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
        *,
        projection: Callable[[Session, ContentSyncJob], str] = _apply_projection,
    ) -> None:
        self._session_factory = session_factory
        self._projection = projection

    def process(self, job_ids: Iterable[str]) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        for job_id in dict.fromkeys(job_ids):
            report = self._process_one(job_id)
            if report is not None:
                reports.append(report)
        return reports

    def process_pending(self, *, limit: int = 100) -> list[dict[str, Any]]:
        now = datetime.utcnow()
        with self._session_factory() as session:
            session.info[_SKIP_AUTO_DISPATCH] = True
            job_ids = [
                row.id
                for row in session.query(ContentSyncJob)
                .filter(
                    ContentSyncJob.attempt_count < ContentSyncJob.max_attempts,
                    or_(
                        ContentSyncJob.status == "pending",
                        (
                            (ContentSyncJob.status == "failed")
                            & or_(
                                ContentSyncJob.next_attempt_at.is_(None),
                                ContentSyncJob.next_attempt_at <= now,
                            )
                        ),
                    ),
                )
                .order_by(ContentSyncJob.created_at.asc())
                .limit(limit)
                .all()
            ]
        return self.process(job_ids)

    def recover_interrupted(self) -> int:
        with self._session_factory() as session:
            session.info[_SKIP_AUTO_DISPATCH] = True
            jobs = session.query(ContentSyncJob).filter(
                ContentSyncJob.status == "running"
            ).all()
            for job in jobs:
                job.status = "failed"
                job.last_error = "Application stopped while mirror synchronization was running."
                job.next_attempt_at = datetime.utcnow()
            if jobs:
                _commit(session)
            return len(jobs)

    def _process_one(self, job_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            session.info[_SKIP_AUTO_DISPATCH] = True
            job = session.query(ContentSyncJob).filter(ContentSyncJob.id == job_id).first()
            if job is None or job.status in {"completed", "cancelled"}:
                return None
            if (job.attempt_count or 0) >= (job.max_attempts or 5):
                return {
                    "job_id": job.id,
                    "status": job.status,
                    "detail": "retry_limit_reached",
                }

            job.status = "running"
            job.attempt_count = (job.attempt_count or 0) + 1
            job.started_at = datetime.utcnow()
            job.last_error = None
            _commit(session)

            try:
                job = session.query(ContentSyncJob).filter(ContentSyncJob.id == job_id).one()
                detail = self._projection(session, job)
                job.status = "completed"
                job.completed_at = datetime.utcnow()
                job.next_attempt_at = None
                _commit(session)
                return {"job_id": job_id, "status": "completed", "detail": detail}
            except Exception as exc:
                session.rollback()
                job = session.query(ContentSyncJob).filter(ContentSyncJob.id == job_id).first()
                if job is None:
                    logger.exception("Content sync job disappeared after failure: %s", job_id)
                    return {"job_id": job_id, "status": "failed", "detail": str(exc)}
                job.status = "failed"
                job.last_error = str(exc)[:4000]
                delay_minutes = min(2 ** max(job.attempt_count - 1, 0), 60)
                job.next_attempt_at = datetime.utcnow() + timedelta(minutes=delay_minutes)
                _commit(session)
                logger.warning("Content mirror sync failed for %s: %s", job_id, exc)
                return {"job_id": job_id, "status": "failed", "detail": str(exc)}


def _session_factory_for(session: Session) -> Callable[[], Session]:
    bind = session.get_bind()
    engine = getattr(bind, "engine", bind)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return factory


def _dispatch_after_commit(session: Session) -> None:
    job_ids = list(session.info.pop(_SESSION_JOB_IDS, []))
    if not job_ids or session.info.get(_SKIP_AUTO_DISPATCH):
        return
    try:
        ContentSyncProcessor(_session_factory_for(session)).process(job_ids)
        session.expire_all()
    except Exception:
        # The outbox rows are already committed. Startup recovery or an
        # explicit retry can safely continue without undoing the story write.
        logger.exception("Failed to dispatch committed content sync jobs")


def _clear_after_rollback(session: Session) -> None:
    session.info.pop(_SESSION_JOB_IDS, None)


def configure_content_sync_events() -> None:
    """Register post-commit dispatch once during explicit app composition."""

    global _events_configured
    if _events_configured:
        return
    event.listen(Session, "after_commit", _dispatch_after_commit)
    event.listen(Session, "after_rollback", _clear_after_rollback)
    _events_configured = True


def ensure_chapter_mirror(
    session: Session,
    project: Any,
    chapter: Any,
    *,
    index: int = 0,
    source: str = "mirror_read",
) -> tuple[Path, Path]:
    """Return a readable mirror without committing the caller's transaction."""

    folder = Path(project.folder_path) if project.folder_path else None
    path = folder / chapter.content_file_path if folder and chapter.content_file_path else None
    if not path or not path.exists():
        session_factory = _session_factory_for(session)
        with session_factory() as sync_session:
            persisted_project = sync_session.get(type(project), project.id)
            persisted_chapter = sync_session.get(type(chapter), chapter.id)
            if persisted_project is None or persisted_chapter is None:
                raise RuntimeError(
                    "Chapter must be committed before its mirror can be synchronized"
                )
            job = enqueue_content_sync(
                sync_session,
                ContentSyncIntent(
                    project_id=project.id,
                    target=ContentSyncTarget.CHAPTER,
                    entity_id=chapter.id,
                    payload={"index": index},
                    source=source,
                ),
            )
            job_id = job.id
            _commit(sync_session)
        # Explicit processing keeps scripts without FastAPI event setup correct.
        ContentSyncProcessor(session_factory).process([job_id])
        with session.no_autoflush:
            session.refresh(project)
            session.refresh(chapter)
        folder = Path(project.folder_path) if project.folder_path else None
        path = folder / chapter.content_file_path if folder and chapter.content_file_path else None
    if folder is None or path is None or not path.exists():
        raise RuntimeError("Chapter mirror synchronization did not produce a readable file")
    return folder, path.resolve()


def content_sync_health(session: Session, project_id: str) -> dict[str, Any]:
    rows = (
        session.query(ContentSyncJob.status, ContentSyncJob.id, ContentSyncJob.last_error)
        .filter(ContentSyncJob.project_id == project_id)
        .order_by(ContentSyncJob.created_at.desc())
        .limit(100)
        .all()
    )
    counts: dict[str, int] = {}
    for status, _job_id, _error in rows:
        counts[status] = counts.get(status, 0) + 1
    failed = next(
        (
            {"job_id": job_id, "error": error}
            for status, job_id, error in rows
            if status == "failed"
        ),
        None,
    )
    return {
        "counts": counts,
        "pending_count": counts.get("pending", 0) + counts.get("running", 0),
        "failed_count": counts.get("failed", 0),
        "latest_failure": failed,
    }


def recover_content_sync_queue(*, limit: int = 200) -> dict[str, int]:
    """Resume interrupted jobs and enqueue missing legacy project mirrors."""

    from ....database.models import Project
    from ....services.content_store import MANIFEST_NAME

    processor = ContentSyncProcessor(SessionLocal)
    interrupted = processor.recover_interrupted()
    queued = 0
    with SessionLocal() as session:
        for project in session.query(Project).all():
            manifest_exists = bool(
                project.folder_path
                and (Path(project.folder_path) / MANIFEST_NAME).exists()
            )
            if manifest_exists:
                continue
            enqueue_content_sync(
                session,
                ContentSyncIntent(
                    project_id=project.id,
                    target=ContentSyncTarget.PROJECT,
                    source="startup_recovery",
                ),
            )
            queued += 1
        if queued:
            _commit(session)
    # Auto-dispatch handles newly committed rows; this pass also picks up old
    # failed rows whose retry window has elapsed.
    reports = processor.process_pending(limit=limit)
    return {
        "interrupted": interrupted,
        "queued": queued,
        "processed": len(reports),
    }


__all__ = [
    "ContentSyncProcessor",
    "SqlAlchemyContentSyncOutbox",
    "SqlAlchemyContentSyncRuntime",
    "configure_content_sync_events",
    "content_sync_health",
    "ensure_chapter_mirror",
    "enqueue_content_sync",
    "enqueue_many_content_sync",
    "enqueue_project_sync",
    "recover_content_sync_queue",
]
