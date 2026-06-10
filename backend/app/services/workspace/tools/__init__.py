"""Workspace tool handlers grouped by domain."""
from .chapter_writer import chapter_writer
from .chapters import create_chapter, delete_chapter, update_chapter
from .context_preview import preview_writing_context
from .character_writer import character_writer
from .characters import create_character, delete_character, update_character
from .character_merge import list_duplicate_characters, merge_duplicate_characters, preview_character_merge
from .outline import create_outline_node, delete_outline_node, update_outline_node
from .outline_writer import outline_writer
from .plot import design_plot
from .relationships import create_relationship, delete_relationship, update_relationship
from .analysis import detect_character_changes, detect_forbidden_patterns, detect_new_worldbuilding, detect_worldbuilding_conflicts, evaluate_chapter, suggest_conflicts
from .roleplay import dialogue_battle, roleplay_character
from .text_operations import continue_text, expand_text, rewrite_text
from .search import (
    list_characters,
    list_chapters,
    list_worldbuilding,
    search_chapters,
    search_characters,
    search_outline,
    search_outline_tree,
    search_relationships,
    search_worldbuilding,
)
from .memory import forget, list_memories, recall, remember
from .web_search import web_search
from .rag_tools import search_context, preview_rag_context, explain_context_selection
from .worldbuilding import (
    create_worldbuilding_entry,
    delete_worldbuilding_entry,
    update_worldbuilding_entry,
)
from .worldbuilding_writer import worldbuilding_writer
from .projects import create_project, delete_project, get_project_info, list_projects, update_project_info
from .scheduler import (
    create_scheduled_task,
    delete_scheduled_task,
    list_scheduled_tasks,
    run_scheduled_task_now,
    update_scheduled_task,
)
from .skills import (
    create_skill,
    delete_skill,
    draft_skill,
    ensure_builtin_skills_tool,
    list_skill_templates_tool,
    list_skill_tools_tool,
    list_skill_versions_tool,
    list_skills,
    preview_skill_match_tool,
    reset_skill,
    update_skill,
)
from .export import export_project, get_export_word_count
from .import_tools import import_file_as_chapters, import_file_as_project, import_text_as_chapters, preview_import_splits
from .cataloging import (
    apply_pending_cataloging,
    cancel_cataloging_job,
    get_cataloging_job,
    list_cataloging_candidates,
    list_cataloging_facts,
    list_cataloging_jobs,
    pause_cataloging_job,
    rerun_cataloging_resolution_current,
    resume_cataloging_job,
    retry_current_cataloging_chapter,
    set_cataloging_mode,
    start_cataloging_job,
    update_cataloging_candidate,
)
from .deconstruct import (
    get_deconstruct_report,
    import_deconstruct_report_tool,
    list_deconstruct_reports,
    preview_deconstruct_source,
    rerun_failed_deconstruct_chunks,
    start_deconstruct_job,
)
from .stats import get_today_writing_stats, get_writing_stats_history, set_daily_word_goal
from .project_status import get_project_archive_status

__all__ = [
    "chapter_writer",
    "preview_writing_context",
    "character_writer",
    "outline_writer",
    "worldbuilding_writer",
    "create_chapter",
    "update_chapter",
    "delete_chapter",
    "create_character",
    "update_character",
    "delete_character",
    "list_duplicate_characters",
    "preview_character_merge",
    "merge_duplicate_characters",
    "create_outline_node",
    "update_outline_node",
    "delete_outline_node",
    "create_relationship",
    "update_relationship",
    "delete_relationship",
    "create_worldbuilding_entry",
    "update_worldbuilding_entry",
    "delete_worldbuilding_entry",
    "list_characters",
    "list_chapters",
    "list_worldbuilding",
    "search_characters",
    "search_chapters",
    "search_outline",
    "search_outline_tree",
    "search_worldbuilding",
    "search_relationships",
    "roleplay_character",
    "dialogue_battle",
    "rewrite_text",
    "expand_text",
    "continue_text",
    "suggest_conflicts",
    "design_plot",
    "detect_character_changes",
    "detect_new_worldbuilding",
    "detect_worldbuilding_conflicts",
    "detect_forbidden_patterns",
    "evaluate_chapter",
    "web_search",
    "remember",
    "recall",
    "forget",
    "list_memories",
    "list_projects",
    "get_project_info",
    "create_project",
    "update_project_info",
    "delete_project",
    "list_scheduled_tasks",
    "create_scheduled_task",
    "update_scheduled_task",
    "delete_scheduled_task",
    "run_scheduled_task_now",
    "list_skills",
    "list_skill_templates_tool",
    "list_skill_tools_tool",
    "draft_skill",
    "create_skill",
    "update_skill",
    "delete_skill",
    "reset_skill",
    "preview_skill_match_tool",
    "list_skill_versions_tool",
    "ensure_builtin_skills_tool",
    "export_project",
    "get_export_word_count",
    "preview_import_splits",
    "import_text_as_chapters",
    "import_file_as_chapters",
    "import_file_as_project",
    "start_cataloging_job",
    "list_cataloging_jobs",
    "get_cataloging_job",
    "set_cataloging_mode",
    "list_cataloging_candidates",
    "list_cataloging_facts",
    "update_cataloging_candidate",
    "apply_pending_cataloging",
    "retry_current_cataloging_chapter",
    "rerun_cataloging_resolution_current",
    "pause_cataloging_job",
    "resume_cataloging_job",
    "cancel_cataloging_job",
    "preview_deconstruct_source",
    "list_deconstruct_reports",
    "get_deconstruct_report",
    "start_deconstruct_job",
    "rerun_failed_deconstruct_chunks",
    "import_deconstruct_report_tool",
    "get_today_writing_stats",
    "get_writing_stats_history",
    "set_daily_word_goal",
    "get_project_archive_status",
]
