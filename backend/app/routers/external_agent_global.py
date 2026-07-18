"""API router for global External Agent settings."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ..core.response import ApiResponse
from ..modules.integrations.application.external_agent_settings import (
    ExternalAgentSettingsStore,
)
from ..modules.integrations.interfaces.external_agent_dependencies import (
    get_external_agent_settings_store,
)
from ..schemas.external_agent_settings import ExternalAgentGlobalSettingsUpdate

router = APIRouter(prefix="/external-agent", tags=["external-agent-global"])


@router.get("/settings")
def get_global_settings(
    settings: Annotated[
        ExternalAgentSettingsStore,
        Depends(get_external_agent_settings_store),
    ],
):
    return ApiResponse.success(data=settings.get_global())


@router.put("/settings")
def update_global_settings(
    body: ExternalAgentGlobalSettingsUpdate,
    settings: Annotated[
        ExternalAgentSettingsStore,
        Depends(get_external_agent_settings_store),
    ],
):
    return ApiResponse.success(
        data=settings.update_global(body.model_dump(exclude_unset=True)),
        message="Global settings updated",
    )


@router.get("/effective-permissions")
def get_effective_permissions(
    settings: Annotated[
        ExternalAgentSettingsStore,
        Depends(get_external_agent_settings_store),
    ],
    project_id: str | None = None,
):
    result = settings.effective_permissions(project_id)
    return ApiResponse.success(
        data={
            "effective_pack": result["effective_pack"],
            "source": result["source"],
            "cli_override": result["cli_override"],
            "enabled_packs": result["enabled_packs"],
            "warnings": result["warnings"],
        }
    )


__all__ = [
    "get_effective_permissions",
    "get_global_settings",
    "router",
    "update_global_settings",
]
