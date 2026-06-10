"""API router for global external Agent settings (no project_id required)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.response import ApiResponse
from ..database.session import get_db
from ..schemas.external_agent_settings import (
    ExternalAgentGlobalSettingsUpdate,
    DEFAULT_ENABLED_PACKS,
)

router = APIRouter(prefix="/external-agent", tags=["external-agent-global"])


@router.get("/settings")
def get_global_settings(db: Session = Depends(get_db)):
    """Get global external Agent permission settings."""
    from ..database.models import ExternalAgentGlobalSettings

    settings = db.query(ExternalAgentGlobalSettings).first()
    if not settings:
        return ApiResponse.success(data={
            "enabled_packs": DEFAULT_ENABLED_PACKS,
            "trusted_local_enabled": False,
            "trusted_local_clients": [],
            "require_confirmation_for_writes": True,
            "require_confirmation_for_destructive": True,
            "mcp_permission_source": "global_settings",
        })

    return ApiResponse.success(data={
        "id": settings.id,
        "enabled_packs": settings.enabled_packs or DEFAULT_ENABLED_PACKS,
        "trusted_local_enabled": settings.trusted_local_enabled or False,
        "trusted_local_clients": settings.trusted_local_clients or [],
        "require_confirmation_for_writes": settings.require_confirmation_for_writes if settings.require_confirmation_for_writes is not None else True,
        "require_confirmation_for_destructive": settings.require_confirmation_for_destructive if settings.require_confirmation_for_destructive is not None else True,
        "mcp_permission_source": settings.mcp_permission_source or "global_settings",
    })


@router.put("/settings")
def update_global_settings(
    body: ExternalAgentGlobalSettingsUpdate,
    db: Session = Depends(get_db),
):
    """Update global external Agent permission settings."""
    from ..database.models import ExternalAgentGlobalSettings

    settings = db.query(ExternalAgentGlobalSettings).first()
    if not settings:
        settings = ExternalAgentGlobalSettings()
        db.add(settings)

    if body.enabled_packs is not None:
        settings.enabled_packs = body.enabled_packs
    if body.trusted_local_enabled is not None:
        settings.trusted_local_enabled = body.trusted_local_enabled
    if body.trusted_local_clients is not None:
        settings.trusted_local_clients = body.trusted_local_clients
    if body.require_confirmation_for_writes is not None:
        settings.require_confirmation_for_writes = body.require_confirmation_for_writes
    if body.require_confirmation_for_destructive is not None:
        settings.require_confirmation_for_destructive = body.require_confirmation_for_destructive

    settings.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(settings)

    return ApiResponse.success(data={
        "id": settings.id,
        "enabled_packs": settings.enabled_packs or DEFAULT_ENABLED_PACKS,
        "trusted_local_enabled": settings.trusted_local_enabled or False,
        "trusted_local_clients": settings.trusted_local_clients or [],
        "require_confirmation_for_writes": settings.require_confirmation_for_writes if settings.require_confirmation_for_writes is not None else True,
        "require_confirmation_for_destructive": settings.require_confirmation_for_destructive if settings.require_confirmation_for_destructive is not None else True,
        "mcp_permission_source": settings.mcp_permission_source or "global_settings",
    }, message="Global settings updated")


@router.get("/effective-permissions")
def get_effective_permissions(
    project_id: str | None = None,
    db: Session = Depends(get_db),
):
    """Get effective permissions (global + optional project override)."""
    from ..database.models import ExternalAgentGlobalSettings, ExternalAgentSettings
    from ..services.external_agent.permissions import resolve_effective_pack

    result = resolve_effective_pack(db, project_id=project_id)
    return ApiResponse.success(data=result)
