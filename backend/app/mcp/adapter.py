"""MCP adapter — bridges ToolRegistry to MCP protocol.

Reads ToolDef entries from the existing registry singleton,
applies permission filtering, and converts to MCP schema format.
Does NOT modify the ToolRegistry.
"""
from __future__ import annotations

from typing import Any

from backend.app.services.workspace.registry import ToolDef, registry
from backend.app.mcp.schemas import McpTool, McpToolResult, tool_def_to_mcp_tool, make_json_result, make_text_result
from backend.app.mcp.permissions import filter_tools, is_allowed


def list_mcp_tools(
    *,
    allowed_tiers: set[str] | None = None,
) -> list[McpTool]:
    """Return MCP-formatted tool list, filtered by permission tier.

    Args:
        allowed_tiers: Tier names to allow. Defaults to {"readonly"}.
    """
    if allowed_tiers is None:
        allowed_tiers = {"readonly"}

    all_defs: list[ToolDef] = []
    for name in registry.all_names():
        td = registry.get(name)
        if td is not None:
            all_defs.append(td)

    allowed_defs = filter_tools(all_defs, allowed_tiers=allowed_tiers)

    result: list[McpTool] = []
    for td in allowed_defs:
        result.append(tool_def_to_mcp_tool(
            name=td.name,
            description=td.description,
            input_schema=td.input_schema,
            required=td.required or None,
        ))
    return result


def get_tool_def(name: str) -> ToolDef | None:
    """Look up a ToolDef by name."""
    return registry.get(name)


def is_tool_allowed(name: str, *, allowed_tiers: set[str] | None = None) -> bool:
    """Check whether a tool is allowed under the given tiers."""
    td = registry.get(name)
    if td is None:
        return False
    return is_allowed(td, allowed_tiers=allowed_tiers)
