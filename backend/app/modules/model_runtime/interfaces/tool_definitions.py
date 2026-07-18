# ruff: noqa: E501
"""Model-runtime workspace tool declarations."""

from __future__ import annotations

from app.architecture.tool_definition import ToolDef

TOOL_DEFINITIONS: tuple[ToolDef, ...] = (
    ToolDef(
        name="start_local_cli_agent_run",
        description="Start a Siming-managed local CLI Agent worker (Claude/Codex/opencode). The CLI reads project files directly but must write/delete/update only through Siming MCP tools. Returns an Agent run_id whose events can be streamed in the UI.",
        input_schema={
            "task_type": {"type": "string", "description": "general|cataloging|writing"},
            "user_request": {
                "type": "string",
                "description": "User request for the local CLI agent",
            },
            "provider": {
                "type": "string",
                "description": "Optional local CLI provider id, e.g. claude_cli/codex_cli/opencode_cli/mimocode_cli/cursor_cli/kilocode_cli/qwen_code_cli/hermes_cli/openclaw_cli",
            },
            "outline_node_id": {
                "type": "string",
                "description": "Writing target outline node for the governed baseline",
            },
            "chapter_id": {
                "type": "string",
                "description": "Cataloging/review target chapter for the governed baseline",
            },
            "context_manifest_id": {
                "type": "string",
                "description": "Optional previously prepared baseline manifest",
            },
            "pinned_chunk_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Author-pinned context chunks",
            },
            "pinned_source_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Author-pinned context source ids",
            },
        },
        tool_type="scheduler",
        estimated_cost="local_cli",
        handler_name="start_local_cli_agent_run",
    ),
    ToolDef(
        name="wait_local_cli_agent_run",
        description="Wait for a Siming-managed local CLI Agent run to finish and validate that writes landed in the database. For writing runs, detects direct file edits/orphan chapter mirror files and fails the plan instead of reporting false success.",
        input_schema={
            "run_id": {
                "type": "string",
                "description": "Agent run ID returned by start_local_cli_agent_run",
            },
            "task_type": {"type": "string", "description": "general|cataloging|writing"},
            "outline_node_id": {
                "type": "string",
                "description": "Expected target outline node for writing validation",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Maximum wait time; default 1800",
            },
            "startup_timeout_seconds": {
                "type": "integer",
                "description": "Maximum time to wait for cli_started; default 10",
            },
            "poll_seconds": {"type": "number", "description": "Polling interval; default 2"},
        },
        required=["run_id"],
        tool_type="scheduler",
        estimated_cost="free",
        handler_name="wait_local_cli_agent_run",
    ),
)


__all__ = ["TOOL_DEFINITIONS"]
