"""SQLAlchemy External Agent permission settings adapter."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..application.external_agent_settings import (
    DEFAULT_ENABLED_PACKS,
    DEFAULT_REQUIRE_CONFIRMATION_FOR_DESTRUCTIVE,
    DEFAULT_REQUIRE_CONFIRMATION_FOR_WRITES,
    DEFAULT_TRUSTED_LOCAL_CLIENTS,
    DEFAULT_TRUSTED_LOCAL_ENABLED,
)
from .models import ExternalAgentGlobalSettings, ExternalAgentSettings

PACK_ORDER = [
    "readonly_collaboration",
    "draft_generation",
    "project_writing",
    "project_management",
    "internal_llm",
    "trusted_local_maintenance",
]


def _normalize_enabled_packs(enabled_packs: list[str] | None) -> list[str]:
    packs = [str(pack) for pack in (enabled_packs or []) if pack]
    if not packs or packs == ["readonly_collaboration"]:
        return list(DEFAULT_ENABLED_PACKS)
    return packs


def _highest_pack(enabled_packs: list[str]) -> str:
    max_level = 0
    for pack in enabled_packs:
        try:
            max_level = max(max_level, PACK_ORDER.index(pack))
        except ValueError:
            continue
    return PACK_ORDER[max_level]


def _global_data(settings: ExternalAgentGlobalSettings | None) -> dict[str, Any]:
    return {
        **({"id": settings.id} if settings else {}),
        "enabled_packs": (
            settings.enabled_packs if settings and settings.enabled_packs else DEFAULT_ENABLED_PACKS
        ),
        "trusted_local_enabled": (
            settings.trusted_local_enabled
            if settings and settings.trusted_local_enabled is not None
            else DEFAULT_TRUSTED_LOCAL_ENABLED
        ),
        "trusted_local_clients": (
            settings.trusted_local_clients
            if settings and settings.trusted_local_clients
            else DEFAULT_TRUSTED_LOCAL_CLIENTS
        ),
        "require_confirmation_for_writes": (
            settings.require_confirmation_for_writes
            if settings and settings.require_confirmation_for_writes is not None
            else DEFAULT_REQUIRE_CONFIRMATION_FOR_WRITES
        ),
        "require_confirmation_for_destructive": (
            settings.require_confirmation_for_destructive
            if settings and settings.require_confirmation_for_destructive is not None
            else DEFAULT_REQUIRE_CONFIRMATION_FOR_DESTRUCTIVE
        ),
        "mcp_permission_source": (
            settings.mcp_permission_source if settings else "global_settings"
        )
        or "global_settings",
    }


def _project_data(
    project_id: str,
    settings: ExternalAgentSettings | None,
) -> dict[str, Any]:
    return {
        **({"id": settings.id} if settings else {}),
        "project_id": project_id,
        "enabled_packs": (
            settings.enabled_packs if settings and settings.enabled_packs else DEFAULT_ENABLED_PACKS
        ),
        "trusted_local_enabled": (
            settings.trusted_local_enabled
            if settings and settings.trusted_local_enabled is not None
            else DEFAULT_TRUSTED_LOCAL_ENABLED
        ),
        "trusted_local_clients": (
            settings.trusted_local_clients
            if settings and settings.trusted_local_clients
            else DEFAULT_TRUSTED_LOCAL_CLIENTS
        ),
        "require_confirmation_for_writes": (
            settings.require_confirmation_for_writes
            if settings and settings.require_confirmation_for_writes is not None
            else DEFAULT_REQUIRE_CONFIRMATION_FOR_WRITES
        ),
        "require_confirmation_for_destructive": (
            settings.require_confirmation_for_destructive
            if settings and settings.require_confirmation_for_destructive is not None
            else DEFAULT_REQUIRE_CONFIRMATION_FOR_DESTRUCTIVE
        ),
    }


class SqlAlchemyExternalAgentSettingsStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _global(self) -> ExternalAgentGlobalSettings | None:
        return self._session.query(ExternalAgentGlobalSettings).first()

    def _project(self, project_id: str) -> ExternalAgentSettings | None:
        return (
            self._session.query(ExternalAgentSettings)
            .filter(ExternalAgentSettings.project_id == project_id)
            .first()
        )

    def get_global(self) -> dict[str, Any]:
        return _global_data(self._global())

    def update_global(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = self._global()
        if not settings:
            settings = ExternalAgentGlobalSettings()
            self._session.add(settings)
        for field, value in payload.items():
            if value is not None and hasattr(settings, field):
                setattr(settings, field, value)
        settings.updated_at = datetime.utcnow()
        self._session.flush()
        return _global_data(settings)

    def get_project(self, project_id: str) -> dict[str, Any]:
        return _project_data(project_id, self._project(project_id))

    def update_project(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        settings = self._project(project_id)
        if not settings:
            settings = ExternalAgentSettings(project_id=project_id)
            self._session.add(settings)
        for field, value in payload.items():
            if value is not None and hasattr(settings, field):
                setattr(settings, field, value)
        settings.updated_at = datetime.utcnow()
        self._session.flush()
        return _project_data(project_id, settings)

    def effective_permissions(self, project_id: str | None = None) -> dict[str, Any]:
        global_settings = self._global()
        project_settings = self._project(project_id) if project_id else None
        warnings: list[str] = []

        if project_settings and project_settings.enabled_packs:
            enabled = _normalize_enabled_packs(project_settings.enabled_packs)
            source = "project_override"
            cli_override = False
        elif global_settings and global_settings.enabled_packs:
            enabled = _normalize_enabled_packs(global_settings.enabled_packs)
            source = "global_settings"
            cli_override = global_settings.mcp_permission_source == "cli_override"
            if cli_override:
                warnings.append("Global settings indicate CLI override is active.")
        else:
            enabled = list(DEFAULT_ENABLED_PACKS)
            source = "default"
            cli_override = False

        return {
            "global_enabled_packs": (
                global_settings.enabled_packs
                if global_settings and global_settings.enabled_packs
                else list(DEFAULT_ENABLED_PACKS)
            ),
            "project_enabled_packs": (
                project_settings.enabled_packs
                if project_settings and project_settings.enabled_packs
                else None
            ),
            "effective_pack": _highest_pack(enabled),
            "source": source,
            "cli_override": cli_override,
            "enabled_packs": enabled,
            "warnings": warnings,
        }


__all__ = ["SqlAlchemyExternalAgentSettingsStore"]
