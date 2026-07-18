"""SQLAlchemy adapter for context-governance administration."""

from __future__ import annotations

from ....architecture.uow import SqlAlchemyUnitOfWork
from ....services.context_orchestrator import ContextOrchestrator
from .models import ContextManifest, ContextRebuildJob, ModelContextProfile


def _job_payload(job: ContextRebuildJob) -> dict:
    return {
        "id": job.id,
        "operation_id": job.operation_id,
        "policy_version": job.policy_version,
        "status": job.status,
        "requested_by": job.requested_by,
        "total_projects": job.total_projects,
        "completed_projects": job.completed_projects,
        "failed_projects": job.failed_projects,
        "semantic_available": bool(job.semantic_available),
        "error": job.error,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "projects": [
            {
                "id": item.id,
                "project_id": item.project_id,
                "status": item.status,
                "index_version": item.index_version,
                "current_source_type": item.current_source_type,
                "indexed_chunks": item.indexed_chunks,
                "semantic_chunks": item.semantic_chunks,
                "error": item.error,
                "started_at": item.started_at.isoformat() if item.started_at else None,
                "completed_at": item.completed_at.isoformat() if item.completed_at else None,
            }
            for item in job.projects
        ],
    }


class SqlAlchemyContextGovernance:
    def list_profiles(self, session) -> dict:
        rows = (
            session.query(ModelContextProfile)
            .order_by(ModelContextProfile.provider, ModelContextProfile.model_name)
            .all()
        )
        return {
            "items": [
                {
                    "id": row.id,
                    "provider": row.provider,
                    "model_name": row.model_name,
                    "context_window_tokens": row.context_window_tokens,
                    "max_output_tokens": row.max_output_tokens,
                    "safety_margin_tokens": row.safety_margin_tokens,
                    "enabled": bool(row.enabled),
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
                for row in rows
            ],
            "fallback": {
                "context_window_tokens": 16384,
                "reason": "Unknown models use a conservative 16K profile.",
            },
            "semantic": ContextOrchestrator(session).semantic_status(),
        }

    def save_profile(self, session, values: dict) -> dict:
        provider = str(values.pop("provider")).strip()
        model_name = str(values.pop("model_name")).strip()
        row = (
            session.query(ModelContextProfile)
            .filter(
                ModelContextProfile.provider == provider,
                ModelContextProfile.model_name == model_name,
            )
            .first()
        )
        with SqlAlchemyUnitOfWork.from_session(session) as uow:
            if row is None:
                row = ModelContextProfile(provider=provider, model_name=model_name)
                session.add(row)
            for field, value in values.items():
                setattr(row, field, value)
            uow.commit()
            session.refresh(row)
        return {
            "id": row.id,
            "provider": row.provider,
            "model_name": row.model_name,
            "context_window_tokens": row.context_window_tokens,
            "max_output_tokens": row.max_output_tokens,
            "safety_margin_tokens": row.safety_margin_tokens,
            "enabled": bool(row.enabled),
        }

    def list_rebuilds(self, session, limit: int) -> list[dict]:
        rows = (
            session.query(ContextRebuildJob)
            .order_by(ContextRebuildJob.created_at.desc())
            .limit(limit)
            .all()
        )
        return [_job_payload(row) for row in rows]

    def create_rebuild(self, session, values: dict) -> dict:
        with SqlAlchemyUnitOfWork.from_session(session) as uow:
            job = ContextOrchestrator(session).create_rebuild_job(**values)
            uow.commit()
            session.refresh(job)
        return _job_payload(job)

    def retry_rebuild(self, session, job_id: str) -> dict | None:
        job = session.query(ContextRebuildJob).filter(ContextRebuildJob.id == job_id).first()
        if not job:
            return None
        with SqlAlchemyUnitOfWork.from_session(session) as uow:
            ContextOrchestrator(session).retry_rebuild_job(job)
            uow.commit()
            session.refresh(job)
        return _job_payload(job)

    def list_manifests(self, session, project_id: str, limit: int) -> list[dict]:
        rows = (
            session.query(ContextManifest)
            .filter(ContextManifest.project_id == project_id)
            .order_by(ContextManifest.created_at.desc())
            .limit(limit)
            .all()
        )
        orchestrator = ContextOrchestrator(session)
        return [orchestrator.manifest_payload(row, include_content=False) for row in rows]


__all__ = ["SqlAlchemyContextGovernance"]
