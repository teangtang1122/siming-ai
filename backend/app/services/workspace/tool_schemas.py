"""JSON Schema function definitions for all workspace tools — backward-compatible layer.

Delegates to the central registry. New code should import from registry directly.
"""
from __future__ import annotations

from collections.abc import Iterable

from .registry import registry


# ── Aggregated lists (derived from registry) ────────────────────────────

# Search/read/generate/analyze tools — allowed during information-gathering rounds
SEARCH_TOOL_SCHEMAS: list[dict] = registry.get_schemas(
    tool_types={"read", "analysis", "web", "memory", "generator"},
)

# Write tools — only allowed when the assistant is ready to commit changes
WRITE_TOOL_SCHEMAS: list[dict] = registry.get_schemas(
    tool_types={"write"},
)

ALL_TOOL_SCHEMAS: list[dict] = registry.get_schemas()

# Tool-name sets for quick classification
SEARCH_TOOL_NAMES: set[str] = registry.get_names_by_type("read") | registry.get_names_by_type("analysis") | registry.get_names_by_type("web") | registry.get_names_by_type("memory") | registry.get_names_by_type("generator")
WRITE_TOOL_NAMES: set[str] = registry.get_names_by_type("write")


def build_tool_schemas(*, search_only: bool = False) -> list[dict]:
    """Return the appropriate tool schema list.

    Args:
        search_only: If True, return only search/read tools (for info-gathering rounds).
                     If False, return all tools.

    Used by the agentic loop to expose different tools at different phases.
    """
    if search_only:
        return list(SEARCH_TOOL_SCHEMAS)
    return list(ALL_TOOL_SCHEMAS)


CORE_WORKSPACE_TOOLS = {
    "get_project_info",
    "list_chapters",
    "list_characters",
    "list_worldbuilding",
    "search_context",
    "recall",
    "remember",
    "forget",
}

SCOPE_TOOL_NAMES: dict[str, set[str]] = {
    "outline": {
        "search_outline",
        "search_outline_tree",
        "search_chapters",
        "search_characters",
        "search_worldbuilding",
        "search_relationships",
        "design_plot",
        "suggest_conflicts",
        "outline_writer",
        "create_outline_node",
        "create_outline_nodes",
        "update_outline_node",
        "delete_outline_node",
    },
    "characters": {
        "search_characters",
        "search_relationships",
        "search_chapters",
        "search_outline",
        "character_writer",
        "create_character",
        "update_character",
        "delete_character",
        "create_relationship",
        "update_relationship",
        "delete_relationship",
        "roleplay_character",
        "dialogue_battle",
        "detect_character_changes",
        "list_duplicate_characters",
        "preview_character_merge",
        "merge_duplicate_characters",
    },
    "worldbuilding": {
        "search_worldbuilding",
        "search_chapters",
        "search_outline",
        "search_characters",
        "worldbuilding_writer",
        "create_worldbuilding_entry",
        "update_worldbuilding_entry",
        "delete_worldbuilding_entry",
        "detect_new_worldbuilding",
        "detect_worldbuilding_conflicts",
    },
    "project": {
        "list_projects",
        "update_project_info",
        "create_project",
        "get_today_writing_stats",
        "get_writing_stats_history",
        "set_daily_word_goal",
        "preview_rag_context",
        "search_chapters",
        "search_outline",
        "search_outline_tree",
        "search_characters",
        "search_worldbuilding",
        "search_relationships",
    },
}

