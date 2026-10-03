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
        description="建立或读取任务上下文。首次传真实目标ID：写章用 outline_node_id，建档/评审用 chapter_id，规划用 parent_id/insert_after_id。写章和规划先返回精简锚点；提交证据后，内置生成器取得令牌并自行读取完整资料；外部 Agent 按 next_arguments 读完所有 context_page 后取得令牌。",
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
                "description": "仅当作者明确提出正文篇幅时填写，未提出则省略，不得自行设定或逐轮抬高。作为生成参考与草稿字数提示，未达到时仍保留完整未保存草稿，不自动重写",
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
            "context_manifest_id": {"type": "string", "description": "Copy the context_manifest_id returned by prepare_task_context; required for workspace Agent searches"},
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
        description="提交选中的候选来源。必须原样填写 prepare_task_context 返回的 context_manifest_id；无需额外来源时 sources 提交空数组。内置生成器取得选择令牌，外部 Agent 逐页读取 context_page。",
        input_schema={
            "context_manifest_id": {"type": "string", "description": "Copy the context_manifest_id returned by prepare_task_context; required for workspace Agent evidence submission"},
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
        required=["context_manifest_id", "sources"],
        tool_type="read",
        direct_mcp_project_scoped=True,
        direct_mcp_transactional=True,
        estimated_cost="free",
        handler_name="submit_context_evidence",
    ),
)


__all__ = ["TOOL_DEFINITIONS"]
