# ruff: noqa: E501
"""Model-runtime workspace tool declarations."""

from __future__ import annotations

from app.architecture.tool_definition import ToolDef

TOOL_DEFINITIONS: tuple[ToolDef, ...] = (
    ToolDef(
        name="start_local_cli_agent_run",
        description="Start a Siming-managed local CLI Agent worker for general work, cataloging, chapter writing, or an author-reviewable outline proposal. Writing and planning use the same model-driven task-context selection contract and only save drafts. Never call this from an already-running external MCP client.",
        input_schema={
            "task_type": {
                "type": "string",
                "enum": ["general", "cataloging", "writing", "outline_planning"],
                "description": "general|cataloging|writing|outline_planning",
            },
            "user_request": {
                "type": "string",
                "description": "User request for the local CLI agent",
            },
            "provider": {
                "type": "string",
                "description": "Optional local CLI provider id, e.g. claude_cli/codex_cli/opencode_cli/mimocode_cli/cursor_cli/kilocode_cli/qwen_code_cli/hermes_cli/openclaw_cli/dsh_cli",
            },
            "chapter_id": {
                "type": "string",
                "description": "Cataloging/review target chapter for the governed baseline",
            },
            "outline_node_id": {
                "type": "string",
                "description": "Real chapter-level outline target for writing when already resolved",
            },
            "parent_id": {
                "type": "string",
                "description": "Real parent outline ID for an outline proposal; empty means root",
            },
            "insert_after_id": {
                "type": "string",
                "description": "Real sibling insertion anchor for an outline proposal",
            },
            "batch_count": {
                "type": "integer",
                "description": "Requested outline proposal count, 1-8; never triggers planning by itself",
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
        description="Wait for a Siming-managed CLI Agent run to finish.",
        input_schema={
            "run_id": {
                "type": "string",
                "description": "Agent run ID returned by start_local_cli_agent_run",
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
