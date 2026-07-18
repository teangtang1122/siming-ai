"""Compatibility boundary for environment names used before Siming 3.0."""

from __future__ import annotations

import os
from collections.abc import MutableMapping

LEGACY_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "SIMING_HOME": ("MOSHU_HOME", "NOVEL_AGENT_HOME"),
    "SIMING_KEY": ("MOSHU_KEY", "NOVEL_AGENT_KEY"),
    "SIMING_KEY_FILE": ("MOSHU_KEY_FILE", "NOVEL_AGENT_KEY_FILE"),
    "SIMING_CONTENT_ROOT": ("MOSHU_CONTENT_ROOT",),
    "SIMING_MODEL_ROOT": ("MOSHU_MODEL_ROOT",),
    "SIMING_REDIS_URL": ("MOSHU_REDIS_URL",),
    "SIMING_DISABLE_AUTO_MCP_SETUP": ("MOSHU_DISABLE_AUTO_MCP_SETUP",),
    "SIMING_UPDATE_CHANNEL": ("MOSHU_UPDATE_CHANNEL", "NOVEL_AGENT_UPDATE_CHANNEL"),
    "SIMING_GITHUB_TOKEN": ("MOSHU_GITHUB_TOKEN", "NOVEL_AGENT_GITHUB_TOKEN"),
    "SIMING_DISABLE_UPDATE": ("MOSHU_DISABLE_UPDATE", "NOVEL_AGENT_DISABLE_UPDATE"),
    "SIMING_UPDATE_MANIFEST_URL": (
        "MOSHU_UPDATE_MANIFEST_URL",
        "NOVEL_AGENT_UPDATE_MANIFEST_URL",
    ),
    "SIMING_UPDATE_REPO": ("MOSHU_UPDATE_REPO", "NOVEL_AGENT_UPDATE_REPO"),
    "SIMING_MANAGED_AGENT_KIND": ("MOSHU_MANAGED_AGENT_KIND",),
    "SIMING_MODEL_MANIFEST_URL": ("MOSHU_MODEL_MANIFEST_URL",),
    "SIMING_MODEL_MANIFEST_PUBLIC_KEY": ("MOSHU_MODEL_MANIFEST_PUBLIC_KEY",),
}


def compatible_env_names(primary: str) -> tuple[str, ...]:
    """Return the canonical name followed by all accepted legacy aliases."""

    aliases = LEGACY_ENV_ALIASES.get(primary)
    if aliases is not None:
        return (primary, *aliases)
    if primary.startswith("SIMING_MANAGED_"):
        return (primary, primary.replace("SIMING_", "MOSHU_", 1))
    return (primary,)


def get_compatible_env(primary: str, *fallback_names: str, default: str = "") -> str:
    """Read a canonical environment value with compatibility fallbacks."""

    for name in (*compatible_env_names(primary), *fallback_names):
        value = os.environ.get(name)
        if value is not None and value != "":
            return value
    return default


def compatible_env_enabled(primary: str) -> bool:
    return get_compatible_env(primary).strip() == "1"


def set_compatible_env(
    primary: str,
    value: str,
    *,
    target: MutableMapping[str, str] | None = None,
) -> None:
    """Publish a canonical value and aliases needed by older child processes."""

    environment = os.environ if target is None else target
    for name in compatible_env_names(primary):
        environment[name] = value


def compatible_env_prefixes() -> tuple[str, ...]:
    """Return canonical and legacy prefixes for grouped managed-agent values."""

    return ("SIMING", "MOSHU")


__all__ = [
    "compatible_env_enabled",
    "compatible_env_names",
    "compatible_env_prefixes",
    "get_compatible_env",
    "set_compatible_env",
]
