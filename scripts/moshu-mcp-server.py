#!/usr/bin/env python3
"""Moshu MCP Server — stdio entrypoint for MCP clients.

Usage:
    python scripts/moshu-mcp-server.py [--project-id ID]

This script starts a stdio-based MCP server that exposes Moshu workspace
tools to MCP clients such as Claude Desktop, Cursor, and other editors.

The server defaults to readonly mode. Only read/analysis tools are exposed.

Environment variables:
    DATABASE_URL — override the database connection string.
    MOSHU_HOME  — override the data directory (default: %LOCALAPPDATA%\\Moshu).
"""
from __future__ import annotations

import argparse
import os
import sys

# ── Path setup ───────────────────────────────────────────────────────────
# Add backend/ to sys.path so `app.*` imports resolve.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_SCRIPT_DIR)
_BACKEND_DIR = os.path.join(_ROOT_DIR, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="moshu-mcp-server",
        description="Moshu MCP Server — exposes Moshu workspace tools over stdio.",
    )
    parser.add_argument(
        "--project-id",
        default="",
        help="Default project ID for tool execution. If omitted, tools that require a project will return an error unless the client provides one.",
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
        format="[moshu-mcp] %(levelname)s %(name)s: %(message)s",
    )

    # ── Database setup ───────────────────────────────────────────────────
    from app.database.session import SessionLocal
    db = SessionLocal()

    # ── MCP server ───────────────────────────────────────────────────────
    from app.mcp.server import serve_stdio

    try:
        serve_stdio(
            db=db,
            project_id=args.project_id,
            allowed_tiers={"readonly"},
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
