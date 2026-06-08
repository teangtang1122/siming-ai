"""API router for MCP client (external server) management."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.response import ApiResponse
from ..core.db_helpers import get_project_or_404
from ..database.session import get_db
from ..database.models import McpServerConfig
from ..schemas.mcp import (
    McpServerConfigCreate,
    McpServerConfigRead,
    McpServerConfigUpdate,
)

router = APIRouter(prefix="/projects/{project_id}/mcp-servers", tags=["mcp"])


def _config_to_read(config: McpServerConfig) -> McpServerConfigRead:
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
    configs = (
        db.query(McpServerConfig)
        .filter(McpServerConfig.project_id == project_id)
        .order_by(McpServerConfig.created_at.desc())
        .all()
    )
    return ApiResponse.success(data={
        "items": [_config_to_read(c).model_dump() for c in configs],
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

    config = McpServerConfig(
        project_id=project_id,
        name=body.name,
        transport=body.transport,
        command=body.command,
        url=body.url,
        enabled=body.enabled,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return ApiResponse.success(data=_config_to_read(config).model_dump(), message="MCP server config created")


@router.get("/{config_id}")
def get_mcp_server(
    project_id: str,
    config_id: str,
    db: Session = Depends(get_db),
):
    """Get a single MCP server config."""
    get_project_or_404(db, project_id)
    config = db.query(McpServerConfig).filter(
        McpServerConfig.project_id == project_id,
        McpServerConfig.id == config_id,
    ).first()
    if not config:
        raise HTTPException(status_code=404, message="MCP server config not found")
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
    config = db.query(McpServerConfig).filter(
        McpServerConfig.project_id == project_id,
        McpServerConfig.id == config_id,
    ).first()
    if not config:
        raise HTTPException(status_code=404, message="MCP server config not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(config, field, value)
    db.commit()
    db.refresh(config)
    return ApiResponse.success(data=_config_to_read(config).model_dump(), message="MCP server config updated")


@router.delete("/{config_id}")
def delete_mcp_server(
    project_id: str,
    config_id: str,
    db: Session = Depends(get_db),
):
    """Delete an MCP server config."""
    get_project_or_404(db, project_id)
    config = db.query(McpServerConfig).filter(
        McpServerConfig.project_id == project_id,
        McpServerConfig.id == config_id,
    ).first()
    if not config:
        raise HTTPException(status_code=404, message="MCP server config not found")

    db.delete(config)
    db.commit()
    return ApiResponse.success(message="MCP server config deleted")


@router.post("/{config_id}/test")
def test_mcp_server_connection(
    project_id: str,
    config_id: str,
    db: Session = Depends(get_db),
):
    """Test connection to an MCP server."""
    get_project_or_404(db, project_id)
    config = db.query(McpServerConfig).filter(
        McpServerConfig.project_id == project_id,
        McpServerConfig.id == config_id,
    ).first()
    if not config:
        raise HTTPException(status_code=404, message="MCP server config not found")

    # Connection test is deferred to Phase 5 implementation
    return ApiResponse.success(data={
        "status": config.status,
        "message": "Connection test not yet implemented",
    })
