# ruff: noqa: E501
"""Context workspace tool declarations."""

from __future__ import annotations

from app.architecture.tool_definition import ToolDef

TOOL_DEFINITIONS: tuple[ToolDef, ...] = (
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
        description="为模型选定的任务建立可审计的精简上下文基线。写章和规划大纲都只返回目标/位置、文风、作者要求等硬锚点，不会自动带入角色、前文、世界观或检索结果；随后由模型按需检索并提交精确来源。",
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
        description="按模型自行提出的查询检索当前作品，返回带真实ID、哈希和短摘要的候选来源；结果只供复核，不会自动进入正文上下文。可多次从不同角度查询。",
        input_schema={
            "context_manifest_id": {"type": "string", "description": "Baseline manifest ID"},
            "run_id": {"type": "string", "description": "Agent run bound to a baseline manifest"},
            "query": {"type": "string", "description": "Task-specific retrieval query"},
            "source_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选范围：chapter|chapter_summary|outline|character|character_timeline|worldbuilding|assistant_memory|narrative_governance",
            },
            "limit": {"type": "integer", "description": "Maximum candidates on this search page; default 12, model-selected generation max 20"},
        },
        required=["query"],
        tool_type="read",
        estimated_cost="free",
        handler_name="search_task_context",
    ),
    ToolDef(
        name="submit_context_evidence",
        description="提交模型复核后选中的 search_task_context 候选。服务端完整读取精确原文并校验归属与哈希，不设单条固定字符截断；32k token 只是可超过的精简软目标，实际容量按模型窗口扣除输出预留与安全余量，来源数量没有固定硬上限。返回的 context_selection_token 必须在下一模型步骤用于写章或规划；确实无需额外资料时可提交空数组。",
        input_schema={
            "context_manifest_id": {"type": "string", "description": "Baseline manifest ID"},
            "run_id": {"type": "string", "description": "Agent run bound to a baseline manifest"},
            "sources": {
                "type": "array",
                "items": {"type": "object"},
                "description": "从检索结果复制 item_id（推荐）或 chunk_id/source_type/source_id/source_hash；仅受模型实际输入预算约束",
            },
        },
        required=["sources"],
        tool_type="read",
        estimated_cost="free",
        handler_name="submit_context_evidence",
    ),
)


__all__ = ["TOOL_DEFINITIONS"]
