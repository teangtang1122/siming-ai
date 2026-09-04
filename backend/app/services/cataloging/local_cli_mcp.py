"""Narrow MCP and filesystem permissions for managed cataloging CLI turns."""
from __future__ import annotations

import json
from typing import Any

from app.ai.local_cli_prompt import TRANSIENT_MCP_NAME
from app.architecture.tool_categories import TOOL_CATEGORY_CONTROLLER
from app.services.external_agent.mcp_preflight import CATALOGING_MCP_TOOL_NAMES


def opencode_cataloging_permission_env() -> str:
    permission: dict[str, Any] = {
        "*": "deny",
        "read": {
            "*": "allow",
            "*.env": "deny",
            "*.env.*": "deny",
            "*.env.example": "allow",
        },
        "glob": "allow",
        "grep": "allow",
        "edit": "deny",
        "bash": "deny",
        "question": "deny",
        "task": "deny",
        "skill": "deny",
        "lsp": "deny",
        "webfetch": "deny",
        "websearch": "deny",
        "external_directory": "deny",
        "doom_loop": "allow",
    }
    for tool_name in (*CATALOGING_MCP_TOOL_NAMES, TOOL_CATEGORY_CONTROLLER):
        permission[f"{TRANSIENT_MCP_NAME}_{tool_name}"] = "allow"
    return json.dumps(permission, ensure_ascii=False, separators=(",", ":"))
