"""JSON Schema function definitions for all workspace tools — backward-compatible layer.

Delegates to the central registry. New code should import from registry directly.
"""
from __future__ import annotations

from .registry import registry


# ── Aggregated lists (derived from registry) ────────────────────────────

# Search/read/generate/analyze tools — allowed during information-gathering rounds
SEARCH_TOOL_SCHEMAS: list[dict] = registry.get_schemas(
    tool_types={"read", "analysis", "web", "memory", "generator"},
)

# Write tools — only allowed when the assistant is ready to commit changes
WRITE_TOOL_SCHEMAS: list[dict] = registry.get_schemas(
    tool_types={"write"},
)

ALL_TOOL_SCHEMAS: list[dict] = registry.get_schemas()

# Tool-name sets for quick classification
SEARCH_TOOL_NAMES: set[str] = registry.get_names_by_type("read") | registry.get_names_by_type("analysis") | registry.get_names_by_type("web") | registry.get_names_by_type("memory") | registry.get_names_by_type("generator")
WRITE_TOOL_NAMES: set[str] = registry.get_names_by_type("write")


def build_tool_schemas(*, search_only: bool = False) -> list[dict]:
    """Return the appropriate tool schema list.

    Args:
        search_only: If True, return only search/read tools (for info-gathering rounds).
                     If False, return all tools.

    Used by the agentic loop to expose different tools at different phases.
    """
    if search_only:
        return list(SEARCH_TOOL_SCHEMAS)
    return list(ALL_TOOL_SCHEMAS)