KEYWORD_TOOL_GROUPS: tuple[tuple[tuple[str, ...], set[str]], ...] = (
    (("写", "正文", "章节", "续写", "改写", "扩写", "润色", "重写", "对话", "质量", "评估"), {
        "search_outline",
        "search_outline_tree",
        "search_chapters",
        "search_characters",
        "search_relationships",
        "search_worldbuilding",
        "preview_writing_context",
        "design_plot",
        "roleplay_character",
        "dialogue_battle",
        "chapter_writer",
        "evaluate_chapter",
        "detect_forbidden_patterns",
        "detect_character_changes",
        "detect_new_worldbuilding",
        "archive_chapter_after_write",
        "create_chapter",
        "update_chapter",
        "rewrite_text",
        "expand_text",
        "continue_text",
    }),
    (("大纲", "规划下一章", "后续章节", "补大纲"), {
        "search_outline",
        "search_outline_tree",
        "search_chapters",
        "search_characters",
        "search_worldbuilding",
        "outline_writer",
        "create_outline_node",
        "create_outline_nodes",
        "update_outline_node",
    }),
    (("建档", "档案", "编目", "目录", "catalog", "cataloging"), {
        "start_cataloging_job",
        "list_cataloging_jobs",
        "get_cataloging_job",
        "get_cataloging_control_state",
        "set_cataloging_mode",
        "list_cataloging_candidates",
        "list_cataloging_facts",
        "update_cataloging_candidate",
        "apply_pending_cataloging",
        "archive_chapter_after_write",
        "inspect_story_granularity",
        "repair_story_granularity",
        "retry_current_cataloging_chapter",
        "rerun_cataloging_resolution_current",
        "pause_cataloging_job",
        "resume_cataloging_job",
        "cancel_cataloging_job",
        "get_project_archive_status",
    }),
    (("导入", "分章", "拆章", "txt", "docx", "文件"), {
        "list_imported_files",
        "read_imported_file",
        "preview_import_splits",
        "import_text_as_chapters",
        "import_file_as_chapters",
        "import_file_as_project",
    }),
    (("拆书", "拆解", "分析报告", "deconstruct"), {
        "preview_deconstruct_source",
        "list_deconstruct_reports",
        "get_deconstruct_report",
        "start_deconstruct_job",
        "rerun_failed_deconstruct_chunks",
        "import_deconstruct_report",
    }),
    (("导出", "pdf", "docx", "txt", "全文"), {
        "get_export_word_count",
        "export_project",
    }),
    (("定时", "自动任务", "提醒", "周期", "每天", "每周", "监控"), {
        "list_scheduled_tasks",
        "create_scheduled_task",
        "update_scheduled_task",
        "delete_scheduled_task",
        "run_scheduled_task_now",
    }),
    (("技能", "规则", "提示词", "风格", "偏好"), {
        "list_skills",
        "list_skill_templates",
        "list_skill_tools",
        "list_skill_versions",
        "preview_skill_match",
        "draft_skill",
        "create_skill",
        "update_skill",
        "delete_skill",
        "reset_skill",
        "ensure_builtin_skills",
    }),
    (("搜索", "联网", "查资料", "资料", "真实", "历史", "地理", "文化"), {
        "web_search",
    }),
    (("外部", "agent", "claude", "codex", "opencode", "本机cli", "mcp"), {
        "get_moshu_usage_guide",
        "get_mcp_permission_status",
        "get_project_files_info",
        "list_project_files",
        "read_project_file",
        "search_project_files",
        "start_local_cli_agent_run",
    }),
)

SELECTED_TEXT_TOOLS = {
    "rewrite_text",
    "expand_text",
    "continue_text",
    "detect_forbidden_patterns",
}


def select_workspace_tool_names(
    *,
    scope: str,
    message: str,
    selected_text: bool = False,
) -> list[str]:
    """Pick a compact, task-relevant tool list for the workspace assistant.

    The full registry is intentionally large for external agents. Internal web
    chat should expose a smaller surface so local models do not spend most of
    their context on irrelevant tool schemas.
    """
    names = set(CORE_WORKSPACE_TOOLS)
    names.update(SCOPE_TOOL_NAMES.get(scope, SCOPE_TOOL_NAMES["project"]))

    lowered = (message or "").lower()
    for keywords, group in KEYWORD_TOOL_GROUPS:
        if any(keyword.lower() in lowered for keyword in keywords):
            names.update(group)
    if selected_text:
        names.update(SELECTED_TEXT_TOOLS)

    # Keep destructive/system-wide tools out unless their keyword group added them.
    names = {name for name in names if registry.get(name) is not None}
    return sorted(names)


def build_workspace_tool_schemas(tool_names: Iterable[str]) -> list[dict]:
    wanted = set(tool_names)
    schemas: list[dict] = []
    for schema in ALL_TOOL_SCHEMAS:
        name = schema.get("function", {}).get("name")
        if name in wanted:
            schemas.append(schema)
    return schemas
