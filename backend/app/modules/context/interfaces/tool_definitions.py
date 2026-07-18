# ruff: noqa: E501
"""Context workspace tool declarations."""

from __future__ import annotations

from app.architecture.tool_definition import ToolDef

TOOL_DEFINITIONS: tuple[ToolDef, ...] = (
    ToolDef(
        name="preview_writing_context",
        description="写作前上下文预检。显示本次章节写作将读取的大纲、近期摘要、角色当前状态、关系和世界观，并给出缺失/风险提示。质量模式创建或重写章节前应先调用。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "目标大纲节点ID"},
            "requirements": {
                "type": "string",
                "description": "用户的写作方向或额外要求，可用于筛选世界观",
            },
            "involved_characters": {
                "type": "array",
                "items": {"type": "string"},
                "description": "本章预计出场的角色名或别名列表",
            },
            "recent_limit": {
                "type": "integer",
                "description": "读取最近章节摘要数量，默认5，最大12",
            },
            "character_limit": {
                "type": "integer",
                "description": "返回角色状态数量，默认8，最大16",
            },
            "worldbuilding_limit": {
                "type": "integer",
                "description": "返回世界观条目数量，默认16，最大32",
            },
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler_name="preview_writing_context",
    ),
    ToolDef(
        name="search_context",
        description="全文检索项目中所有已索引的内容（章节、大纲、角色、世界观、记忆等）。返回相关度排序的结果列表。适用于跨类型模糊搜索。",
        input_schema={
            "query": {"type": "string", "description": "搜索关键词，支持中英文"},
            "source_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "限定搜索范围：chapter|chapter_summary|outline|character|character_timeline|worldbuilding|assistant_memory",
            },
            "limit": {"type": "integer", "description": "返回条数上限，默认20，最大50"},
        },
        required=["query"],
        tool_type="read",
        estimated_cost="free",
        handler_name="search_context",
    ),
    ToolDef(
        name="preview_rag_context",
        description="预算感知的上下文打包预览。展示本次写作将使用的大纲、摘要、角色、世界观、记忆等上下文分区，每分区含选取原因、字符预算和相关性评分。与preview_writing_context不同，此工具使用RAG检索。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "目标大纲节点ID"},
            "requirements": {"type": "string", "description": "写作方向或额外要求"},
            "budget_override": {
                "type": "object",
                "description": "预算覆盖：max_chapter_chars/max_summary_chars/max_character_chars/max_worldbuilding_chars/max_memory_chars/max_outline_chars/reserve_chars",
            },
            "pinned_chunk_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "固定选取的内容块ID列表，无论如何都会被包含",
            },
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler_name="preview_rag_context",
    ),
    ToolDef(
        name="explain_context_selection",
        description="解释为什么特定来源被选入或未选入上下文。传入来源ID列表，返回每个来源的评分详情和选取原因。用于理解上下文打包决策。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "目标大纲节点ID"},
            "source_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要解释的来源ID列表",
            },
            "requirements": {"type": "string", "description": "写作方向或额外要求"},
        },
        required=["source_ids"],
        tool_type="analysis",
        estimated_cost="free",
        handler_name="explain_context_selection",
    ),
    ToolDef(
        name="prepare_task_context",
        description="Prepare an auditable, budgeted baseline context manifest for an Agent task.",
        input_schema={
            "task_type": {
                "type": "string",
                "description": "writing|cataloging|review|rewrite|new_project|planning",
            },
            "context_manifest_id": {
                "type": "string",
                "description": "Existing manifest ID from a Siming MCP prompt or prior task preparation",
            },
            "manifest_id": {
                "type": "string",
                "description": "Compatibility alias for context_manifest_id",
            },
            "model": {
                "type": "string",
                "description": "Provider:model used for context-window budgeting",
            },
            "execution_route": {
                "type": "string",
                "description": "external_mcp|local_cli_agent|internal_api",
            },
            "arguments": {
                "type": "object",
                "description": "Task arguments used to resolve contract anchors",
            },
            "run_id": {
                "type": "string",
                "description": "Optional Agent run to bind to this manifest",
            },
            "pinned_chunk_ids": {"type": "array", "items": {"type": "string"}},
            "pinned_source_ids": {"type": "array", "items": {"type": "string"}},
        },
        required=["task_type"],
        tool_type="read",
        estimated_cost="free",
        handler_name="prepare_task_context",
    ),
    ToolDef(
        name="search_task_context",
        description="Search a baseline task manifest and return source-hash verified evidence candidates.",
        input_schema={
            "context_manifest_id": {"type": "string", "description": "Baseline manifest ID"},
            "run_id": {"type": "string", "description": "Agent run bound to a baseline manifest"},
            "query": {"type": "string", "description": "Task-specific retrieval query"},
            "limit": {"type": "integer", "description": "Maximum verified sources; default 12"},
        },
        required=["query"],
        tool_type="read",
        estimated_cost="free",
        handler_name="search_task_context",
    ),
    ToolDef(
        name="submit_context_evidence",
        description="Submit Agent-selected baseline/search sources for server-side hash verification before a formal write.",
        input_schema={
            "context_manifest_id": {"type": "string", "description": "Baseline manifest ID"},
            "run_id": {"type": "string", "description": "Agent run bound to a baseline manifest"},
            "sources": {
                "type": "array",
                "items": {"type": "object"},
                "description": "chunk_id/source_type/source_id/source_hash evidence",
            },
        },
        required=["sources"],
        tool_type="read",
        estimated_cost="free",
        handler_name="submit_context_evidence",
    ),
)


__all__ = ["TOOL_DEFINITIONS"]
