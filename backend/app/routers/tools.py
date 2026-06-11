"""API router for tool catalog — exposes ToolRegistry metadata."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.response import ApiResponse
from ..core.db_helpers import get_project_or_404
from ..database.session import get_db
from ..services.workspace.registry import registry

router = APIRouter(tags=["tools"])


@router.get("/tools/catalog")
def get_tool_catalog():
    """List all registered tools with metadata.

    Returns the full tool catalog from the single ToolRegistry source.
    Adding a new tool to the registry automatically makes it appear here.
    """
    tools = registry.list_for_frontend()
    return ApiResponse.success(data={
        "items": tools,
        "total": len(tools),
    })


@router.get("/projects/{project_id}/tools/exposed")
def get_exposed_tools(
    project_id: str,
    db: Session = Depends(get_db),
):
    """List tools exposed to external Agent for a project.

    Returns which tools are available based on the project's
    external Agent permission settings.
    """
    get_project_or_404(db, project_id)

    from ..services.external_agent.permissions import resolve_effective_pack

    permission = resolve_effective_pack(db, project_id=project_id)
    enabled_packs = permission["enabled_packs"]
    effective_pack = permission["effective_pack"]

    # Get tools for this pack
    allowed_tools = registry.list_for_mcp(permission_pack=effective_pack)
    allowed_names = {td.name for td in allowed_tools}

    # Build response with all tools and their exposure status
    all_tools = registry.list_for_frontend()
    result = []
    for tool in all_tools:
        exposed = tool["name"] in allowed_names
        denied_reason = None
        if not exposed:
            if not tool["expose_to_mcp"]:
                denied_reason = "internal_only"
            elif tool["mcp_permission_pack"] not in enabled_packs:
                denied_reason = f"pack_disabled:{tool['mcp_permission_pack']}"
            else:
                denied_reason = "pack_level_insufficient"

        result.append({
            **tool,
            "exposed": exposed,
            "denied_reason": denied_reason,
        })

    return ApiResponse.success(data={
        "items": result,
        "total": len(result),
        "effective_pack": effective_pack,
        "enabled_packs": enabled_packs,
        "source": permission["source"],
        "warnings": permission["warnings"],
    })
