"""External Agent permission settings application port."""
from __future__ import annotations

from typing import Any, Protocol

DEFAULT_ENABLED_PACKS = [
    "readonly_collaboration",
    "project_writing",
    "project_management",
    "trusted_local_maintenance",
]
DEFAULT_TRUSTED_LOCAL_ENABLED = True
DEFAULT_TRUSTED_LOCAL_CLIENTS = [
    "claude-code",
    "codex",
    "opencode",
    "mimocode",
    "cursor",
    "trae",
    "kilocode",
    "qwen-code",
    "hermes",
    "openclaw",
]
DEFAULT_REQUIRE_CONFIRMATION_FOR_WRITES = False
DEFAULT_REQUIRE_CONFIRMATION_FOR_DESTRUCTIVE = False


class ExternalAgentSettingsStore(Protocol):
    def get_global(self) -> dict[str, Any]: ...

    def update_global(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def get_project(self, project_id: str) -> dict[str, Any]: ...

    def update_project(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...

    def effective_permissions(self, project_id: str | None = None) -> dict[str, Any]: ...


__all__ = [
    "DEFAULT_ENABLED_PACKS",
    "DEFAULT_REQUIRE_CONFIRMATION_FOR_DESTRUCTIVE",
    "DEFAULT_REQUIRE_CONFIRMATION_FOR_WRITES",
    "DEFAULT_TRUSTED_LOCAL_CLIENTS",
    "DEFAULT_TRUSTED_LOCAL_ENABLED",
    "ExternalAgentSettingsStore",
]
