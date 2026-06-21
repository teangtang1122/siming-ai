#!/usr/bin/env python3
"""Moshu MCP Server — stdio entrypoint for MCP clients.

Usage:
    python scripts/moshu-mcp-server.py [--project-id ID] [--permission-pack PACK]

This script starts a stdio-based MCP server that exposes Moshu workspace
tools to MCP clients such as Claude Desktop, Cursor, and other editors.

The server defaults to readonly collaboration mode. Omit --project-id to let
external agents list and choose among all projects. Project-scoped tools can
still receive a project_id/id argument when needed.

Environment variables:
    DATABASE_URL — override the database connection string.
    MOSHU_HOME  — override the data directory (default: %LOCALAPPDATA%\\Moshu).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# ── Path setup ───────────────────────────────────────────────────────────
# Add backend/ to sys.path so `app.*` imports resolve.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_SCRIPT_DIR)
_BACKEND_DIR = os.path.join(_ROOT_DIR, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def _configure_stdio_utf8() -> None:
    """Prefer UTF-8 stdio for Windows MCP clients."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _app_home() -> Path:
    env_home = os.environ.get("MOSHU_HOME") or os.environ.get("NOVEL_AGENT_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()

    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home()
    current = base / "Moshu"
    legacy = base / "NovelWritingAgent"
    legacy_dot = Path.home() / ".NovelWritingAgent"
    for legacy_dir in (legacy, legacy_dot):
        legacy_db = legacy_dir / "novel_agent.db"
        current_db = current / "novel_agent.db"
        if legacy_db.exists() and legacy_db.stat().st_size > 0:
            if not current_db.exists() or current_db.stat().st_size < legacy_db.stat().st_size:
                return legacy_dir
    return current


def _prepare_data_environment() -> Path:
    home = _app_home()
    home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MOSHU_HOME", str(home))
    os.environ.setdefault("MOSHU_KEY_FILE", str(home / ".crypto_key"))
    os.environ.setdefault("NOVEL_AGENT_HOME", str(home))
    os.environ.setdefault("NOVEL_AGENT_KEY_FILE", str(home / ".crypto_key"))
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{(home / 'novel_agent.db').as_posix()}")
    return home


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="moshu-mcp-server",
        description="Moshu MCP Server — exposes Moshu workspace tools over stdio.",
    )
    parser.add_argument(
        "--project-id",
        default="",
        help="Optional default project ID for tool execution. Omit it for global project browsing; pass project_id/id in individual tool calls when needed.",
    )
    parser.add_argument(
        "--permission-pack",
        default=os.environ.get("MOSHU_MCP_PERMISSION_PACK", "auto"),
        choices=[
            "auto",
            "readonly_collaboration",
            "draft_generation",
            "project_writing",
            "project_management",
            "internal_llm",
            "trusted_local_maintenance",
            "cataloging_worker",
        ],
        help="MCP permission pack to expose. 'auto' resolves from global/project settings. Fixed packs bypass UI settings.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging to stderr.",
    )
    args = parser.parse_args()
    _configure_stdio_utf8()

    # ── Logging ──────────────────────────────────────────────────────────
    import logging
    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="[moshu-mcp] %(levelname)s %(name)s: %(message)s",
    )

    # ── Database setup ───────────────────────────────────────────────────
    _prepare_data_environment()
    from app.database.backup import backup_sqlite_database
    from app.database.migrations import ensure_runtime_schema, runtime_schema_needs_sync
    from app.database.models import Base
    from app.database.session import SessionLocal, engine

    if runtime_schema_needs_sync(engine):
        from app.core.config import get_settings

        backup_sqlite_database(get_settings().database_url, reason="pre-mcp-schema-sync")
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema(engine)
    db = SessionLocal()

    # ── MCP server ───────────────────────────────────────────────────────
    from app.mcp.server import serve_stdio

    try:
        serve_stdio(
            db=db,
            project_id=args.project_id,
            permission_pack=args.permission_pack,
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
