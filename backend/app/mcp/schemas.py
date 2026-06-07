"""MCP-specific schema types.

Maps between internal ToolDef fields and MCP protocol structures.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class McpTool:
    """MCP Tool definition as returned by tools/list."""
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema object


@dataclass(frozen=True)
class McpToolResult:
    """MCP Tool result as returned by tools/call."""
    content: list[dict[str, Any]]
    is_error: bool = False


def tool_def_to_mcp_tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    required: list[str] | None = None,
) -> McpTool:
    """Convert an internal ToolDef to an MCP McpTool."""
    schema: dict[str, Any] = {"type": "object", "properties": input_schema}
    if required:
        schema["required"] = required
    return McpTool(
        name=name,
        description=description,
        input_schema=schema,
    )


def make_text_result(text: str, *, is_error: bool = False) -> McpToolResult:
    """Build a simple text MCP tool result."""
    return McpToolResult(
        content=[{"type": "text", "text": text}],
        is_error=is_error,
    )


def make_json_result(data: Any, *, is_error: bool = False) -> McpToolResult:
    """Build a JSON-encoded MCP tool result."""
    import json
    return McpToolResult(
        content=[{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
        is_error=is_error,
    )
