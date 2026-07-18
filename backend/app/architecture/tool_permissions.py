"""Permission policy for workspace tools."""

from __future__ import annotations

from typing import Protocol

from .tool_definition import ToolDef


class ToolCollection(Protocol):
    """Minimal registry surface needed by the permission policy."""

    def all_names(self) -> list[str]: ...

    def get(self, name: str) -> ToolDef | None: ...


WRITE_PROJECT_DATA = {
    "create_chapter",
    "update_chapter",
    "delete_chapter",
    "restore_chapter_version",
    "create_character",
    "update_character",
    "delete_character",
    "create_outline_node",
    "create_outline_nodes",
    "update_outline_node",
    "delete_outline_node",
    "create_worldbuilding_entry",
    "update_worldbuilding_entry",
    "delete_worldbuilding_entry",
    "create_relationship",
    "update_relationship",
    "delete_relationship",
    "remember",
    "forget",
    "update_cataloging_candidate",
    "apply_pending_cataloging",
    "set_cataloging_mode",
    "set_daily_word_goal",
    "apply_external_story_updates",
    "archive_chapter_after_write",
    "repair_story_granularity",
    "apply_novel_blueprint",
    "submit_novel_creation_stage",
    "save_external_cataloging_facts",
    "save_external_cataloging_candidates",
    "write_project_file",
    "sync_project_files",
}

MANAGEMENT_TOOLS = {
    "create_project",
    "update_project_info",
    "delete_project",
    "import_text_as_chapters",
    "import_file_as_chapters",
    "import_file_as_project",
    "import_deconstruct_report",
    "export_project",
    "create_scheduled_task",
    "update_scheduled_task",
    "delete_scheduled_task",
    "run_scheduled_task_now",
    "create_skill",
    "update_skill",
    "delete_skill",
    "reset_skill",
    "ensure_builtin_skills",
    "start_cataloging_job",
    "start_deconstruct_job",
    "resume_cataloging_job",
    "retry_current_cataloging_chapter",
    "rerun_cataloging_resolution_current",
    "rerun_failed_deconstruct_chunks",
    "cancel_cataloging_job",
    "pause_cataloging_job",
    "set_cataloging_mode",
}

DESTRUCTIVE_TOOLS = {
    "delete_project",
    "delete_chapter",
    "delete_character",
    "delete_outline_node",
    "delete_worldbuilding_entry",
    "delete_relationship",
    "delete_scheduled_task",
    "delete_skill",
    "merge_duplicate_characters",
}

HIGH_RISK_TOOLS = {
    "start_cataloging_job",
    "start_deconstruct_job",
    "resume_cataloging_job",
    "retry_current_cataloging_chapter",
    "rerun_cataloging_resolution_current",
    "rerun_failed_deconstruct_chunks",
    "run_scheduled_task_now",
    "cancel_cataloging_job",
}

INTERNAL_LLM_TOOLS = {
    "chapter_writer",
    "character_writer",
    "outline_writer",
    "worldbuilding_writer",
    "rewrite_text",
    "expand_text",
    "continue_text",
    "roleplay_character",
    "dialogue_battle",
    "draft_skill",
    "suggest_conflicts",
    "design_plot",
    "evaluate_chapter",
    "detect_character_changes",
    "detect_new_worldbuilding",
    "detect_worldbuilding_conflicts",
    "start_cataloging_job",
    "start_deconstruct_job",
    "resume_cataloging_job",
    "retry_current_cataloging_chapter",
    "rerun_cataloging_resolution_current",
    "rerun_failed_deconstruct_chunks",
}

REPORTING_TOOLS = {
    "start_agent_run",
    "finish_agent_run",
    "append_draft_chunk",
    "mark_draft_ready",
}


def _permission_values(tool: ToolDef) -> tuple[set[str], str, bool]:
    name = tool.name
    tags: set[str] = set()
    risk = "safe"
    writes = False

    if tool.tool_type in ("read", "analysis", "web"):
        tags = {"read", "analysis"} if tool.tool_type == "analysis" else {"read", "search"}
    elif tool.tool_type == "generator":
        tags = {"generator", "draft"}
        risk = "low"
    elif tool.tool_type == "memory":
        if name == "remember":
            tags, risk, writes = {"memory", "write"}, "low", True
        elif name == "forget":
            tags, risk, writes = {"memory", "delete"}, "medium", True
        else:
            tags = {"memory", "read"}
    elif tool.tool_type == "scheduler":
        tags, risk, writes = {"write", "management"}, "medium", True
    elif tool.tool_type == "write":
        if name in DESTRUCTIVE_TOOLS:
            tags, risk, writes = {"write", "delete"}, "destructive", True
        elif name in MANAGEMENT_TOOLS:
            risk = "high" if name in HIGH_RISK_TOOLS else "medium"
            tags, writes = {"write", "management"}, True
        elif name in WRITE_PROJECT_DATA:
            tags, risk, writes = {"write", "create"}, "medium", True
        else:
            tags, risk = {"write", "management"}, "low"

    if name in INTERNAL_LLM_TOOLS or tool.tool_type == "generator":
        tags = {"internal_llm", "model"}
        if tool.tool_type == "generator":
            tags.add("generator")
        if tool.tool_type == "analysis":
            tags.add("analysis")
        if tool.tool_type == "write":
            tags.update({"write", "management"})
            writes = True
        risk = "high" if name in HIGH_RISK_TOOLS or tool.tool_type == "write" else "medium"

    if name in REPORTING_TOOLS or name.startswith("report_"):
        return {"read", "telemetry"}, "safe", False
    return tags, risk, writes


def _permission_pack(tool: ToolDef, writes: bool) -> str:
    name = tool.name
    if name in REPORTING_TOOLS or name.startswith("report_"):
        return "readonly_collaboration"
    if name in INTERNAL_LLM_TOOLS or tool.tool_type == "generator":
        return "internal_llm"
    if name in DESTRUCTIVE_TOOLS:
        return "trusted_local_maintenance"
    if name in MANAGEMENT_TOOLS or tool.tool_type == "scheduler":
        return "project_management"
    if tool.tool_type in ("read", "analysis", "web"):
        return "readonly_collaboration"
    if tool.tool_type == "memory":
        return "project_writing" if writes else "readonly_collaboration"
    return "project_writing" if writes else "project_management"


def classify_tool_definitions(tools: ToolCollection) -> None:
    """Assign derived permission metadata to unclassified tool definitions."""

    for name in tools.all_names():
        tool = tools.get(name)
        if tool is None or tool.permission_tags:
            continue
        tags, risk, writes = _permission_values(tool)
        object.__setattr__(tool, "permission_tags", tags)
        object.__setattr__(tool, "risk_level", risk)
        object.__setattr__(tool, "writes_project_data", writes)
        object.__setattr__(tool, "mcp_permission_pack", _permission_pack(tool, writes))


__all__ = ["classify_tool_definitions"]
