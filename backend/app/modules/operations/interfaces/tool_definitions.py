# ruff: noqa: E501
"""Operations workspace tool declarations."""

from __future__ import annotations

from app.architecture.tool_definition import ToolDef

TOOL_DEFINITIONS: tuple[ToolDef, ...] = (
    ToolDef(
        name="list_scheduled_tasks",
        description="列出当前作品的自动任务/定时任务。",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler_name="list_scheduled_tasks",
    ),
    ToolDef(
        name="create_scheduled_task",
        description="创建自动任务/定时任务。适用于用户要求定时搜索、定时整理资料、周期性提醒或定期执行项目助手任务。",
        input_schema={
            "name": {"type": "string", "description": "任务名称"},
            "prompt": {"type": "string", "description": "任务执行提示词，描述AI到点后要做什么"},
            "cron_expr": {
                "type": "string",
                "description": "Cron表达式，如 0 22 * * * 表示每天22点",
            },
            "interval_minutes": {
                "type": "integer",
                "description": "间隔分钟。若cron_expr存在则优先使用cron_expr",
            },
            "tool_policy": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选，任务可使用的工具名",
            },
            "status": {"type": "string", "description": "active|paused，默认active"},
        },
        required=["name", "prompt"],
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler_name="create_scheduled_task",
    ),
    ToolDef(
        name="update_scheduled_task",
        description="更新自动任务/定时任务。可按ID或名称定位，修改提示词、周期、状态等。",
        input_schema={
            "id": {"type": "string", "description": "任务ID"},
            "task_id": {"type": "string", "description": "兼容字段，同id"},
            "name": {"type": "string", "description": "任务名称，可用于定位或重命名"},
            "prompt": {"type": "string", "description": "新的执行提示词"},
            "cron_expr": {"type": "string", "description": "新的Cron表达式，空值表示清除"},
            "interval_minutes": {"type": "integer", "description": "新的间隔分钟"},
            "tool_policy": {
                "type": "array",
                "items": {"type": "string"},
                "description": "任务可使用工具名",
            },
            "status": {"type": "string", "description": "active|paused"},
        },
        tool_type="write",
        estimated_cost="free",
        handler_name="update_scheduled_task",
    ),
    ToolDef(
        name="delete_scheduled_task",
        description="删除自动任务/定时任务。必须在用户明确确认删除后调用。",
        input_schema={
            "id": {"type": "string", "description": "任务ID"},
            "task_id": {"type": "string", "description": "兼容字段，同id"},
            "name": {"type": "string", "description": "任务名称，id为空时用于定位"},
        },
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler_name="delete_scheduled_task",
    ),
    ToolDef(
        name="run_scheduled_task_now",
        description="立即执行一个自动任务。可能触发LLM、联网搜索或写入项目资料；用户明确要求立即运行时使用。",
        input_schema={
            "id": {"type": "string", "description": "任务ID"},
            "task_id": {"type": "string", "description": "兼容字段，同id"},
            "name": {"type": "string", "description": "任务名称，id为空时用于定位"},
        },
        tool_type="write",
        estimated_cost="medium",
        handler_name="run_scheduled_task_now",
    ),
    ToolDef(
        name="start_agent_run",
        description="Start a new external Agent run. Returns run_id for subsequent reporting.",
        input_schema={
            "client_name": {
                "type": "string",
                "description": "Client name: claude-code, codex, etc.",
            },
            "title": {"type": "string", "description": "Optional run title"},
        },
        tool_type="read",
        estimated_cost="free",
        handler_name="start_agent_run",
    ),
    ToolDef(
        name="report_agent_plan",
        description="Report the execution plan for an Agent run.",
        input_schema={
            "run_id": {"type": "string", "description": "Agent run ID"},
            "plan": {"type": "array", "items": {"type": "string"}, "description": "Plan steps"},
        },
        required=["run_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="report_agent_plan",
    ),
    ToolDef(
        name="report_agent_progress",
        description="Report a progress update for an Agent run.",
        input_schema={
            "run_id": {"type": "string", "description": "Agent run ID"},
            "message": {"type": "string", "description": "Progress message"},
            "step": {"type": "integer", "description": "Optional step index"},
        },
        required=["run_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="report_agent_progress",
    ),
    ToolDef(
        name="report_context_selected",
        description="Report which context was selected for reasoning.",
        input_schema={
            "run_id": {"type": "string", "description": "Agent run ID"},
            "sources": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Selected sources",
            },
        },
        required=["run_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="report_context_selected",
    ),
    ToolDef(
        name="append_draft_chunk",
        description="Stream a draft content chunk to the Agent run.",
        input_schema={
            "run_id": {"type": "string", "description": "Agent run ID"},
            "content": {"type": "string", "description": "Draft content chunk"},
            "chunk_index": {"type": "integer", "description": "Chunk sequence number"},
        },
        required=["run_id", "content"],
        tool_type="read",
        estimated_cost="free",
        handler_name="append_draft_chunk",
    ),
    ToolDef(
        name="mark_draft_ready",
        description="Signal that a draft is complete.",
        input_schema={
            "run_id": {"type": "string", "description": "Agent run ID"},
            "content_type": {
                "type": "string",
                "description": "Content type: chapter, outline, character, worldbuilding",
            },
            "summary": {"type": "string", "description": "Brief description of the draft"},
        },
        required=["run_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="mark_draft_ready",
    ),
    ToolDef(
        name="finish_agent_run",
        description="Signal Agent run completion with a summary.",
        input_schema={
            "run_id": {"type": "string", "description": "Agent run ID"},
            "summary": {"type": "string", "description": "Final summary of what was accomplished"},
        },
        required=["run_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="finish_agent_run",
    ),
)


__all__ = ["TOOL_DEFINITIONS"]
