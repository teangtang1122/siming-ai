"""Explicit application startup and shutdown lifecycle."""
from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass

from fastapi import FastAPI

from ..architecture.uow import SqlAlchemyUnitOfWork
from ..database.session import SessionLocal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeBootstrapStatus:
    """User-visible result of the application bootstrap sequence."""

    status: str = "pending"
    database_mode: str = "pending"
    schema_revision: str | None = None
    message: str = "Application startup has not run yet."
    read_only: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _run_legacy_startup_recovery() -> None:
    """Run compatibility recovery after the database schema is available."""
    from ..services.content_store import migrate_legacy_projects_to_files
    from ..services.operation_runtime import mark_interrupted_operations
    from ..services.workspace.run_log import mark_interrupted_assistant_runs

    with SessionLocal() as db:
        migrate_legacy_projects_to_files(db)
        mark_interrupted_assistant_runs(db)
        mark_interrupted_operations(db)


def _start_scheduler() -> None:
    try:
        from ..services.scheduler.engine import start_scheduler

        start_scheduler()
    except Exception as exc:
        logger.warning("Failed to start scheduler: %s", exc)


def _resume_local_runtime_jobs() -> None:
    try:
        from ..services.local_runtime.model_jobs import resume_incomplete_downloads
        from ..services.local_runtime.training import resume_incomplete_training_jobs
        from ..services.opencode_onboarding import resume_incomplete_opencode_activations

        resume_incomplete_downloads()
        resume_incomplete_training_jobs()
        resume_incomplete_opencode_activations()
    except Exception as exc:
        logger.warning("Failed to resume local AI jobs: %s", exc)


def _schedule_context_rebuild() -> None:
    try:
        from ..services.context_orchestrator import ContextOrchestrator, run_context_rebuild_job

        with SqlAlchemyUnitOfWork(SessionLocal) as uow:
            job = ContextOrchestrator(uow.session).create_rebuild_job(
                requested_by="startup"
            )
            job_id = job.id if job.status == "queued" else ""
            uow.commit()
        if job_id:
            asyncio.create_task(asyncio.to_thread(run_context_rebuild_job, job_id))
    except Exception as exc:
        logger.warning("Failed to schedule context rebuild: %s", exc)


async def _configure_external_agents() -> None:
    try:
        from ..services.external_agent.mcp_auto_config import (
            auto_configure_detected_mcp_clients,
            ensure_detected_local_cli_model_configs,
            migrate_legacy_external_agent_defaults,
        )

        result = await asyncio.to_thread(
            auto_configure_detected_mcp_clients,
            permission_pack="auto",
        )
        with SessionLocal() as db:
            created_providers = ensure_detected_local_cli_model_configs(db)
            defaults_migrated = migrate_legacy_external_agent_defaults(db)
        logger.info(
            "External Agent MCP auto-configuration: %s; providers added=%s; defaults migrated=%s",
            result.get("detail"),
            created_providers,
            defaults_migrated,
        )
    except Exception as exc:
        logger.warning("External Agent MCP auto-configuration failed: %s", exc)


async def _bootstrap_runtime(app: FastAPI) -> RuntimeBootstrapStatus:
    """Prepare persistent state before accepting requests."""
    if "pytest" in sys.modules and not getattr(app.state, "force_test_bootstrap", False):
        return RuntimeBootstrapStatus(
            status="ready",
            database_mode="test_managed",
            message="Database lifecycle is managed by the test fixture.",
        )

    # The database bootstrap implementation is isolated behind this import so
    # importing app.main or exporting OpenAPI never mutates persistent state.
    from ..database.bootstrap import bootstrap_database

    result = await asyncio.to_thread(bootstrap_database)
    if result.read_only:
        return RuntimeBootstrapStatus(
            status="recovery",
            database_mode=result.mode,
            schema_revision=result.schema_revision,
            message=result.message,
            read_only=True,
        )

    await asyncio.to_thread(_run_legacy_startup_recovery)
    await asyncio.to_thread(_start_scheduler)
    await asyncio.to_thread(_resume_local_runtime_jobs)
    if "pytest" not in sys.modules:
        _schedule_context_rebuild()
        asyncio.create_task(_configure_external_agents())
    return RuntimeBootstrapStatus(
        status="ready",
        database_mode=result.mode,
        schema_revision=result.schema_revision,
        message=result.message,
    )


def _shutdown_runtime() -> None:
    try:
        from ..services.scheduler.engine import stop_scheduler

        stop_scheduler()
    except Exception as exc:
        logger.warning("Failed to stop scheduler: %s", exc)
    try:
        from ..services.local_runtime import get_runtime_manager

        get_runtime_manager().stop()
    except Exception as exc:
        logger.warning("Failed to stop local runtime: %s", exc)


@asynccontextmanager
async def application_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run all stateful startup work in one observable lifecycle."""
    app.state.runtime_bootstrap = RuntimeBootstrapStatus()
    try:
        app.state.runtime_bootstrap = await _bootstrap_runtime(app)
        yield
    finally:
        await asyncio.to_thread(_shutdown_runtime)
