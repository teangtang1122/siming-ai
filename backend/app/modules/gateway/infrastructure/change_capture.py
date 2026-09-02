"""Transactional capture of canonical Gateway writes into the sync change log."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import datetime

from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from app.architecture.uow import commit_session
from app.core.config import get_settings
from app.database.models_support import generate_uuid
from app.database.session import SessionLocal
from app.modules.gateway.application.contracts import SyncMutation
from app.modules.gateway.infrastructure.models import (
    SyncCaptureJob,
    SyncEntityState,
    SyncProject,
)
from app.modules.gateway.infrastructure.service import GatewayService
from app.services.gateway_legacy_replication import (
    Chapter,
    Character,
    RecordSpec,
    WorldbuildingEntry,
    project_id_for_record,
    serialize_record,
    spec_for_instance,
)

logger = logging.getLogger(__name__)

_PENDING_CAPTURE = "siming_pending_gateway_capture"
_CAPTURE_JOB_IDS = "siming_gateway_capture_job_ids"
_SKIP_CAPTURE = "siming_sync_projection"
_events_configured = False


def _remember_job(session: Session, job_id: str) -> None:
    job_ids = session.info.setdefault(_CAPTURE_JOB_IDS, [])
    if job_id not in job_ids:
        job_ids.append(job_id)


def _parent_from_session(session: Session, model: type, parent_id: str | None):
    if not parent_id:
        return None
    candidates = list(session.new) + list(session.identity_map.values())
    for candidate in candidates:
        if isinstance(candidate, model) and str(getattr(candidate, "id", "")) == str(parent_id):
            return candidate
    return session.get(model, parent_id)


def _project_id(session: Session, row, spec: RecordSpec) -> str | None:
    resolved = project_id_for_record(session, row, spec)
    if resolved or spec.project_mode in {"self", "direct"}:
        return resolved
    if spec.project_mode == "chapter":
        parent = _parent_from_session(session, Chapter, getattr(row, "chapter_id", None))
    elif spec.project_mode == "character":
        parent = _parent_from_session(session, Character, getattr(row, "character_id", None))
    elif spec.project_mode == "world":
        parent = _parent_from_session(
            session,
            WorldbuildingEntry,
            getattr(row, "entry_id", None),
        )
    else:
        parent = None
    return str(parent.project_id) if parent is not None else None


def _capture_before_flush(session: Session, _flush_context, _instances) -> None:
    if not get_settings().gateway_enabled or session.info.get(_SKIP_CAPTURE):
        return

    tracked = list(session.new) + list(session.dirty) + list(session.deleted)
    for row in tracked:
        spec = spec_for_instance(row)
        if spec is not None and getattr(row, "id", None) is None:
            row.id = generate_uuid()

    pending: dict[tuple[str, str, str], dict] = session.info.setdefault(
        _PENDING_CAPTURE,
        {},
    )
    deleted_ids = {id(row) for row in session.deleted}
    new_ids = {id(row) for row in session.new}
    for row in tracked:
        spec = spec_for_instance(row)
        if spec is None:
            continue
        if (
            id(row) not in deleted_ids
            and id(row) not in new_ids
            and not session.is_modified(row, include_collections=False)
        ):
            continue
        project_id = _project_id(session, row, spec)
        entity_id = str(getattr(row, "id", "") or "")
        if not project_id or not entity_id:
            continue
        with session.no_autoflush:
            config = session.get(SyncProject, project_id)
        if config is None or config.status != "enabled":
            continue
        operation = "delete" if id(row) in deleted_ids else "upsert"
        pending[(project_id, spec.entity_type, entity_id)] = {
            "project_id": project_id,
            "entity_type": spec.entity_type,
            "entity_id": entity_id,
            "operation": operation,
            "row": row,
            "spec": spec,
            "delete_payload": serialize_record(row, spec) if operation == "delete" else None,
        }


def _capture_after_flush(session: Session, _flush_context) -> None:
    pending = session.info.pop(_PENDING_CAPTURE, {})
    if not pending or session.info.get(_SKIP_CAPTURE):
        return
    for item in pending.values():
        payload = (
            item["delete_payload"]
            if item["operation"] == "delete"
            else serialize_record(item["row"], item["spec"])
        )
        job = SyncCaptureJob(
            id=generate_uuid(),
            project_id=item["project_id"],
            entity_type=item["entity_type"],
            entity_id=item["entity_id"],
            operation=item["operation"],
            payload_json=payload,
        )
        session.add(job)
        _remember_job(session, job.id)


def _session_factory_for(session: Session) -> Callable[[], Session]:
    bind = session.get_bind()
    engine = getattr(bind, "engine", bind)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


class SyncCaptureProcessor:
    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        self._session_factory = session_factory

    def process(self, job_ids: Iterable[str]) -> list[dict[str, str]]:
        reports: list[dict[str, str]] = []
        for job_id in dict.fromkeys(job_ids):
            report = self._process_one(job_id)
            if report:
                reports.append(report)
        return reports

    def process_pending(self, *, limit: int = 500) -> list[dict[str, str]]:
        with self._session_factory() as session:
            job_ids = [
                row.id
                for row in session.query(SyncCaptureJob)
                .filter(SyncCaptureJob.status.in_(["pending", "failed"]))
                .order_by(SyncCaptureJob.created_at)
                .limit(limit)
                .all()
            ]
        return self.process(job_ids)

    def _process_one(self, job_id: str) -> dict[str, str] | None:
        with self._session_factory() as session:
            job = session.get(SyncCaptureJob, job_id)
            if job is None or job.status == "completed":
                return None
            job.status = "running"
            job.attempt_count = (job.attempt_count or 0) + 1
            job.last_error = None
            session.flush()
            state = (
                session.query(SyncEntityState)
                .filter(
                    SyncEntityState.project_id == job.project_id,
                    SyncEntityState.entity_type == job.entity_type,
                    SyncEntityState.entity_id == job.entity_id,
                )
                .first()
            )
            base_revision = state.revision if state is not None else 0
            mutation = SyncMutation(
                mutation_id=f"server-{job.id}",
                project_id=job.project_id,
                entity_type=job.entity_type,
                entity_id=job.entity_id,
                operation=job.operation,
                base_revision=base_revision,
                payload=job.payload_json if job.operation == "upsert" else None,
            )
            result = GatewayService(session)._apply_mutation(
                mutation,
                device_id=None,
                project_domain=False,
            )
            if result.status in {"applied", "duplicate"}:
                job.status = "completed"
                job.completed_at = datetime.utcnow()
                commit_session(session)
                return {"job_id": job.id, "status": "completed"}
            job.status = "failed"
            job.last_error = (result.message or result.status)[:4000]
            commit_session(session)
            return {"job_id": job.id, "status": "failed"}


def _dispatch_after_commit(session: Session) -> None:
    job_ids = list(session.info.pop(_CAPTURE_JOB_IDS, []))
    if not job_ids:
        return
    logger.debug("Dispatching Gateway sync capture jobs=%d", len(job_ids))
    try:
        SyncCaptureProcessor(_session_factory_for(session)).process(job_ids)
        session.expire_all()
    except Exception:
        # Jobs were committed atomically with the authoring write. Startup
        # recovery can retry without losing the canonical story mutation.
        logger.exception("Failed to dispatch Gateway sync capture jobs")


def _clear_after_rollback(session: Session) -> None:
    session.info.pop(_PENDING_CAPTURE, None)
    session.info.pop(_CAPTURE_JOB_IDS, None)


def configure_gateway_capture_events() -> None:
    global _events_configured
    if _events_configured:
        return
    event.listen(Session, "before_flush", _capture_before_flush)
    event.listen(Session, "after_flush_postexec", _capture_after_flush)
    event.listen(Session, "after_commit", _dispatch_after_commit)
    event.listen(Session, "after_rollback", _clear_after_rollback)
    _events_configured = True


def recover_sync_capture_queue(*, limit: int = 500) -> int:
    return len(SyncCaptureProcessor(SessionLocal).process_pending(limit=limit))


__all__ = [
    "SyncCaptureProcessor",
    "configure_gateway_capture_events",
    "recover_sync_capture_queue",
]
