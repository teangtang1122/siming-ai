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
from ..core.config import get_settings
from ..core.logging_setup import configure_logging
from ..database.session import SessionLocal

logger = logging.getLogger(__name__)


def _is_benign_windows_pipe_reset(context: dict[str, object]) -> bool:
    """Identify one harmless asyncio/Windows transport shutdown callback.

    A browser or health-check client can close a socket just before the
    Proactor transport calls ``shutdown``. CPython reports WinError 10054 via
    the event-loop exception handler even though the request and server remain
    healthy. Keep the match deliberately narrow so real connection failures
    and all application exceptions still reach the normal handler.
    """

    error = context.get("exception")
    message = str(context.get("message") or "")
    return (
        sys.platform == "win32"
        and isinstance(error, ConnectionResetError)
        and getattr(error, "winerror", None) == 10054
        and "_ProactorBasePipeTransport._call_connection_lost" in message
    )


def _install_windows_transport_exception_filter(
    loop: asyncio.AbstractEventLoop,
) -> tuple[object, object | None]:
    """Suppress only the known harmless Proactor close-race log entry."""

    previous_handler = loop.get_exception_handler()

    def handler(current_loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        if _is_benign_windows_pipe_reset(context):
            logger.debug("Ignored harmless Windows Proactor connection-reset close race")
            return
        if previous_handler is not None:
            previous_handler(current_loop, context)
            return
        current_loop.default_exception_handler(context)

    loop.set_exception_handler(handler)
    return handler, previous_handler


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
    from ..modules.assistant.infrastructure.system_conversations import (
        SqlAlchemySystemConversationStore,
    )
    from ..modules.model_runtime.infrastructure.readiness import (
        repair_runtime_readiness_demotions,
    )
    from ..modules.story.infrastructure.content_sync import (
        recover_content_sync_queue,
    )
    from ..services.cataloging.job_control import reconcile_cataloging_operation_projections
    from ..services.cataloging.launcher import mark_interrupted_cataloging_jobs
    from ..services.novel_creation_imports import mark_interrupted_material_imports
    from ..services.novel_creation_runs import mark_interrupted_novel_creation_runs
    from ..services.operation_runtime import mark_interrupted_operations
    from ..services.workspace.run_log import mark_interrupted_assistant_runs

    recover_content_sync_queue()
    _recover_gateway_sync_capture_queue()
    with SqlAlchemyUnitOfWork(SessionLocal) as uow:
        repair_runtime_readiness_demotions(uow.session)
        mark_interrupted_assistant_runs(uow.session)
        # Creation runs own their durable result state. Reconcile them before
        # projecting generic operations so saved output remains reviewable.
        mark_interrupted_novel_creation_runs(uow.session)
        mark_interrupted_material_imports(uow.session)
        # CatalogingJob is authoritative.  Repair legacy/local-CLI projection
        # drift before generic recovery can mistake finished work for an
        # interrupted process.
        mark_interrupted_cataloging_jobs(uow.session)
        reconcile_cataloging_operation_projections(uow.session)
        mark_interrupted_operations(uow.session)
        SqlAlchemySystemConversationStore(uow.session).interrupt_running_messages()
        uow.commit()


def _recover_gateway_sync_capture_queue() -> None:
    """Recover optional Gateway replication without blocking desktop startup."""

    if not get_settings().gateway_enabled:
        return
    try:
        from ..modules.gateway.infrastructure.change_capture import (
            recover_sync_capture_queue,
        )

        recover_sync_capture_queue()
    except Exception:
        # Gateway capture is a retryable projection of canonical authoring data.
        # A stale or malformed queue item must not make the whole desktop app
        # unavailable; keep it for a later retry and expose the cause in logs.
        logger.exception("Failed to recover Gateway sync capture queue")


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
            job = ContextOrchestrator(uow.session).create_rebuild_job(requested_by="startup")
            job_id = job.id if job.status == "queued" else ""
            uow.commit()
        if job_id:
            asyncio.create_task(asyncio.to_thread(run_context_rebuild_job, job_id))
            logger.info("Context rebuild job scheduled job_id=%s", job_id)
    except Exception as exc:
        logger.warning("Failed to schedule context rebuild: %s", exc)


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
    logger.info(
        "Database bootstrap mode=%s schema_revision=%s",
        result.mode,
        result.schema_revision,
    )
    if result.read_only:
        logger.warning(
            "Database bootstrap entered read-only recovery mode=%s message=%s",
            result.mode,
            result.message,
        )
        return RuntimeBootstrapStatus(
            status="recovery",
            database_mode=result.mode,
            schema_revision=result.schema_revision,
            message=result.message,
            read_only=True,
        )

    await asyncio.to_thread(_run_legacy_startup_recovery)
    logger.info("Legacy startup recovery finished")
    await asyncio.to_thread(_start_scheduler)
    logger.info("Background scheduler started")
    settings = get_settings()
    if not settings.gateway_enabled:
        await asyncio.to_thread(_resume_local_runtime_jobs)
    if "pytest" not in sys.modules:
        _schedule_context_rebuild()
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
        logger.info("Background scheduler stopped")
    except Exception as exc:
        logger.warning("Failed to stop scheduler: %s", exc)
    try:
        from ..services.local_runtime import get_runtime_manager

        get_runtime_manager().stop()
        logger.info("Local runtime stopped")
    except Exception as exc:
        logger.warning("Failed to stop local runtime: %s", exc)


@asynccontextmanager
async def application_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run all stateful startup work in one observable lifecycle."""
    # Uvicorn applies its own logging dictConfig before the lifespan starts;
    # re-assert our console/file layout so every entry point produces the same
    # logs regardless of the launcher that started the process.
    configure_logging(force=True)
    loop = asyncio.get_running_loop()
    installed_handler, previous_handler = _install_windows_transport_exception_filter(loop)
    app.state.runtime_bootstrap = RuntimeBootstrapStatus()
    try:
        app.state.runtime_bootstrap = await _bootstrap_runtime(app)
        yield
    finally:
        await asyncio.to_thread(_shutdown_runtime)
        if loop.get_exception_handler() is installed_handler:
            loop.set_exception_handler(previous_handler)
