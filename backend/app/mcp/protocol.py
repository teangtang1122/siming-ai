"""ASCII-safe JSON-RPC responses and UTF-8 stdio configuration."""
from __future__ import annotations

import json
import sys
from contextlib import suppress
from typing import Any

from app.mcp.schemas import McpToolResult


def _jsonrpc_error(id: Any, code: int, message: str, data: Any = None) -> str:
    """Build a JSON-RPC error response string."""
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    resp = {"jsonrpc": "2.0", "id": id, "error": err}
    # Keep the wire payload ASCII-safe for Windows stdio MCP clients. JSON
    # parsers still recover the original Unicode strings after decoding.
    return json.dumps(resp, ensure_ascii=True)


def _jsonrpc_result(id: Any, result: Any) -> str:
    """Build a JSON-RPC success response string."""
    resp = {"jsonrpc": "2.0", "id": id, "result": result}
    # Keep the wire payload ASCII-safe for Windows stdio MCP clients. JSON
    # parsers still recover the original Unicode strings after decoding.
    return json.dumps(resp, ensure_ascii=True)


def _configure_stdio_utf8() -> None:
    """Prefer UTF-8 stdio when the host process supports reconfiguration."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")


def _tool_result_to_dict(result: McpToolResult) -> dict:
    """Convert McpToolResult to MCP protocol dict."""
    return {
        "content": result.content,
        "isError": result.is_error,
    }
