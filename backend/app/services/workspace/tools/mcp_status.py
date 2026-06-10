"""MCP status tools — report MCP permission status.

These tools are readonly and exposed to all permission packs.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session


async def get_mcp_permission_status(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Report current MCP permission status.

    Shows effective permission pack, source (global/project/CLI),
    and whether a CLI override is active.
    """
    from app.services.external_agent.permissions import resolve_effective_pack

    result = resolve_effective_pack(db, project_id=project_id or None)

    return {
        "tool": "get_mcp_permission_status",
        "status": "ok",
        "detail": f"Effective pack: {result['effective_pack']} (source: {result['source']})",
        "data": {
            "effective_pack": result["effective_pack"],
            "source": result["source"],
            "cli_override": result["cli_override"],
            "enabled_packs": result["enabled_packs"],
            "warnings": result["warnings"],
        },
    }
