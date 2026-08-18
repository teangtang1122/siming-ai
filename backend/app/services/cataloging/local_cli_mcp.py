"""Narrow MCP and filesystem permissions for managed cataloging CLI turns."""
from __future__ import annotations

import json
from typing import Any

from app.services.external_agent.mcp_preflight import (
    CATALOGING_MCP_TOOL_NAMES,
    preflight_cli_integration,
)


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
    for tool_name in CATALOGING_MCP_TOOL_NAMES:
        permission[f"siming_{tool_name}"] = "allow"
    return json.dumps(permission, ensure_ascii=False, separators=(",", ":"))


def preflight_opencode_cataloging(cli_command: str | None) -> dict[str, Any]:
    return preflight_cli_integration(
        "opencode_cli",
        cli_command=cli_command,
        permission_pack="cataloging_worker",
    )
