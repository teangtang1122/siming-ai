# ruff: noqa: E501
"""Context workspace tool declarations."""

from __future__ import annotations

from app.architecture.tool_definition import ToolDef
from app.services.task_context_delivery import CONTEXT_PAGE_INPUTS

TOOL_DEFINITIONS: tuple[ToolDef, ...] = (
    ToolDef(
        name="search_context",
        description="分页全文检索项目索引，每页只返回短候选、真实ID和哈希。返回 next_cursor 时可继续下一页；原文用对应读取工具按范围获取。",
        input_schema={
            "query": {"type": "string", "maxLength": 200, "description": "搜索关键词，支持中英文"},
            "source_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "限定搜索范围：chapter|chapter_summary|outline|character|character_timeline|worldbuilding|assistant_memory",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3,
                "description": "本页条数，默认/最大3",
            },
            "cursor": {
                "type": "integer",
                "minimum": 0,
                "maximum": 40,
                "description": "上一页返回的 next_cursor",
            },
        },
        required=["query"],
        tool_type="read",
        estimated_cost="free",
        handler_name="search_context",
    ),
    ToolDef(
        name="preview_rag_context",
        description="预算感知的RAG检索分析预览。展示给定查询可能命中的大纲、摘要、角色、世界观和记忆分区；只用于分析，不会成为 chapter_writer 的已选证据。",
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
        description="建立或按原ID读取任务上下文。首次建立时直接提交当前任务的结构化目标：writing 必须提交 outline_node_id；cataloging 必须提交 chapter_id；review/rewrite 必须提交 chapter_id 或 text；outline_planning 直接提交 parent_id/insert_after_id。未选择证据时仅含目标/位置、文风、作者要求等硬锚点；选定后包含全部精确来源。正文只在 context_page.text 中分页返回；按 next_arguments 读取，直到 has_more=false，才能生成。页面有完整文档哈希，不丢弃后续内容。",
        input_schema={
            "task_type": {
                "type": "string",
                "description": "writing|outline_planning|cataloging|review|rewrite|new_project|planning",
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
                "description": "Provider:model used for budgeting by unbound external agents; managed CLI runs use their pinned executing model",
            },
            "execution_route": {
                "type": "string",
                "description": "external_mcp|local_cli_agent|internal_api",
            },
            "outline_node_id": {
                "type": "string",
                "description": "writing 的必填章级大纲ID；必须属于当前作品",
            },
            "target_chapter_id": {
                "type": "string",
                "description": "writing 修订任务对应的既有章节ID",
            },
            "source_draft_id": {
                "type": "string",
                "description": "继续修改当前未保存章节草稿时必填；必须是当前作品真实 pending 草稿ID",
            },
            "chapter_id": {
                "type": "string",
                "description": "cataloging/review/rewrite 的目标章节ID",
            },
            "parent_id": {"type": "string", "description": "outline_planning 的父节点ID；根级可省略"},
            "insert_after_id": {"type": "string", "description": "outline_planning 的同级插入锚点ID"},
            "batch_count": {"type": "integer", "minimum": 1, "maximum": 8, "default": 1},
            "requirements": {"type": "string", "description": "作者对本次任务的明确要求"},
            "minimum_han_characters": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100000,
                "description": "仅当作者明确要求中文正文硬下限时，由模型结构化填写；保存边界按汉字数强制校验，不从 requirements 猜测",
            },
            "text": {"type": "string", "description": "review/rewrite 没有 chapter_id 时的目标文本"},
            "title": {"type": "string", "description": "内联目标文本的标题"},
            "run_id": {
                "type": "string",
                "description": "Optional Agent run to bind to this manifest",
            },
            "pinned_chunk_ids": {"type": "array", "items": {"type": "string"}},
            "pinned_source_ids": {"type": "array", "items": {"type": "string"}},
            **CONTEXT_PAGE_INPUTS,
        },
        required=["task_type"],
        tool_type="read",
        direct_mcp_project_scoped=True,
        direct_mcp_transactional=True,
        estimated_cost="free",
        handler_name="prepare_task_context",
    ),
    ToolDef(
        name="search_task_context",
        description="按模型自行提出的查询检索当前作品，返回带真实ID、哈希和短摘要的候选来源；结果只供复核，不会自动进入正文上下文。可多次从不同角度查询。",
        input_schema={
            "context_manifest_id": {"type": "string", "description": "Baseline manifest ID"},
            "run_id": {"type": "string", "description": "Agent run bound to a baseline manifest"},
            "query": {
                "type": "string",
                "maxLength": 500,
                "description": "Task-specific retrieval query",
            },
            "source_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选范围：chapter|chapter_summary|outline|character|character_timeline|worldbuilding|assistant_memory|narrative_governance",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 10,
                "description": (
                    "Maximum short candidates on this search page; default/max 10."
                ),
            },
            "cursor": {
                "type": "integer",
                "minimum": 0,
                "maximum": 20,
                "default": 0,
                "description": "Use the previous page's next_cursor for more candidates.",
            },
        },
        required=["query"],
        tool_type="read",
        direct_mcp_project_scoped=True,
        direct_mcp_transactional=True,
        estimated_cost="free",
        handler_name="search_task_context",
    ),
    ToolDef(
        name="submit_context_evidence",
        description="提交模型复核后选中的 search_task_context 候选，校验归属与哈希并完整读取原文。返回首个 context_page；若 has_more=true，选择令牌会被暂扣，必须逐次原样复制 next_arguments 调用 prepare_task_context，直到最后一页才返回 context_selection_token。页游标跳跃、哈希或页大小变化都会被拒绝。32k token 是可超过的精简软目标，容量依模型窗口；确实无需额外资料时可提交原生空数组。",
        input_schema={
            "context_manifest_id": {"type": "string", "description": "Baseline manifest ID"},
            "run_id": {"type": "string", "description": "Agent run bound to a baseline manifest"},
            "sources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "chunk_id": {"type": "string"},
                        "source_type": {"type": "string"},
                        "source_id": {"type": "string"},
                        "source_hash": {"type": "string"},
                    },
                    "anyOf": [
                        {"required": ["item_id"]},
                        {"required": ["chunk_id"]},
                        {"required": ["source_type", "source_id"]},
                    ],
                    "additionalProperties": False,
                },
                "description": "从检索结果复制 item_id（推荐）或 chunk_id/source_type/source_id/source_hash；仅受模型实际输入预算约束",
            },
        },
        required=["sources"],
        tool_type="read",
        direct_mcp_project_scoped=True,
        direct_mcp_transactional=True,
        estimated_cost="free",
        handler_name="submit_context_evidence",
    ),
)


__all__ = ["TOOL_DEFINITIONS"]
