"""FastAPI application entry point."""
import asyncio
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from .core.config import get_settings
from .core.exceptions import (
    AppException,
    app_exception_handler,
    validation_exception_handler,
    general_exception_handler,
)
from .database.backup import backup_sqlite_database
from .database.session import Base, SessionLocal, engine
from .database.migrations import ensure_runtime_schema
from .routers import projects, config, getting_started, worldbuilding, characters, outline, chapters, ai_writer, stats, export, deconstruct, importer, cataloging, agent, skill, scheduler, mcp, external_agent, external_agent_global, tools, novel_creation, system_assistant, local_models, prompt_packs, narrative_governance, context_governance
from .services.content_store import migrate_legacy_projects_to_files
from .services.workspace.run_log import mark_interrupted_assistant_runs
from .version import APP_VERSION

settings = get_settings()


def _find_frontend_dist() -> Path | None:
    """Find bundled frontend assets in source or PyInstaller runtime."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "frontend" / "dist")
    backend_dir = Path(__file__).resolve().parents[2]
    repo_dir = backend_dir.parent
    candidates.extend([
        repo_dir / "frontend" / "dist",
        backend_dir / "frontend" / "dist",
    ])
    for candidate in candidates:
        if (candidate / "index.html").exists():
            return candidate
    return None


FRONTEND_DIST = _find_frontend_dist()

# Create database tables. For packaged users with existing local data, create a
# copy before runtime schema sync so a failed migration never strands the only DB.
backup_sqlite_database(settings.database_url, reason=f"pre-{APP_VERSION}")
Base.metadata.create_all(bind=engine)
ensure_runtime_schema(engine)
with SessionLocal() as db:
    migrate_legacy_projects_to_files(db)
    mark_interrupted_assistant_runs(db)

app = FastAPI(
    title="司命 API",
    description="Backend API for the Siming novel-writing tool.",
    version=APP_VERSION,
)

# Register exception handlers
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(projects.router, prefix="/api/v1")
app.include_router(config.router, prefix="/api/v1")
app.include_router(getting_started.router, prefix="/api/v1")
app.include_router(worldbuilding.router, prefix="/api/v1")
app.include_router(characters.router, prefix="/api/v1")
app.include_router(outline.router, prefix="/api/v1")
app.include_router(chapters.router, prefix="/api/v1")
app.include_router(ai_writer.router, prefix="/api/v1")
app.include_router(stats.router, prefix="/api/v1")
app.include_router(export.router, prefix="/api/v1")
app.include_router(deconstruct.router, prefix="/api/v1")
app.include_router(importer.router, prefix="/api/v1")
app.include_router(cataloging.router, prefix="/api/v1")
app.include_router(agent.router, prefix="/api/v1")
app.include_router(skill.router, prefix="/api/v1")
app.include_router(scheduler.router, prefix="/api/v1")
app.include_router(mcp.router, prefix="/api/v1")
app.include_router(external_agent.router, prefix="/api/v1")
app.include_router(external_agent_global.router, prefix="/api/v1")
app.include_router(tools.router, prefix="/api/v1")
app.include_router(prompt_packs.router, prefix="/api/v1")
app.include_router(novel_creation.router, prefix="/api/v1")
app.include_router(system_assistant.router, prefix="/api/v1")
app.include_router(local_models.router, prefix="/api/v1")
app.include_router(narrative_governance.router, prefix="/api/v1")
app.include_router(context_governance.router, prefix="/api/v1")


@app.get("/")
async def root():
    """Health check endpoint."""
    if FRONTEND_DIST:
        return FileResponse(FRONTEND_DIST / "index.html")
    return {"status": "ok", "service": "siming-api"}


@app.get("/health")
async def health_check():
    """Detailed health check."""
    return {"status": "healthy", "version": APP_VERSION, "build": "multi-agent-cli-autoconfig"}


@app.on_event("startup")
async def startup_scheduler():
    """Start background services and auto-configure detected local Agent clients."""
    try:
        from .services.scheduler.engine import start_scheduler
        start_scheduler()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Failed to start scheduler: %s", exc)
    try:
        from .services.local_runtime.model_jobs import resume_incomplete_downloads
        from .services.local_runtime.training import resume_incomplete_training_jobs
        from .services.opencode_onboarding import resume_incomplete_opencode_activations

        resume_incomplete_downloads()
        resume_incomplete_training_jobs()
        resume_incomplete_opencode_activations()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Failed to resume local AI jobs: %s", exc)
    if "pytest" not in sys.modules:
        try:
            from .services.context_orchestrator import ContextOrchestrator, run_context_rebuild_job

            with SessionLocal() as db:
                job = ContextOrchestrator(db).create_rebuild_job(requested_by="startup")
                job_id = job.id if job.status == "queued" else ""
                db.commit()
            if job_id:
                asyncio.create_task(asyncio.to_thread(run_context_rebuild_job, job_id))
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Failed to schedule context rebuild: %s", exc)
    if "pytest" not in sys.modules:
        async def _configure_external_agents() -> None:
            try:
                import asyncio
                import logging
                from .services.external_agent.mcp_auto_config import (
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
                logging.getLogger(__name__).info(
                    "External Agent MCP auto-configuration: %s; providers added=%s; defaults migrated=%s",
                    result.get("detail"),
                    created_providers,
                    defaults_migrated,
                )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "External Agent MCP auto-configuration failed: %s",
                    exc,
                )

        asyncio.create_task(_configure_external_agents())


@app.on_event("shutdown")
async def shutdown_local_runtime():
    from .services.local_runtime import get_runtime_manager

    get_runtime_manager().stop()


if FRONTEND_DIST:
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        """Serve the React SPA in packaged mode."""
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        target = FRONTEND_DIST / full_path
        if target.exists() and target.is_file():
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")
