"""API router for MCP client (external server) management."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.integrations.application.mcp_servers import get_mcp_server_configuration
from ..schemas.mcp import (
    McpServerConfigCreate,
    McpServerConfigRead,
    McpServerConfigUpdate,
)

router = APIRouter(prefix="/projects/{project_id}/mcp-servers", tags=["mcp"])


def _config_to_read(config: dict | Any) -> McpServerConfigRead:
    """Compatibility projection without coupling the router to an ORM class."""
    if isinstance(config, dict):
        return McpServerConfigRead(**config)
    return McpServerConfigRead(
        id=config.id,
        project_id=config.project_id,
        name=config.name,
        transport=config.transport,
        command=config.command,
        url=config.url,
        enabled=config.enabled,
        status=config.status,
        last_error=config.last_error,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


@router.get("")
def list_mcp_servers(
    project_id: str,
    db: Session = Depends(get_db),
):
    """List all MCP server configs for a project."""
    get_project_or_404(db, project_id)
    configs = get_mcp_server_configuration().list(db, project_id)
    return ApiResponse.success(data={
        "items": [_config_to_read(config).model_dump() for config in configs],
        "total": len(configs),
    })


@router.post("")
def create_mcp_server(
    project_id: str,
    body: McpServerConfigCreate,
    db: Session = Depends(get_db),
):
    """Create a new MCP server config."""
    get_project_or_404(db, project_id)

    config = get_mcp_server_configuration().create(
        db,
        project_id,
        body.model_dump(),
    )
    return ApiResponse.success(data=_config_to_read(config).model_dump(), message="MCP server config created")


@router.get("/{config_id}")
def get_mcp_server(
    project_id: str,
    config_id: str,
    db: Session = Depends(get_db),
):
    """Get a single MCP server config."""
    get_project_or_404(db, project_id)
    config = get_mcp_server_configuration().get(db, project_id, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="MCP server config not found")
    return ApiResponse.success(data=_config_to_read(config).model_dump())


@router.patch("/{config_id}")
def update_mcp_server(
    project_id: str,
    config_id: str,
    body: McpServerConfigUpdate,
    db: Session = Depends(get_db),
):
    """Update an MCP server config."""
    get_project_or_404(db, project_id)
    config = get_mcp_server_configuration().update(
        db,
        project_id,
        config_id,
        body.model_dump(exclude_unset=True),
    )
    if not config:
        raise HTTPException(status_code=404, detail="MCP server config not found")
    return ApiResponse.success(data=_config_to_read(config).model_dump(), message="MCP server config updated")


@router.delete("/{config_id}")
def delete_mcp_server(
    project_id: str,
    config_id: str,
    db: Session = Depends(get_db),
):
    """Delete an MCP server config."""
    get_project_or_404(db, project_id)
    if not get_mcp_server_configuration().delete(db, project_id, config_id):
        raise HTTPException(status_code=404, detail="MCP server config not found")
    return ApiResponse.success(message="MCP server config deleted")


@router.post("/{config_id}/test")
def test_mcp_server_connection(
    project_id: str,
    config_id: str,
    db: Session = Depends(get_db),
):
    """Test connection to an MCP server."""
    get_project_or_404(db, project_id)
    config = get_mcp_server_configuration().get(db, project_id, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="MCP server config not found")

    # Connection test is deferred to Phase 5 implementation
    return ApiResponse.success(data={
        "status": config["status"],
        "message": "Connection test not yet implemented",
    })
