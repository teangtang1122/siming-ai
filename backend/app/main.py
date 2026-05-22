"""FastAPI application entry point."""
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
from .database.session import engine, Base
from .database.migrations import ensure_runtime_schema
from .routers import projects, config, llm, worldbuilding, characters, outline, chapters, ai_writer, stats, export, deconstruct, importer
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

# Create database tables
Base.metadata.create_all(bind=engine)
ensure_runtime_schema(engine)

app = FastAPI(
    title="墨枢 API",
    description="Backend API for the Moshu novel-writing tool.",
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
app.include_router(llm.router, prefix="/api/v1")
app.include_router(worldbuilding.router, prefix="/api/v1")
app.include_router(characters.router, prefix="/api/v1")
app.include_router(outline.router, prefix="/api/v1")
app.include_router(chapters.router, prefix="/api/v1")
app.include_router(ai_writer.router, prefix="/api/v1")
app.include_router(stats.router, prefix="/api/v1")
app.include_router(export.router, prefix="/api/v1")
app.include_router(deconstruct.router, prefix="/api/v1")
app.include_router(importer.router, prefix="/api/v1")


@app.get("/")
async def root():
    """Health check endpoint."""
    if FRONTEND_DIST:
        return FileResponse(FRONTEND_DIST / "index.html")
    return {"status": "ok", "service": "moshu-api"}


@app.get("/health")
async def health_check():
    """Detailed health check."""
    return {"status": "healthy", "version": APP_VERSION}


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
