# ruff: noqa: E501
"""Bounded story search tool declarations."""

from __future__ import annotations

from app.architecture.tool_definition import ToolDef

SEARCH_TOOL_DEFINITIONS: tuple[ToolDef, ...] = (
    ToolDef(
        name="search_characters",
        description="按角色名分页搜索角色档案。fields 选择最多3个详情字段；某字段返回 next_offset_chars 时，继续按范围读取可得到精确原文。",
        input_schema={
            "query": {
                "type": "string",
                "maxLength": 100,
                "description": "角色名片段，支持模糊匹配",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 2,
                "description": "本页条数，默认/最大2",
            },
            "cursor": {"type": "integer", "minimum": 0, "description": "上一页的 next_cursor"},
            "fields": {
                "type": "array",
                "maxItems": 3,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "enum": [
                        "appearance",
                        "personality",
                        "background",
                        "abilities",
                        "physical_state",
                        "mental_state",
                        "current_goal",
                        "active_conflict",
                        "abilities_state",
                        "items_or_assets",
                    ],
                },
                "description": "本次精确读取的详情字段，默认外貌/性格/背景",
            },
            "field_offset_chars": {
                "type": "integer",
                "minimum": 0,
                "description": "所选字段的起始字符偏移",
            },
            "field_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "description": "每个所选字段的本页字符数，默认/最大200",
            },
        },
        required=["query"],
        tool_type="read",
        direct_mcp_project_scoped=True,
        direct_mcp_transactional=True,
        estimated_cost="free",
        handler_name="search_characters",
    ),
    ToolDef(
        name="search_chapters",
        description="分页搜索章节，并按 content_offset_chars/content_chars 精确读取正文范围。返回 content_range.next_offset_chars 时用它继续。",
        input_schema={
            "query": {
                "type": "string",
                "maxLength": 200,
                "description": "章节标题片段，支持模糊匹配",
            },
            "outline_node_id": {
                "type": "string",
                "description": "限定大纲节点ID，传入后忽略query直接返回该节点下所有章节",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 2,
                "description": "本页条数，默认/最大2",
            },
            "cursor": {"type": "integer", "minimum": 0, "description": "上一页的 next_cursor"},
            "content_offset_chars": {
                "type": "integer",
                "minimum": 0,
                "description": "正文起始字符偏移",
            },
            "content_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": 400,
                "description": "每章本页正文字符数，默认/最大400",
            },
        },
        tool_type="read",
        direct_mcp_project_scoped=True,
        direct_mcp_transactional=True,
        estimated_cost="free",
        handler_name="search_chapters",
    ),
    ToolDef(
        name="search_outline",
        description="分页搜索大纲节点或指定节点的直接子节点；摘要和关联角色均有显式范围/分页。",
        input_schema={
            "query": {
                "type": "string",
                "maxLength": 200,
                "description": "大纲标题片段，支持模糊匹配",
            },
            "node_id": {
                "type": "string",
                "description": "指定节点ID，传入后返回该节点及分页直接子节点（忽略query）",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 2,
                "description": "本页节点数，默认/最大2",
            },
            "cursor": {
                "type": "integer",
                "minimum": 0,
                "description": "节点或子节点的上一页 next_cursor",
            },
            "summary_offset_chars": {
                "type": "integer",
                "minimum": 0,
                "description": "summary/actual_summary/planned_summary 起始偏移",
            },
            "summary_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "每个摘要字段的本页字符数，默认/最大100",
            },
            "linked_cursor": {
                "type": "integer",
                "minimum": 0,
                "description": "关联角色上一页 next_cursor",
            },
            "linked_limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 2,
                "description": "每节点本页关联角色数，默认/最大2",
            },
        },
        tool_type="read",
        direct_mcp_project_scoped=True,
        direct_mcp_transactional=True,
        estimated_cost="free",
        handler_name="search_outline",
    ),
    ToolDef(
        name="search_outline_tree",
        description="以稳定前序的扁平分页返回大纲树结构（ID、父ID、标题、层级），避免一次回灌整棵大树。",
        input_schema={
            "root_id": {"type": "string", "description": "可选，子树根节点ID。不传则从顶层开始"},
            "cursor": {"type": "integer", "minimum": 0, "description": "上一页的 next_cursor"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "本页节点数，默认/最大10",
            },
        },
        tool_type="read",
        estimated_cost="free",
        handler_name="search_outline_tree",
    ),
    ToolDef(
        name="search_worldbuilding",
        description="分页搜索世界观条目，并按 content_offset_chars/content_chars 精确读取内容范围。",
        input_schema={
            "query": {
                "type": "string",
                "maxLength": 200,
                "description": "设定标题片段，支持模糊匹配",
            },
            "dimension": {
                "type": "string",
                "description": "限定维度：geography|history|factions|power_system|races|culture",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 2,
                "description": "本页条数，默认/最大2",
            },
            "cursor": {"type": "integer", "minimum": 0, "description": "上一页的 next_cursor"},
            "content_offset_chars": {
                "type": "integer",
                "minimum": 0,
                "description": "内容起始字符偏移",
            },
            "content_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": 400,
                "description": "每条本页字符数，默认/最大400",
            },
        },
        tool_type="read",
        direct_mcp_project_scoped=True,
        direct_mcp_transactional=True,
        estimated_cost="free",
        handler_name="search_worldbuilding",
    ),
)


__all__ = ["SEARCH_TOOL_DEFINITIONS"]
