#!/usr/bin/env python3
"""Tool Registry Linter — checks every registered tool has required metadata.

Run this script to verify that all tools in the workspace ToolRegistry
have the required metadata fields for permission pack classification,
MCP exposure, and frontend display.

Usage:
    python scripts/check-tool-registry.py

Exit codes:
    0 — all checks pass
    1 — one or more checks failed
"""
from __future__ import annotations

import sys
import os

# Add backend to path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_SCRIPT_DIR)
_BACKEND_DIR = os.path.join(_ROOT_DIR, "backend")
sys.path.insert(0, _BACKEND_DIR)

from app.services.workspace.registry import registry, ToolDef
from app.mcp.permissions import is_secret_tool


def check_tool(td: ToolDef) -> list[str]:
    """Check a single tool for required metadata. Returns list of issues."""
    issues: list[str] = []

    # Required string fields
    if not td.name:
        issues.append("missing name")
    if not td.description:
        issues.append(f"{td.name}: missing description")
    if td.input_schema is None:
        issues.append(f"{td.name}: missing input_schema (None)")
    if not td.tool_type:
        issues.append(f"{td.name}: missing tool_type")

    # Required metadata
    if not td.permission_tags:
        issues.append(f"{td.name}: missing permission_tags")
    if td.risk_level not in ("safe", "low", "medium", "high", "destructive"):
        issues.append(f"{td.name}: invalid risk_level '{td.risk_level}'")
    if not isinstance(td.writes_project_data, bool):
        issues.append(f"{td.name}: writes_project_data must be bool")
    if not isinstance(td.expose_to_internal_agent, bool):
        issues.append(f"{td.name}: expose_to_internal_agent must be bool")
    if not isinstance(td.expose_to_scheduler, bool):
        issues.append(f"{td.name}: expose_to_scheduler must be bool")
    if not isinstance(td.expose_to_mcp, bool):
        issues.append(f"{td.name}: expose_to_mcp must be bool")

    # Handler check
    if td.handler is None:
        issues.append(f"{td.name}: missing handler")

    # Secret tool check
    if is_secret_tool(td.name) and td.expose_to_mcp:
        issues.append(f"{td.name}: secret-looking tool exposed to MCP")

    # MCP permission pack check
    valid_packs = {
        "readonly_collaboration", "draft_generation", "project_writing",
        "project_management", "trusted_local_maintenance",
    }
    pack = registry._derive_mcp_pack(td)
    if pack not in valid_packs:
        issues.append(f"{td.name}: invalid mcp_permission_pack '{pack}'")

    return issues


def main() -> int:
    print("[linter] Checking tool registry...")
    print(f"[linter] Total tools: {len(registry.all_names())}")

    all_issues: list[str] = []

    for name in registry.all_names():
        td = registry.get(name)
        if not td:
            all_issues.append(f"Tool '{name}' not found in registry")
            continue
        issues = check_tool(td)
        all_issues.extend(issues)

    # Report
    if all_issues:
        print(f"\n[linter] FAIL — {len(all_issues)} issue(s) found:\n")
        for issue in all_issues:
            print(f"  - {issue}")
        return 1
    else:
        print("\n[linter] PASS — all tools have required metadata.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
