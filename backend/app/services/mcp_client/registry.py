"""External MCP tool registry — bridges external MCP server tools into the workspace.

External tools are registered as mcp.{server_name}.{tool_name} in the
workspace ToolRegistry.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.workspace.registry import ToolDef, registry

logger = logging.getLogger(__name__)

# Prefix for external MCP tools
_MCP_PREFIX = "mcp."


def external_tool_name(server_name: str, tool_name: str) -> str:
    """Build the workspace tool name for an external MCP tool."""
    return f"{_MCP_PREFIX}{server_name}.{tool_name}"


def is_external_mcp_tool(name: str) -> bool:
    """Return True if the tool name is an external MCP tool."""
    return name.startswith(_MCP_PREFIX)


def parse_external_tool_name(name: str) -> tuple[str, str] | None:
    """Parse an external MCP tool name into (server_name, tool_name).

    Returns None if the name is not an external MCP tool.
    """
    if not is_external_mcp_tool(name):
        return None
    rest = name[len(_MCP_PREFIX):]
    parts = rest.split(".", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None


def register_external_tool(
    server_name: str,
    tool_name: str,
    description: str,
    input_schema: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> str:
    """Register an external MCP tool in the workspace registry.

    Args:
        server_name: Name of the external MCP server.
        tool_name: Name of the tool on the external server.
        description: Tool description.
        input_schema: JSON Schema properties dict.
        required: Required parameter names.

    Returns:
        The full workspace tool name (mcp.{server_name}.{tool_name}).
    """
    full_name = external_tool_name(server_name, tool_name)

    # Don't re-register if already exists
    if registry.get(full_name):
        return full_name

    def _handler(db: Any, project_id: str, args: dict) -> dict:
        """Stub handler — actual execution delegated to external server."""
        return {
            "tool": full_name,
            "status": "error",
            "detail": f"External MCP tool execution not yet wired: {full_name}",
        }

    td = ToolDef(
        name=full_name,
        description=f"[MCP:{server_name}] {description}",
        input_schema=input_schema,
        required=required or [],
        tool_type="read",  # external tools default to read; override as needed
        estimated_cost="low",
        handler=_handler,
    )
    registry.register(td)
    logger.info("Registered external MCP tool: %s", full_name)
    return full_name


def unregister_server_tools(server_name: str) -> int:
    """Unregister all tools from a specific external MCP server.

    Returns the number of tools removed.
    """
    prefix = f"{_MCP_PREFIX}{server_name}."
    to_remove = [
        name for name in registry.all_names()
        if name.startswith(prefix)
    ]
    for name in to_remove:
        registry._tools.pop(name, None)
    if to_remove:
        logger.info("Unregistered %d tools from server: %s", len(to_remove), server_name)
    return len(to_remove)
