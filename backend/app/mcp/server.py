"""MCP server protocol handler.

Processes JSON-RPC messages for the MCP protocol. This module handles
the message framing and dispatches to adapter/permissions layers.

V1 serves over stdio only.
"""
from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from backend.app.mcp.adapter import list_mcp_tools, is_tool_allowed, get_tool_def
from backend.app.mcp.schemas import McpToolResult, make_text_result, make_json_result

# ── MCP protocol constants ───────────────────────────────────────────────

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "moshu"
SERVER_VERSION = "0.1.0"  # TODO: pull from app version

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
TOOL_NOT_FOUND = -32000
PERMISSION_DENIED = -32001
PROJECT_NOT_FOUND = -32002
TOOL_EXECUTION_FAILED = -32003


def _jsonrpc_error(id: Any, code: int, message: str, data: Any = None) -> str:
    """Build a JSON-RPC error response string."""
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    resp = {"jsonrpc": "2.0", "id": id, "error": err}
    return json.dumps(resp, ensure_ascii=False)


def _jsonrpc_result(id: Any, result: Any) -> str:
    """Build a JSON-RPC success response string."""
    resp = {"jsonrpc": "2.0", "id": id, "result": result}
    return json.dumps(resp, ensure_ascii=False)


def handle_message(raw: str, *, allowed_tiers: set[str] | None = None) -> str:
    """Process one JSON-RPC message and return the response string.

    Args:
        raw: The raw JSON-RPC message string.
        allowed_tiers: Permission tiers to allow. Defaults to {"readonly"}.

    Returns:
        JSON-RPC response string.
    """
    if allowed_tiers is None:
        allowed_tiers = {"readonly"}

    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return _jsonrpc_error(None, PARSE_ERROR, "Invalid JSON")

    msg_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {})

    if method == "initialize":
        return _handle_initialize(msg_id, params)
    elif method == "tools/list":
        return _handle_tools_list(msg_id, allowed_tiers)
    elif method == "tools/call":
        return _handle_tools_call(msg_id, params, allowed_tiers)
    elif method == "ping":
        return _jsonrpc_result(msg_id, {})
    else:
        return _jsonrpc_error(msg_id, METHOD_NOT_FOUND, f"Unknown method: {method}")


def _handle_initialize(msg_id: Any, params: dict) -> str:
    """Handle the MCP initialize handshake."""
    result = {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {
            "tools": {"listChanged": False},
        },
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
        },
    }
    return _jsonrpc_result(msg_id, result)


def _handle_tools_list(msg_id: Any, allowed_tiers: set[str]) -> str:
    """Handle tools/list request."""
    tools = list_mcp_tools(allowed_tiers=allowed_tiers)
    tool_dicts = []
    for t in tools:
        tool_dicts.append({
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema,
        })
    return _jsonrpc_result(msg_id, {"tools": tool_dicts})


def _handle_tools_call(msg_id: Any, params: dict, allowed_tiers: set[str]) -> str:
    """Handle tools/call request."""
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    # Check tool exists
    td = get_tool_def(tool_name)
    if td is None:
        result = make_text_result(
            f"Tool not found: {tool_name}",
            is_error=True,
        )
        return _jsonrpc_result(msg_id, _tool_result_to_dict(result))

    # Check permission
    if not is_tool_allowed(tool_name, allowed_tiers=allowed_tiers):
        result = make_text_result(
            f"Permission denied: {tool_name} requires a higher permission tier",
            is_error=True,
        )
        return _jsonrpc_result(msg_id, _tool_result_to_dict(result))

    # v1: tool execution not yet wired — return not-implemented
    result = make_text_result(
        f"Tool execution not yet implemented: {tool_name}",
        is_error=True,
    )
    return _jsonrpc_result(msg_id, _tool_result_to_dict(result))


def _tool_result_to_dict(result: McpToolResult) -> dict:
    """Convert McpToolResult to MCP protocol dict."""
    return {
        "content": result.content,
        "isError": result.is_error,
    }


def serve_stdio(*, allowed_tiers: set[str] | None = None) -> None:
    """Run the MCP server over stdio (blocking).

    Reads newline-delimited JSON-RPC from stdin, writes responses to stdout.
    """
    if allowed_tiers is None:
        allowed_tiers = {"readonly"}

    stdin: TextIO = sys.stdin
    stdout: TextIO = sys.stdout

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        response = handle_message(line, allowed_tiers=allowed_tiers)
        stdout.write(response + "\n")
        stdout.flush()
