"""Checkpointed context-index rebuild runner."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.architecture.uow import SqlAlchemyUnitOfWork
from app.database.session import SessionLocal
from app.modules.operations.interfaces.reporting import checkpoint_operation

from .models import ContextRebuildJob, ContextRebuildProject


class ContextRebuildRunner:
    """Run each project as an independent, durable transaction."""

    def __init__(
        self,
        *,
        orchestrator_factory: Callable[[Any], Any],
        lexical_reindexer: Callable[[Any, str], dict],
    ) -> None:
        self._orchestrator_factory = orchestrator_factory
        self._lexical_reindexer = lexical_reindexer

    def run(self, job_id: str) -> None:
        row_ids = self._start(job_id)
        for row_id in row_ids:
            if self._already_completed(row_id):
                continue
            self._mark_running(row_id)
            try:
                self._process(row_id)
            except Exception as exc:
                self._mark_failed(row_id, exc)
        self._finish(job_id)

    def _start(self, job_id: str) -> list[str]:
        with SqlAlchemyUnitOfWork(SessionLocal) as uow:
            job = (
                uow.session.query(ContextRebuildJob).filter(ContextRebuildJob.id == job_id).first()
            )
            if not job:
                return []
            if job.status != "completed":
                job.status = "running"
                job.started_at = job.started_at or datetime.utcnow()
                self._orchestrator_factory(uow.session)._sync_rebuild_operation(
                    job,
                    message="正在准备上下文索引重建",
                )
            row_ids = [row.id for row in job.projects]
            uow.commit()
            return row_ids

    @staticmethod
    def _already_completed(row_id: str) -> bool:
        with SessionLocal() as db:
            row = db.query(ContextRebuildProject).filter(ContextRebuildProject.id == row_id).first()
            return bool(not row or row.status == "completed")

    def _mark_running(self, row_id: str) -> None:
        with SqlAlchemyUnitOfWork(SessionLocal) as uow:
            row = (
                uow.session.query(ContextRebuildProject)
                .filter(ContextRebuildProject.id == row_id)
                .first()
            )
            if not row:
                return
            row.status = "running"
            row.started_at = row.started_at or datetime.utcnow()
            row.error = None
            row.current_source_type = "lexical"
            self._orchestrator_factory(uow.session)._sync_rebuild_operation(
                row.job,
                message=f"正在重建作品 {row.project_id} 的关键词索引",
            )
            uow.commit()

    def _process(self, row_id: str) -> None:
        with SqlAlchemyUnitOfWork(SessionLocal) as uow:
            row = (
                uow.session.query(ContextRebuildProject)
                .filter(ContextRebuildProject.id == row_id)
                .first()
            )
            if not row:
                return
            orchestrator = self._orchestrator_factory(uow.session)
            lexical = self._lexical_reindexer(uow.session, row.project_id)
            row.indexed_chunks = int(lexical.get("total_chunks") or 0)
            row.current_source_type = "semantic"
            semantic = orchestrator.build_semantic_embeddings(row.project_id)
            row.semantic_chunks = int(semantic.get("indexed") or 0)
            row.status = "completed"
            row.current_source_type = None
            row.completed_at = datetime.utcnow()
            row.job.completed_projects = int(row.job.completed_projects or 0) + 1
            orchestrator._sync_rebuild_operation(
                row.job,
                message=f"作品 {row.project_id} 索引重建完成",
            )
            checkpoint_operation(
                uow.session,
                row.job.operation_id,
                payload={"project_id": row.project_id, "status": row.status},
            )
            uow.commit()

    def _mark_failed(self, row_id: str, error: Exception) -> None:
        with SqlAlchemyUnitOfWork(SessionLocal) as uow:
            row = (
                uow.session.query(ContextRebuildProject)
                .filter(ContextRebuildProject.id == row_id)
                .first()
            )
            if not row:
                return
            row.status = "failed"
            row.current_source_type = None
            row.error = str(error)[:8000]
            row.completed_at = datetime.utcnow()
            row.job.failed_projects = int(row.job.failed_projects or 0) + 1
            self._orchestrator_factory(uow.session)._sync_rebuild_operation(
                row.job,
                message=f"作品 {row.project_id} 索引重建失败",
            )
            checkpoint_operation(
                uow.session,
                row.job.operation_id,
                payload={"project_id": row.project_id, "status": row.status},
            )
            uow.commit()

    def _finish(self, job_id: str) -> None:
        with SqlAlchemyUnitOfWork(SessionLocal) as uow:
            job = (
                uow.session.query(ContextRebuildJob).filter(ContextRebuildJob.id == job_id).first()
            )
            if not job:
                return
            remaining = (
                uow.session.query(ContextRebuildProject)
                .filter(
                    ContextRebuildProject.job_id == job.id,
                    ContextRebuildProject.status.in_(["queued", "running"]),
                )
                .count()
            )
            if not remaining:
                job.status = "completed" if not job.failed_projects else "failed"
                job.completed_at = datetime.utcnow()
            self._orchestrator_factory(uow.session)._sync_rebuild_operation(
                job,
                message=(
                    "上下文索引重建完成"
                    if job.status == "completed"
                    else "部分作品的上下文索引重建失败"
                ),
            )
            uow.commit()
