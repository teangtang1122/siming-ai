"""External Agent permissions service.

Resolves effective permissions from global settings, project overrides,
and CLI overrides.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Pack hierarchy
PACK_ORDER = [
    "readonly_collaboration",
    "draft_generation",
    "project_writing",
    "project_management",
    "trusted_local_maintenance",
]

DEFAULT_PACKS = ["readonly_collaboration"]


def resolve_effective_pack(
    db: Session,
    project_id: str | None = None,
    cli_pack: str | None = None,
) -> dict[str, Any]:
    """Resolve the effective permission pack.

    Priority: CLI override > project override > global settings > default.

    Returns:
        dict with effective_pack, source, cli_override, warnings.
    """
    from app.database.models import ExternalAgentGlobalSettings, ExternalAgentSettings

    warnings: list[str] = []

    # 1. Check CLI override
    if cli_pack and cli_pack != "auto":
        return {
            "effective_pack": cli_pack,
            "source": "cli_override",
            "cli_override": True,
            "enabled_packs": _packs_up_to(cli_pack),
            "warnings": [f"CLI override active: {cli_pack}. UI settings are bypassed."],
        }

    # 2. Check project override
    if project_id:
        project_settings = db.query(ExternalAgentSettings).filter(
            ExternalAgentSettings.project_id == project_id,
        ).first()
        if project_settings and project_settings.enabled_packs:
            effective = _highest_pack(project_settings.enabled_packs)
            return {
                "effective_pack": effective,
                "source": "project_override",
                "cli_override": False,
                "enabled_packs": project_settings.enabled_packs,
                "warnings": warnings,
            }

    # 3. Check global settings
    global_settings = db.query(ExternalAgentGlobalSettings).first()
    if global_settings:
        if global_settings.mcp_permission_source == "cli_override":
            warnings.append("Global settings indicate CLI override is active.")

        if global_settings.enabled_packs:
            effective = _highest_pack(global_settings.enabled_packs)
            return {
                "effective_pack": effective,
                "source": "global_settings",
                "cli_override": global_settings.mcp_permission_source == "cli_override",
                "enabled_packs": global_settings.enabled_packs,
                "warnings": warnings,
            }

    # 4. Default
    return {
        "effective_pack": "readonly_collaboration",
        "source": "default",
        "cli_override": False,
        "enabled_packs": DEFAULT_PACKS,
        "warnings": warnings,
    }


def _highest_pack(enabled_packs: list[str]) -> str:
    """Return the highest-level pack from the enabled list."""
    max_level = 0
    for pack in enabled_packs:
        try:
            level = PACK_ORDER.index(pack)
            max_level = max(max_level, level)
        except ValueError:
            continue
    return PACK_ORDER[max_level]


def _packs_up_to(pack: str) -> list[str]:
    """Return all packs up to and including the given pack."""
    try:
        idx = PACK_ORDER.index(pack)
        return PACK_ORDER[: idx + 1]
    except ValueError:
        return [pack]
