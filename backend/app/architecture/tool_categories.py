"""Authoritative small tool groups, projected by the current authorized scope.

Freeform Agents select groups; fixed tasks receive their explicit tool set.
Neither path infers user intent from natural-language matching.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

TOOL_CATEGORY_CONTROLLER = "set_tool_categories"
MAX_ACTIVE_TOOL_CATEGORIES = 2

# scope, label, description, tool names. Each tool belongs to exactly one group.
_CATEGORY_DEFINITIONS = {
    "project_info": (
        "project",
        "作品信息",
        "查找、创建或维护作品",
        "list_projects get_project_info create_project update_project_info delete_project",
    ),
    "project_brief": (
        "project",
        "创作约束",
        "读取或修改作品创作约束",
        "get_project_creation_brief update_project_creation_brief",
    ),
    "project_files": (
        "project",
        "作品文件",
        "读取、搜索、写入或同步作品文件",
        "get_project_files_info list_project_files read_project_file search_project_files "
        "write_project_file sync_project_files",
    ),
    "project_transfer": (
        "project",
        "导入导出",
        "导入正文、预览拆章或导出作品",
        "export_project get_export_word_count preview_import_splits import_text_as_chapters "
        "import_file_as_chapters import_file_as_project",
    ),
    "writing_stats": (
        "project",
        "写作统计",
        "查看字数统计、设置日目标",
        "get_today_writing_stats get_writing_stats_history set_daily_word_goal",
    ),
    "chapters": (
        "project",
        "已保存章节",
        "查找章节、查看或恢复版本、删除章节",
        "search_chapters list_chapters list_chapter_versions restore_chapter_version "
        "diff_chapter_versions delete_chapter",
    ),
    "outline": (
        "project",
        "大纲节点",
        "查找或修改已有卷章场景；新纲用大纲规划生成草稿",
        "search_outline search_outline_tree "
        "update_outline_node delete_outline_node",
    ),
    "characters": (
        "project",
        "人物卡",
        "查找、创建或修改角色",
        "search_characters list_characters create_character update_character delete_character",
    ),
    "character_merge": (
        "project",
        "人物合并",
        "检查和合并重复角色",
        "list_duplicate_characters preview_character_merge merge_duplicate_characters",
    ),
    "relationships": (
        "project",
        "人物关系",
        "查找、创建或修改关系",
        "search_relationships create_relationship update_relationship delete_relationship",
    ),
    "worldbuilding": (
        "project",
        "世界观条目",
        "查找、创建或修改世界观",
        "search_worldbuilding list_worldbuilding create_worldbuilding_entry "
        "update_worldbuilding_entry delete_worldbuilding_entry",
    ),
    "writing_context": (
        "project",
        "上下文检索",
        "检索写作依据、预览和提交上下文证据",
        "search_context preview_rag_context explain_context_selection prepare_task_context "
        "search_task_context submit_context_evidence",
    ),
    "chapter_writing": (
        "project",
        "章节正文草稿",
        "生成章节正文，准备外部写作上下文或提交未保存草稿",
        "chapter_writer prepare_external_writing_context save_external_chapter_draft "
        "get_external_chapter_draft",
    ),
    "prose_editing": (
        "project",
        "正文润色",
        "改写、扩写或续写文本",
        "rewrite_text expand_text continue_text",
    ),
    "story_design": (
        "project",
        "设定生成",
        "生成剧情、人物、世界观或大纲草稿",
        "design_plot character_writer outline_writer worldbuilding_writer "
        "save_external_outline_draft",
    ),
    "roleplay": (
        "project",
        "角色对话",
        "角色扮演或对话推演",
        "roleplay_character dialogue_battle",
    ),
    "quality_review": (
        "project",
        "章节评估",
        "评分、检查禁用模式或记录评审",
        "evaluate_chapter detect_forbidden_patterns record_external_quality_review "
        "get_quality_rubric",
    ),
    "cataloging_jobs": (
        "project",
        "建档任务",
        "创建、查询、暂停、恢复或取消建档任务",
        "start_cataloging_job list_cataloging_jobs get_cataloging_job pause_cataloging_job "
        "resume_cataloging_job cancel_cataloging_job",
    ),
    "cataloging_control": (
        "project",
        "建档状态",
        "查看建档状态、模式和修复当前章",
        "get_cataloging_control_state set_cataloging_mode get_project_archive_status "
        "retry_current_cataloging_chapter repair_cataloging_plan_current",
    ),
    "cataloging_candidates": (
        "project",
        "建档候选",
        "读取、修改、确认应用候选事实",
        "list_cataloging_candidates list_cataloging_facts update_cataloging_candidate "
        "apply_pending_cataloging",
    ),
    "cataloging_extract": (
        "project",
        "建档提取",
        "外部模型读取本章档案、提交候选并验证",
        "start_external_cataloging_job get_next_external_cataloging_chapter "
        "read_cataloging_archive save_external_cataloging_candidates "
        "verify_external_cataloging_progress",
    ),
    "deconstruct": (
        "project",
        "拆书",
        "预览来源、启动拆书或读取导入报告",
        "preview_deconstruct_source list_deconstruct_reports get_deconstruct_report "
        "start_deconstruct_job rerun_failed_deconstruct_chunks import_deconstruct_report",
    ),
    "consistency": (
        "project",
        "设定检查",
        "检查人物、世界观变化和冲突",
        "suggest_conflicts detect_character_changes detect_new_worldbuilding "
        "detect_worldbuilding_conflicts",
    ),
    "narrative_state": (
        "project",
        "叙事治理",
        "读取、修改叙事账本或治理候选",
        "update_narrative_ledger_entry get_narrative_ledger get_narrative_governance "
        "apply_narrative_governance_candidates",
    ),
    "narrative_history": (
        "project",
        "治理版本",
        "对比恢复治理版本、检查修复故事粒度",
        "list_narrative_checkpoints diff_narrative_checkpoint "
        "restore_narrative_governance_checkpoint inspect_story_granularity "
        "repair_story_granularity",
    ),
    "creation_session": (
        "creation",
        "立项概况",
        "读取会话和快照、修改基本约束",
        "get_creation_session get_creation_snapshot patch_creation_session",
    ),
    "creation_artifacts": (
        "creation",
        "阶段资料",
        "读取、修改或确认阶段资料",
        "get_creation_artifact list_creation_artifacts patch_creation_artifact "
        "confirm_creation_artifact",
    ),
    "creation_entities": (
        "creation",
        "立项实体",
        "检索、读取、修改或删除具体设定实体",
        "list_creation_entities get_creation_entity patch_creation_entity delete_creation_entity",
    ),
    "creation_dependencies": (
        "creation",
        "立项依赖",
        "检查资料依赖和一致性",
        "get_creation_dependencies get_creation_dependency_graph validate_creation_consistency",
    ),
    "creation_fields": (
        "creation",
        "字段锁",
        "锁定或解锁立项字段",
        "lock_creation_fields unlock_creation_fields",
    ),
    "creation_versions": (
        "creation",
        "立项版本",
        "查看、对比、恢复或撤销资料版本",
        "list_creation_artifact_versions get_creation_artifact_diff "
        "restore_creation_artifact_version undo_creation_artifact",
    ),
    "creation_generation": (
        "creation",
        "资料生成",
        "生成、细化或重生成指定阶段资料",
        "generate_creation_artifact refine_creation_artifact regenerate_creation_artifact",
    ),
    "creation_operations": (
        "creation",
        "立项任务",
        "查询、暂停、恢复、取消或重试生成任务",
        "get_creation_operation cancel_creation_operation pause_creation_operation "
        "resume_creation_operation retry_creation_operation",
    ),
    "creation_completion": (
        "creation",
        "正式建书",
        "校验立项并创建正式作品",
        "validate_creation_session finalize_creation_session",
    ),
    "creation_import": (
        "creation",
        "资料导入",
        "预览或应用已导入的立项资料",
        "preview_creation_import apply_creation_import",
    ),
    "creation_setup": (
        "creation",
        "立项与原始素材",
        "新建立项、导入或读取原始素材文件",
        "start_novel_creation_session import_creation_material list_imported_files "
        "read_imported_file",
    ),
    "scheduled_tasks": (
        "project",
        "定时任务",
        "创建、查询、修改或运行定时任务",
        "list_scheduled_tasks create_scheduled_task update_scheduled_task "
        "delete_scheduled_task run_scheduled_task_now",
    ),
    "agent_tracking": (
        "project",
        "执行进度",
        "开始结束运行、上报计划与进度",
        "start_agent_run report_agent_plan report_agent_progress report_context_selected "
        "finish_agent_run",
    ),
    "agent_execution": (
        "project",
        "外部执行",
        "草稿缓冲和本机 CLI 运行管理",
        "append_draft_chunk mark_draft_ready start_local_cli_agent_run wait_local_cli_agent_run",
    ),
    "memory": (
        "project",
        "记忆",
        "读取、保存或删除持久记忆",
        "remember recall forget list_memories",
    ),
    "skill_catalog": (
        "project",
        "技能查询",
        "查询技能、模板、工具和版本",
        "list_skills list_skill_templates list_skill_tools list_skill_versions "
        "ensure_builtin_skills",
    ),
    "skill_edit": (
        "project",
        "技能编辑",
        "创建、修改、删除或重置技能",
        "draft_skill create_skill update_skill delete_skill reset_skill",
    ),
    "extensions": (
        "project",
        "联网与指南",
        "联网检索、查询权限、提示词和工具指南",
        "web_search get_mcp_permission_status get_moshu_usage_guide list_prompt_packs "
        "get_prompt_pack get_tool_playbook",
    ),
}

TOOL_CATEGORY_METADATA: dict[str, dict[str, str]] = {
    category: {"scope": scope, "label": label, "description": description}
    for category, (scope, label, description, _) in _CATEGORY_DEFINITIONS.items()
}
TOOL_NAMES_BY_CATEGORY: dict[str, frozenset[str]] = {
    category: frozenset(names.split())
    for category, (_, _, _, names) in _CATEGORY_DEFINITIONS.items()
}
PROJECT_TOOL_CATEGORIES = frozenset(
    category
    for category, metadata in TOOL_CATEGORY_METADATA.items()
    if metadata["scope"] == "project"
)
CATALOGING_TOOL_CATEGORIES = frozenset({
    "cataloging_jobs", "cataloging_control", "cataloging_candidates", "cataloging_extract",
})


def _build_category_by_tool() -> dict[str, str]:
    result: dict[str, str] = {}
    for category, names in TOOL_NAMES_BY_CATEGORY.items():
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


def tool_categories_for_names(available_tool_names: Iterable[str]) -> tuple[str, ...]:
    available = set(available_tool_names)
    return tuple(
        category for category, names in TOOL_NAMES_BY_CATEGORY.items() if names & available
    )


def normalize_tool_categories(
    value: Any,
    *,
    available_tool_names: Iterable[str] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("enabled_categories 必须是数组")
    allowed = (
        set(tool_categories_for_names(available_tool_names))
        if available_tool_names is not None
        else set(TOOL_CATEGORY_METADATA)
    )
    normalized: list[str] = []
    for raw in value:
        category = str(raw or "").strip()
        if category not in allowed:
            raise ValueError(f"当前任务未开放工具类别：{category or '(空)'}")
        if category not in normalized:
            normalized.append(category)
    if len(normalized) > MAX_ACTIVE_TOOL_CATEGORIES:
        raise ValueError(
            f"每个模型步骤最多开放 {MAX_ACTIVE_TOOL_CATEGORIES} 个工具类别，请按需分步切换"
        )
    return tuple(normalized)


def tool_names_for_categories(categories: Iterable[str]) -> frozenset[str]:
    selected = normalize_tool_categories(list(categories))
    return frozenset(name for category in selected for name in TOOL_NAMES_BY_CATEGORY[category])


def tool_category_controller_schema(
    available_tool_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    categories = (
        tool_categories_for_names(available_tool_names)
        if available_tool_names is not None
        else tuple(TOOL_CATEGORY_METADATA)
    )
    descriptions = "; ".join(
        f"{category}={TOOL_CATEGORY_METADATA[category]['label']}（{TOOL_CATEGORY_METADATA[category]['description']}）"
        for category in categories
    )
    return {
        "type": "function",
        "function": {
            "name": TOOL_CATEGORY_CONTROLLER,
            "description": (
                f"替换下一模型步骤的工具类别，每次最多 {MAX_ACTIVE_TOOL_CATEGORIES} 类。"
                "调用后当前步骤立即结束；空数组关闭全部业务工具。只选当前步骤所需类别，之后可切换。"
                f"{descriptions}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled_categories": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(categories)},
                        "uniqueItems": True,
                        "maxItems": MAX_ACTIVE_TOOL_CATEGORIES,
                        "description": "下一步骤开放的完整类别集合，替换而非追加",
                    },
                },
                "required": ["enabled_categories"],
                "additionalProperties": False,
            },
        },
    }


def tool_category_contract() -> dict[str, Any]:
    return {
        "version": 2,
        "controller": TOOL_CATEGORY_CONTROLLER,
        "max_active_categories": MAX_ACTIVE_TOOL_CATEGORIES,
        "categories": {
            category: {**metadata, "tools": sorted(TOOL_NAMES_BY_CATEGORY[category])}
            for category, metadata in TOOL_CATEGORY_METADATA.items()
        },
    }
