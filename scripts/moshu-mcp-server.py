#!/usr/bin/env python3
"""Siming MCP Server — stdio entrypoint for MCP clients.

Usage:
    python scripts/moshu-mcp-server.py [--project-id ID] [--permission-pack PACK]

This script starts a stdio-based MCP server that exposes Siming workspace
tools to MCP clients such as Claude Desktop, Cursor, and other editors.

The server defaults to readonly collaboration mode. Omit --project-id to let
external agents list and choose among all projects. Project-scoped tools can
still receive a project_id/id argument when needed.

Environment variables:
    DATABASE_URL — override the database connection string.
    SIMING_HOME — override the data directory (default: %LOCALAPPDATA%\\Siming).
    MOSHU_HOME  — legacy override, still supported.
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
    env_home = os.environ.get("SIMING_HOME") or os.environ.get("MOSHU_HOME") or os.environ.get("NOVEL_AGENT_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()

    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home()
    current = base / "Siming"
    legacy_dirs = [base / "Moshu", base / "NovelWritingAgent", Path.home() / ".Moshu", Path.home() / ".NovelWritingAgent"]
    for legacy_dir in legacy_dirs:
        legacy_db = legacy_dir / "novel_agent.db"
        current_db = current / "novel_agent.db"
        if legacy_db.exists() and legacy_db.stat().st_size > 0:
            if not current_db.exists() or current_db.stat().st_size < legacy_db.stat().st_size:
                return legacy_dir
    return current


def _prepare_data_environment() -> Path:
    home = _app_home()
    home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("SIMING_HOME", str(home))
    os.environ.setdefault("SIMING_KEY_FILE", str(home / ".crypto_key"))
    os.environ.setdefault("MOSHU_HOME", str(home))
    os.environ.setdefault("MOSHU_KEY_FILE", str(home / ".crypto_key"))
    os.environ.setdefault("NOVEL_AGENT_HOME", str(home))
    os.environ.setdefault("NOVEL_AGENT_KEY_FILE", str(home / ".crypto_key"))
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{(home / 'novel_agent.db').as_posix()}")
    return home


def main() -> None:
    # Help/errors are part of the CLI surface too. Configure before argparse
    # can exit, otherwise Windows emits GBK while MCP clients expect UTF-8.
    _configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        prog="moshu-mcp-server",
        description="Siming MCP Server — exposes Siming workspace tools over stdio.",
    )
    parser.add_argument(
        "--project-id",
        default="",
        help="Optional default project ID for tool execution. Omit it for global project browsing; pass project_id/id in individual tool calls when needed.",
    )
    parser.add_argument(
        "--permission-pack",
        default=os.environ.get("SIMING_MCP_PERMISSION_PACK") or os.environ.get("MOSHU_MCP_PERMISSION_PACK", "auto"),
        choices=[
            "auto",
            "readonly_collaboration",
            "draft_generation",
            "project_writing",
            "project_management",
            "internal_llm",
            "trusted_local_maintenance",
            "cataloging_worker",
            "creation_session",
        ],
        help="MCP permission pack to expose. 'auto' resolves from global/project settings. Fixed packs bypass UI settings.",
    )
    parser.add_argument(
        "--creation-session-id",
        default="",
        help="Required one-session boundary when --permission-pack creation_session is used.",
    )
    parser.add_argument(
        "--tool-category-state-file",
        default="",
        help="Process-scoped model-selected tool category state for one managed Agent turn.",
    )
    parser.add_argument(
        "--direct-mcp-lease-token",
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging to stderr.",
    )
    args = parser.parse_args()

    # ── Logging ──────────────────────────────────────────────────────────
    import logging
    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="[siming-mcp] %(levelname)s %(name)s: %(message)s",
    )

    # ── Database setup ───────────────────────────────────────────────────
    _prepare_data_environment()
    from app.database.bootstrap import bootstrap_database
    from app.database.session import SessionLocal, engine

    # An MCP server usually starts beside the desktop process.  When the schema
    # is already current, keep this check read-only so it cannot contend with
    # an in-flight desktop write transaction.
    bootstrap = bootstrap_database(engine, refresh_current_metadata=False)
    if bootstrap.read_only:
        raise RuntimeError(
            "MCP cannot start while the database is in read-only recovery mode: "
            + bootstrap.message
        )
    from app.bootstrap.composition import configure_application_services

    configure_application_services()
    db = SessionLocal()

    # ── MCP server ───────────────────────────────────────────────────────
    from app.mcp.server import serve_stdio

    try:
        serve_stdio(
            db=db,
            project_id=args.project_id,
            permission_pack=args.permission_pack,
            creation_session_id=args.creation_session_id,
            tool_category_state_file=args.tool_category_state_file,
            direct_mcp_lease_token=args.direct_mcp_lease_token,
        )
    finally:
        db.close()
        from app.services.local_runtime import get_runtime_manager

        get_runtime_manager().stop()


if __name__ == "__main__":
    main()
