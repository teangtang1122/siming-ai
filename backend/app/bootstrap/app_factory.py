"""FastAPI application factory without import-time side effects."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..core.config import get_settings
from ..core.exceptions import (
    AppException,
    app_exception_handler,
    general_exception_handler,
    validation_exception_handler,
)
from ..version import APP_VERSION
from .composition import configure_application_services
from .http_security import LocalOriginGuardMiddleware, SecurityHeadersMiddleware
from .lifecycle import RuntimeBootstrapStatus, application_lifespan


def find_frontend_dist() -> Path | None:
    """Find bundled frontend assets in source or PyInstaller runtime."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(
            Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "frontend" / "dist"
        )
    backend_dir = Path(__file__).resolve().parents[3]
    repo_dir = backend_dir.parent
    candidates.extend(
        [
            repo_dir / "frontend" / "dist",
            backend_dir / "frontend" / "dist",
        ]
    )
    for candidate in candidates:
        if (candidate / "index.html").exists():
            return candidate
    return None


def resolve_frontend_file(frontend_dist: Path, requested_path: str) -> Path | None:
    """Resolve a bundled frontend file without allowing directory escape."""

    root = frontend_dist.resolve()
    target = (root / requested_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target if target.is_file() else None


def _register_routers(app: FastAPI) -> None:
    from ..routers import (
        agent,
        ai_writer,
        application_updates,
        cataloging,
        chapters,
        characters,
        config,
        context_governance,
        deconstruct,
        export,
        external_agent,
        external_agent_global,
        getting_started,
        importer,
        local_models,
        mcp,
        narrative_governance,
        novel_creation,
        operations,
        outline,
        projects,
        prompt_packs,
        scheduler,
        skill,
        stats,
        system_assistant,
        tools,
        worldbuilding,
    )

    routers = (
        projects,
        application_updates,
        config,
        getting_started,
        worldbuilding,
        characters,
        outline,
        chapters,
        ai_writer,
        stats,
        export,
        deconstruct,
        importer,
        cataloging,
        agent,
        skill,
        scheduler,
        mcp,
        external_agent,
        external_agent_global,
        tools,
        prompt_packs,
        novel_creation,
        system_assistant,
        local_models,
        narrative_governance,
        context_governance,
        operations,
    )
    for router_module in routers:
        app.include_router(router_module.router, prefix="/api/v1")


def _register_recovery_guard(app: FastAPI) -> None:
    """Reject writes when schema safety could not be established."""

    @app.middleware("http")
    async def enforce_read_only_recovery(request, call_next):
        bootstrap = getattr(app.state, "runtime_bootstrap", None)
        read_only = bool(getattr(bootstrap, "read_only", False))
        safe_method = request.method.upper() in {"GET", "HEAD", "OPTIONS"}
        update_path = request.url.path.startswith("/api/v1/config/update/")
        if read_only and not safe_method and not update_path:
            return JSONResponse(
                status_code=503,
                content={
                    "code": 503,
                    "message": (
                        "数据库结构无法安全识别，司命已进入只读恢复模式。"
                        "请先备份数据库、查看日志或安装修复版本。"
                    ),
                    "data": {
                        "mode": getattr(bootstrap, "database_mode", "read_only_recovery"),
                        "detail": getattr(bootstrap, "message", ""),
                        "next_action": "备份数据后查看系统日志，或安装更新版本",
                    },
                },
            )
        return await call_next(request)


def _register_frontend(app: FastAPI, frontend_dist: Path | None) -> None:
    @app.get("/")
    async def root():
        if frontend_dist:
            return FileResponse(frontend_dist / "index.html")
        return {"status": "ok", "service": "siming-api"}

    @app.get("/health")
    async def health_check():
        bootstrap = getattr(
            app.state,
            "runtime_bootstrap",
            RuntimeBootstrapStatus(status="not_started"),
        )
        return {
            "status": "healthy" if not bootstrap.read_only else "recovery",
            "version": APP_VERSION,
            "architecture": "3.0-modular-monolith",
            "bootstrap": bootstrap.to_dict(),
        }

    if not frontend_dist:
        return

    assets_dir = frontend_dist / "assets"
    if assets_dir.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="frontend-assets",
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        root = frontend_dist.resolve()
        target = resolve_frontend_file(root, full_path)
        if target is None and ".." in Path(full_path).parts:
            raise HTTPException(status_code=404, detail="Not Found") from None
        if target is not None:
            return FileResponse(target)
        return FileResponse(root / "index.html")


@asynccontextmanager
async def _no_op_lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.runtime_bootstrap = RuntimeBootstrapStatus(
        status="disabled",
        database_mode="not_started",
        message="Runtime bootstrap is disabled for schema generation.",
    )
    yield


def create_app(*, run_startup: bool = True) -> FastAPI:
    """Build the web application; persistent work belongs to lifespan."""
    settings = get_settings()
    configure_application_services()
    app = FastAPI(
        title="司命 API",
        description="Backend API for the Siming novel-writing tool.",
        version=APP_VERSION,
        lifespan=application_lifespan if run_startup else _no_op_lifespan,
    )
    app.state.runtime_bootstrap = RuntimeBootstrapStatus(status="not_started")

    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)
    from fastapi.exceptions import RequestValidationError

    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        LocalOriginGuardMiddleware,
        allowed_origins=settings.get_cors_origins(),
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    app.add_middleware(SecurityHeadersMiddleware)

    _register_routers(app)
    _register_recovery_guard(app)
    _register_frontend(app, find_frontend_dist())
    return app
