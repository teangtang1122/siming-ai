# ruff: noqa: E501
"""Assistant workspace tool declarations."""

from __future__ import annotations

from app.architecture.tool_definition import ToolDef

TOOL_DEFINITIONS: tuple[ToolDef, ...] = (
    ToolDef(
        name="remember",
        description="保存一条持久化记忆。用户表达偏好或搜索到有用资料后使用。同key自动覆盖。回复中不要提及已保存。",
        input_schema={
            "key": {"type": "string", "description": "简短的记忆标识"},
            "value": {"type": "string", "description": "记忆内容"},
            "category": {
                "type": "string",
                "description": "分类：user_preference|project_fact|writing_style|research_note|workflow_preference，默认user_preference",
            },
            "importance": {"type": "integer", "description": "重要性0-10，默认5。≥7才会被优先召回"},
        },
        required=["key", "value"],
        tool_type="memory",
        estimated_cost="low",
        handler_name="remember",
    ),
    ToolDef(
        name="recall",
        description="按关键词查询已保存的记忆。每次新对话开始时先查询相关记忆。",
        input_schema={
            "query": {"type": "string", "description": "搜索记忆的关键词"},
            "category": {
                "type": "string",
                "description": "可选分类过滤：user_preference|project_fact|writing_style|research_note|workflow_preference",
            },
            "limit": {"type": "integer", "description": "返回条数上限，默认10，最大20"},
        },
        tool_type="memory",
        estimated_cost="low",
        handler_name="recall",
    ),
    ToolDef(
        name="forget",
        description="删除记忆。用户说'不要记住''忘掉'时使用。按ID或key定位。",
        input_schema={
            "id": {"type": "string", "description": "记忆记录ID（优先使用）"},
            "key": {
                "type": "string",
                "description": "记忆标识（id为空时使用，删除所有匹配key的记忆）",
            },
        },
        tool_type="memory",
        estimated_cost="low",
        handler_name="forget",
    ),
    ToolDef(
        name="list_memories",
        description="列出已保存的记忆。可按分类筛选。用于浏览和管理记忆。",
        input_schema={
            "category": {
                "type": "string",
                "description": "可选分类过滤：user_preference|project_fact|writing_style|research_note|workflow_preference",
            },
            "limit": {"type": "integer", "description": "返回条数上限，默认30，最大100"},
        },
        tool_type="memory",
        estimated_cost="free",
        handler_name="list_memories",
    ),
)


__all__ = ["TOOL_DEFINITIONS"]
