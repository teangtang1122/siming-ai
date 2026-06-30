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
    "internal_llm",
    "trusted_local_maintenance",
]

DEFAULT_PACKS = [
    "readonly_collaboration",
    "project_writing",
    "project_management",
    "trusted_local_maintenance",
]

PACK_INCLUDES = {
    "readonly_collaboration": ["readonly_collaboration"],
    "draft_generation": ["readonly_collaboration", "draft_generation"],
    "project_writing": ["readonly_collaboration", "project_writing"],
    "project_management": [
        "readonly_collaboration",
        "project_writing",
        "project_management",
    ],
    "internal_llm": [
        "readonly_collaboration",
        "project_writing",
        "project_management",
        "internal_llm",
    ],
    "trusted_local_maintenance": [
        "readonly_collaboration",
        "project_writing",
        "project_management",
        "trusted_local_maintenance",
    ],
}


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
            enabled_packs = _normalize_enabled_packs(project_settings.enabled_packs)
            effective = _highest_pack(enabled_packs)
            return {
                "effective_pack": effective,
                "source": "project_override",
                "cli_override": False,
                "enabled_packs": enabled_packs,
                "warnings": warnings,
            }

    # 3. Check global settings
    global_settings = db.query(ExternalAgentGlobalSettings).first()
    if global_settings:
        if global_settings.mcp_permission_source == "cli_override":
            warnings.append("Global settings indicate CLI override is active.")

        if global_settings.enabled_packs:
            enabled_packs = _normalize_enabled_packs(global_settings.enabled_packs)
            effective = _highest_pack(enabled_packs)
            return {
                "effective_pack": effective,
                "source": "global_settings",
                "cli_override": global_settings.mcp_permission_source == "cli_override",
                "enabled_packs": enabled_packs,
                "warnings": warnings,
            }

    # 4. Default: trusted local, excluding internal model-spend tools.
    return {
        "effective_pack": "trusted_local_maintenance",
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


def _normalize_enabled_packs(enabled_packs: list[str] | None) -> list[str]:
    """Promote empty/legacy readonly-only settings to the 2.1 local-trust default."""
    packs = [str(pack) for pack in (enabled_packs or []) if pack]
    if not packs or packs == ["readonly_collaboration"]:
        return DEFAULT_PACKS
    return packs


def _packs_up_to(pack: str) -> list[str]:
    """Return the packs implied by a selected pack.

    The permission model is intentionally non-linear: project management does
    not imply internal LLM access, and trusted maintenance does not imply it
    either. ``internal_llm`` must be selected explicitly when users want to
    spend the model API configured inside Siming.
    """
    return PACK_INCLUDES.get(pack, [pack])
