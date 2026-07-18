# ruff: noqa: E501
"""Integrations workspace tool declarations."""

from __future__ import annotations

from app.architecture.tool_definition import ToolDef

TOOL_DEFINITIONS: tuple[ToolDef, ...] = (
    ToolDef(
        name="list_skills",
        description="列出当前作品的AI技能，包括内置技能和自定义技能。",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler_name="list_skills",
    ),
    ToolDef(
        name="list_skill_templates",
        description="列出可用于创建技能的模板。",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler_name="list_skill_templates_tool",
    ),
    ToolDef(
        name="list_skill_tools",
        description="列出技能中可推荐或禁用的工具名及元数据。",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler_name="list_skill_tools_tool",
    ),
    ToolDef(
        name="draft_skill",
        description="根据用户需求生成一个可编辑的技能草案，不会保存。用户想先看看技能怎么写时使用。",
        input_schema={
            "requirements": {"type": "string", "description": "用户想创建的技能需求"},
            "template_key": {"type": "string", "description": "可选模板key"},
            "scope": {
                "type": "string",
                "description": "global|project|writing|outline|characters|worldbuilding|cataloging|research",
            },
        },
        required=["requirements"],
        tool_type="generator",
        estimated_cost="free",
        handler_name="draft_skill",
    ),
    ToolDef(
        name="create_skill",
        description="创建AI技能。可直接提供完整字段；也可只提供requirements，系统会用模板生成技能并保存。",
        input_schema={
            "requirements": {
                "type": "string",
                "description": "用户想创建的技能需求，可用于自动生成技能草案",
            },
            "template_key": {"type": "string", "description": "可选模板key"},
            "name": {"type": "string", "description": "技能名称"},
            "description": {"type": "string", "description": "技能描述"},
            "trigger_examples": {
                "type": "array",
                "items": {"type": "string"},
                "description": "触发关键词/示例",
            },
            "system_prompt": {"type": "string", "description": "技能系统提示词"},
            "recommended_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "推荐工具",
            },
            "forbidden_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "禁用工具",
            },
            "scope": {
                "type": "string",
                "description": "global|project|writing|outline|characters|worldbuilding|cataloging|research",
            },
            "priority": {"type": "integer", "description": "优先级0-100"},
            "enabled": {"type": "boolean", "description": "是否启用"},
        },
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler_name="create_skill",
    ),
    ToolDef(
        name="update_skill",
        description="更新AI技能。可按ID或名称定位，修改触发词、提示词、范围、优先级、启用状态等。",
        input_schema={
            "id": {"type": "string", "description": "技能ID"},
            "skill_id": {"type": "string", "description": "兼容字段，同id"},
            "name": {"type": "string", "description": "技能名称，可用于定位或重命名"},
            "description": {"type": "string", "description": "技能描述"},
            "trigger_examples": {
                "type": "array",
                "items": {"type": "string"},
                "description": "触发关键词/示例",
            },
            "system_prompt": {"type": "string", "description": "技能系统提示词"},
            "recommended_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "推荐工具",
            },
            "forbidden_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "禁用工具",
            },
            "scope": {"type": "string", "description": "技能适用范围"},
            "priority": {"type": "integer", "description": "优先级0-100"},
            "enabled": {"type": "boolean", "description": "是否启用"},
        },
        tool_type="write",
        estimated_cost="free",
        handler_name="update_skill",
    ),
    ToolDef(
        name="delete_skill",
        description="删除自定义AI技能。内置技能不可删除，只能禁用。危险操作，必须在用户确认后调用。",
        input_schema={
            "id": {"type": "string", "description": "技能ID"},
            "skill_id": {"type": "string", "description": "兼容字段，同id"},
            "name": {"type": "string", "description": "技能名称，id为空时用于定位"},
        },
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler_name="delete_skill",
    ),
    ToolDef(
        name="reset_skill",
        description="将内置技能恢复默认值。仅适用于内置技能。",
        input_schema={
            "id": {"type": "string", "description": "技能ID"},
            "skill_id": {"type": "string", "description": "兼容字段，同id"},
            "name": {"type": "string", "description": "技能名称，id为空时用于定位"},
        },
        tool_type="write",
        estimated_cost="free",
        handler_name="reset_skill",
    ),
    ToolDef(
        name="preview_skill_match",
        description="预览某条用户消息会匹配哪些技能。用于调试技能触发效果。",
        input_schema={
            "message": {"type": "string", "description": "用于测试触发的用户消息"},
            "scope": {"type": "string", "description": "助手范围，默认project"},
            "candidate": {"type": "object", "description": "未保存技能草案，可选"},
        },
        required=["message"],
        tool_type="analysis",
        estimated_cost="free",
        handler_name="preview_skill_match_tool",
    ),
    ToolDef(
        name="list_skill_versions",
        description="列出技能版本历史。可按ID或名称定位。",
        input_schema={
            "id": {"type": "string", "description": "技能ID"},
            "skill_id": {"type": "string", "description": "兼容字段，同id"},
            "name": {"type": "string", "description": "技能名称，id为空时用于定位"},
        },
        tool_type="read",
        estimated_cost="free",
        handler_name="list_skill_versions_tool",
    ),
    ToolDef(
        name="ensure_builtin_skills",
        description="确保当前作品已初始化全部内置技能。通常系统会自动处理，用户要求恢复内置技能入口时使用。",
        input_schema={},
        tool_type="write",
        estimated_cost="free",
        handler_name="ensure_builtin_skills_tool",
    ),
    ToolDef(
        name="web_search",
        description="搜索互联网获取最新信息。适用于查证事实、获取参考资料（历史/地理/文化/科技等）。只读，可在任何阶段使用。",
        input_schema={
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "最大结果数，默认5，上限10"},
        },
        required=["query"],
        tool_type="web",
        estimated_cost="low",
        handler_name="web_search",
    ),
    ToolDef(
        name="get_mcp_permission_status",
        description="Report current MCP permission status: effective pack, source, CLI override status.",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler_name="get_mcp_permission_status",
    ),
    ToolDef(
        name="get_moshu_usage_guide",
        description="First-stop guide for Claude Code/Codex/external agents. Explains the correct Siming workflow for importing, API-free cataloging, internal cataloging, external writing, and verification. API-free; call this when unsure which tools to use.",
        input_schema={
            "scenario": {
                "type": "string",
                "description": "quickstart|import_file|cataloging_no_api|cataloging_internal|writing_no_api|writing_internal",
            },
            "no_api": {
                "type": "boolean",
                "description": "True when Siming internal API is unavailable or the external agent should do the reasoning itself.",
            },
        },
        tool_type="read",
        estimated_cost="free",
        handler_name="get_moshu_usage_guide",
    ),
    ToolDef(
        name="list_prompt_packs",
        description="List available public prompt packs. Returns pack_id, scope, title, summary.",
        input_schema={
            "scope": {
                "type": "string",
                "description": "Filter by scope: new_project|chapter_writing|chapter_review|character_design|worldbuilding|outline_planning|cataloging|anti_ai_review",
            }
        },
        tool_type="read",
        estimated_cost="free",
        handler_name="list_prompt_packs",
    ),
    ToolDef(
        name="get_prompt_pack",
        description="Get a specific prompt pack with full system prompt, workflow, quality rubric, and forbidden patterns.",
        input_schema={
            "scope": {
                "type": "string",
                "description": "Prompt scope: chapter_writing|chapter_review|new_project|character_design|worldbuilding|outline_planning|cataloging|anti_ai_review",
            },
            "mode": {"type": "string", "description": "Mode: quality|fast|external_no_api"},
            "pack_id": {
                "type": "string",
                "description": "Direct pack_id lookup (overrides scope/mode)",
            },
        },
        tool_type="read",
        estimated_cost="free",
        handler_name="get_prompt_pack",
    ),
    ToolDef(
        name="get_tool_playbook",
        description="Get a tool usage playbook explaining how to use a specific tool in a given scenario.",
        input_schema={
            "tool_name": {"type": "string", "description": "Tool name to get playbook for"},
            "scenario": {
                "type": "string",
                "description": "Scenario: external_writing|internal_writing|external_cataloging|internal_cataloging",
            },
        },
        required=["tool_name"],
        tool_type="read",
        estimated_cost="free",
        handler_name="get_tool_playbook",
    ),
    ToolDef(
        name="get_quality_rubric",
        description="Get quality rubric with scoring dimensions and passing criteria.",
        input_schema={
            "scope": {"type": "string", "description": "Scope: chapter_writing|chapter_review"},
            "pack_id": {"type": "string", "description": "Direct pack_id lookup (overrides scope)"},
        },
        tool_type="read",
        estimated_cost="free",
        handler_name="get_quality_rubric",
    ),
)


__all__ = ["TOOL_DEFINITIONS"]
