"""Shared model-controlled workspace tool categories.

Categories describe broad business capabilities.  They never infer a user's
intent: the model selects categories through ``set_tool_categories`` and the
runtime only validates that selection and intersects it with authorization.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

TOOL_CATEGORY_CONTROLLER = "set_tool_categories"

TOOL_CATEGORY_METADATA: dict[str, dict[str, str]] = {
    "project_files": {
        "label": "作品与文件",
        "description": "读取或维护作品资料、项目文件、导入导出和写作统计",
    },
    "story_knowledge": {
        "label": "故事资料",
        "description": "查询或维护大纲、章节、角色、关系和世界观实体",
    },
    "writing_context": {
        "label": "写作与上下文",
        "description": "按目标检索写作上下文，并生成、续写、改写或保存草稿",
    },
    "cataloging": {
        "label": "作品建档",
        "description": "启动和控制建档任务，处理候选事实及建档状态",
    },
    "analysis_governance": {
        "label": "分析与治理",
        "description": "执行质量、冲突、拆书、叙事治理和故事粒度检查",
    },
    "creation_data": {
        "label": "立项资料",
        "description": "读取或维护立项会话、阶段资料、实体、依赖和字段锁",
    },
    "creation_flow": {
        "label": "立项流程",
        "description": "生成和确认立项资料，管理版本、任务、导入及正式建书",
    },
    "agent_runtime": {
        "label": "Agent 与记忆",
        "description": "管理 Agent 运行、进度、草稿缓冲、计划任务和持久记忆",
    },
    "extensions": {
        "label": "扩展能力",
        "description": "使用 Skill、联网、MCP 指南、提示词包和质量规范",
    },
}


TOOL_NAMES_BY_CATEGORY: dict[str, frozenset[str]] = {
    "project_files": frozenset({
        "list_projects", "get_project_info", "get_project_creation_brief",
        "update_project_creation_brief", "get_project_files_info",
        "list_project_files", "read_project_file", "search_project_files",
        "write_project_file", "sync_project_files", "create_project",
        "update_project_info", "delete_project", "export_project",
        "get_export_word_count", "preview_import_splits",
        "import_text_as_chapters", "import_file_as_chapters",
        "import_file_as_project", "get_today_writing_stats",
        "get_writing_stats_history", "set_daily_word_goal",
    }),
    "story_knowledge": frozenset({
        "list_duplicate_characters", "preview_character_merge",
        "merge_duplicate_characters", "search_characters", "search_chapters",
        "search_outline", "search_outline_tree", "search_worldbuilding",
        "search_relationships", "list_characters", "list_chapters",
        "list_worldbuilding", "create_worldbuilding_entry",
        "update_worldbuilding_entry", "delete_worldbuilding_entry",
        "create_outline_node", "create_outline_nodes", "update_outline_node",
        "delete_outline_node", "create_character", "update_character",
        "delete_character", "create_relationship", "update_relationship",
        "delete_relationship", "list_chapter_versions",
        "restore_chapter_version", "diff_chapter_versions", "delete_chapter",
    }),
    "writing_context": frozenset({
        "search_context", "preview_rag_context",
        "explain_context_selection", "prepare_task_context",
        "search_task_context", "submit_context_evidence", "design_plot",
        "chapter_writer", "character_writer", "outline_writer",
        "worldbuilding_writer", "rewrite_text", "expand_text", "continue_text",
        "roleplay_character", "dialogue_battle",
        "prepare_external_writing_context", "save_external_chapter_draft",
        "save_external_outline_draft",
        "get_external_chapter_draft", "record_external_quality_review",
    }),
    "cataloging": frozenset({
        "start_cataloging_job", "list_cataloging_jobs", "get_cataloging_job",
        "get_cataloging_control_state", "set_cataloging_mode",
        "list_cataloging_candidates", "list_cataloging_facts",
        "update_cataloging_candidate", "apply_pending_cataloging",
        "retry_current_cataloging_chapter", "rerun_cataloging_resolution_current",
        "pause_cataloging_job", "resume_cataloging_job", "cancel_cataloging_job",
        "start_external_cataloging_job", "get_next_external_cataloging_chapter",
        "save_external_cataloging_facts", "save_external_cataloging_candidates",
        "verify_external_cataloging_progress", "get_project_archive_status",
    }),
    "analysis_governance": frozenset({
        "preview_deconstruct_source", "list_deconstruct_reports",
        "get_deconstruct_report", "start_deconstruct_job",
        "rerun_failed_deconstruct_chunks", "import_deconstruct_report",
        "suggest_conflicts", "detect_character_changes",
        "detect_new_worldbuilding", "detect_worldbuilding_conflicts",
        "detect_forbidden_patterns", "evaluate_chapter",
        "update_narrative_ledger_entry", "get_narrative_ledger",
        "get_narrative_governance", "apply_narrative_governance_candidates",
        "list_narrative_checkpoints", "diff_narrative_checkpoint",
        "restore_narrative_governance_checkpoint", "inspect_story_granularity",
        "repair_story_granularity",
    }),
    "creation_data": frozenset({
        "start_novel_creation_session",
        "get_creation_session", "get_creation_snapshot", "get_creation_operation",
        "patch_creation_session", "get_creation_artifact",
        "list_creation_artifacts", "get_creation_dependencies",
        "get_creation_dependency_graph", "validate_creation_consistency",
        "patch_creation_artifact", "lock_creation_fields",
        "unlock_creation_fields", "undo_creation_artifact",
        "list_creation_entities", "get_creation_entity", "patch_creation_entity",
        "delete_creation_entity",
    }),
    "creation_flow": frozenset({
        "list_creation_artifact_versions", "get_creation_artifact_diff",
        "restore_creation_artifact_version", "confirm_creation_artifact",
        "generate_creation_artifact", "refine_creation_artifact",
        "regenerate_creation_artifact", "cancel_creation_operation",
        "pause_creation_operation", "resume_creation_operation",
        "retry_creation_operation", "validate_creation_session",
        "finalize_creation_session", "import_creation_material",
        "preview_creation_import", "apply_creation_import",
        "list_imported_files", "read_imported_file",
    }),
    "agent_runtime": frozenset({
        "list_scheduled_tasks", "create_scheduled_task", "update_scheduled_task",
        "delete_scheduled_task", "run_scheduled_task_now", "start_agent_run",
        "report_agent_plan", "report_agent_progress", "report_context_selected",
        "append_draft_chunk", "mark_draft_ready", "finish_agent_run",
        "start_local_cli_agent_run", "wait_local_cli_agent_run", "remember",
        "recall", "forget", "list_memories",
    }),
    "extensions": frozenset({
        "list_skills", "list_skill_templates", "list_skill_tools", "draft_skill",
        "create_skill", "update_skill", "delete_skill", "reset_skill",
        "list_skill_versions", "ensure_builtin_skills", "web_search",
        "get_mcp_permission_status", "get_moshu_usage_guide",
        "list_prompt_packs", "get_prompt_pack", "get_tool_playbook",
        "get_quality_rubric",
    }),
}


def _build_category_by_tool() -> dict[str, str]:
    result: dict[str, str] = {}
    for category, names in TOOL_NAMES_BY_CATEGORY.items():
        if category not in TOOL_CATEGORY_METADATA:
            raise RuntimeError(f"工具类别缺少描述：{category}")
        for name in names:
            previous = result.get(name)
            if previous is not None:
                raise RuntimeError(f"工具 {name} 同时属于 {previous} 和 {category}")
            result[name] = category
    return result


TOOL_CATEGORY_BY_NAME = _build_category_by_tool()


def tool_category_for_name(tool_name: str) -> str:
    try:
        return TOOL_CATEGORY_BY_NAME[tool_name]
    except KeyError as exc:
        raise ValueError(f"工具尚未分配 Agent 类别：{tool_name}") from exc


def normalize_tool_categories(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("enabled_categories 必须是数组")
    normalized: list[str] = []
    for raw in value:
        category = str(raw or "").strip()
        if category not in TOOL_CATEGORY_METADATA:
            raise ValueError(f"未知工具类别：{category or '(空)'}")
        if category not in normalized:
            normalized.append(category)
    return tuple(normalized)


def tool_names_for_categories(categories: Iterable[str]) -> frozenset[str]:
    selected = normalize_tool_categories(list(categories))
    names: set[str] = set()
    for category in selected:
        names.update(TOOL_NAMES_BY_CATEGORY[category])
    return frozenset(names)


def tool_category_controller_schema() -> dict[str, Any]:
    descriptions = "; ".join(
        f"{category}={meta['label']}（{meta['description']}）"
        for category, meta in TOOL_CATEGORY_METADATA.items()
    )
    return {
        "type": "function",
        "function": {
            "name": TOOL_CATEGORY_CONTROLLER,
            "description": (
                "替换下一模型步骤可使用的工具类别。调用后当前模型步骤立即结束；"
                "空数组表示关闭全部业务工具。只选择完成最新用户任务所需的类别。"
                f"{descriptions}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled_categories": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": list(TOOL_CATEGORY_METADATA),
                        },
                        "uniqueItems": True,
                        "description": "下一模型步骤开放的完整类别集合，使用替换而非追加语义",
                    },
                },
                "required": ["enabled_categories"],
                "additionalProperties": False,
            },
        },
    }


def tool_category_contract() -> dict[str, Any]:
    return {
        "version": 1,
        "controller": TOOL_CATEGORY_CONTROLLER,
        "categories": {
            category: {
                **metadata,
                "tools": sorted(TOOL_NAMES_BY_CATEGORY[category]),
            }
            for category, metadata in TOOL_CATEGORY_METADATA.items()
        },
    }


__all__ = [
    "TOOL_CATEGORY_BY_NAME",
    "TOOL_CATEGORY_CONTROLLER",
    "TOOL_CATEGORY_METADATA",
    "TOOL_NAMES_BY_CATEGORY",
    "normalize_tool_categories",
    "tool_category_contract",
    "tool_category_controller_schema",
    "tool_category_for_name",
    "tool_names_for_categories",
]
