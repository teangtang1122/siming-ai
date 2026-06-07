"""MCP permission filter.

Enforces which tools are exposed to MCP clients based on permission tiers.
The filter is applied at both tools/list time and tools/call time.

Permission tiers:
  - readonly:   read, analysis, web, and memory-read tools
  - draft:      generator tools (no DB writes) — not enabled in v1
  - write_confirmed: database-mutating tools — not enabled in v1
"""
from __future__ import annotations

import re
from typing import Any

from backend.app.services.workspace.registry import ToolDef

# ── Tier assignment by tool_type ─────────────────────────────────────────

_TIER_MAP: dict[str, str] = {
    "read": "readonly",
    "analysis": "readonly",
    "web": "readonly",
    "memory": "readonly",   # recall / list_memories only; write memory is draft
    "generator": "draft",
    "write": "write_confirmed",
    "scheduler": "write_confirmed",
}

# Specific tools that belong to readonly even though their type maps higher,
# or that belong to a higher tier even though their type is read.
_TIER_OVERRIDES: dict[str, str] = {
    "remember": "draft",
    "forget": "write_confirmed",
}

# ── Secret-related deny patterns ─────────────────────────────────────────

_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"api_key",
        r"secret",
        r"credential",
        r"token",
        r"password",
    ]
]


def get_tier(tool_def: ToolDef) -> str:
    """Return the permission tier for a tool."""
    if tool_def.name in _TIER_OVERRIDES:
        return _TIER_OVERRIDES[tool_def.name]
    return _TIER_MAP.get(tool_def.tool_type, "write_confirmed")


def is_secret_tool(name: str) -> bool:
    """Return True if the tool name matches a secret-management pattern."""
    return any(p.search(name) for p in _SECRET_PATTERNS)


def is_allowed(
    tool_def: ToolDef,
    *,
    allowed_tiers: set[str] | None = None,
) -> bool:
    """Return True if the tool is allowed under the given tier set.

    Args:
        tool_def: The tool to check.
        allowed_tiers: Set of tier names to allow. Defaults to {"readonly"}.
    """
    if allowed_tiers is None:
        allowed_tiers = {"readonly"}

    # Secret tools are always denied regardless of tier.
    if is_secret_tool(tool_def.name):
        return False

    return get_tier(tool_def) in allowed_tiers


def filter_tools(
    tool_defs: list[ToolDef],
    *,
    allowed_tiers: set[str] | None = None,
) -> list[ToolDef]:
    """Return only the tools allowed under the given tier set."""
    return [td for td in tool_defs if is_allowed(td, allowed_tiers=allowed_tiers)]
