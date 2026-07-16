"""Prompt-facing workspace vocabulary with no runtime registry dependency."""
from __future__ import annotations

SCOPE_LABELS = {
    "outline": "大纲规划",
    "characters": "角色管理",
    "worldbuilding": "世界观管理",
    "project": "项目规划",
}


# This is the compatibility surface exposed by the legacy workspace registry.
# Keeping the declaration independent prevents prompts from importing the
# concrete tool registry. A parity test fails whenever the two drift.
WORKSPACE_TOOL_NAMES = frozenset(
    """
    append_draft_chunk
    apply_external_story_updates
    apply_narrative_governance_candidates
    apply_novel_blueprint
    apply_pending_cataloging
    archive_chapter_after_write
    cancel_cataloging_job
    chapter_writer
    character_writer
    continue_text
    create_chapter
    create_character
    create_outline_node
    create_outline_nodes
    create_project
    create_relationship
    create_scheduled_task
    create_skill
    create_worldbuilding_entry
    delete_chapter
    delete_character
    delete_outline_node
    delete_project
    delete_relationship
    delete_scheduled_task
    delete_skill
    delete_worldbuilding_entry
    design_plot
    detect_character_changes
    detect_forbidden_patterns
    detect_new_worldbuilding
    detect_worldbuilding_conflicts
    dialogue_battle
    diff_chapter_versions
    diff_narrative_checkpoint
    draft_novel_blueprint
    draft_skill
    ensure_builtin_skills
    evaluate_chapter
    expand_text
    explain_context_selection
    export_project
    finish_agent_run
    forget
    generate_novel_creation_stage
    get_cataloging_control_state
    get_cataloging_job
    get_deconstruct_report
    get_export_word_count
    get_external_chapter_draft
    get_mcp_permission_status
    get_moshu_usage_guide
    get_narrative_governance
    get_narrative_ledger
    get_next_external_cataloging_chapter
    get_novel_creation_session
    get_project_archive_status
    get_project_files_info
    get_project_info
    get_prompt_pack
    get_quality_rubric
    get_today_writing_stats
    get_tool_playbook
    get_writing_stats_history
    import_deconstruct_report
    import_file_as_chapters
    import_file_as_project
    import_text_as_chapters
    inspect_story_granularity
    list_cataloging_candidates
    list_cataloging_facts
    list_cataloging_jobs
    list_chapter_versions
    list_chapters
    list_characters
    list_deconstruct_reports
    list_duplicate_characters
    list_imported_files
    list_memories
    list_narrative_checkpoints
    list_project_files
    list_projects
    list_prompt_packs
    list_scheduled_tasks
    list_skill_templates
    list_skill_tools
    list_skill_versions
    list_skills
    list_worldbuilding
    mark_draft_ready
    merge_duplicate_characters
    outline_writer
    pause_cataloging_job
    prepare_external_writing_context
    prepare_task_context
    preview_character_merge
    preview_deconstruct_source
    preview_import_splits
    preview_rag_context
    preview_skill_match
    preview_writing_context
    read_imported_file
    read_project_file
    recall
    record_external_quality_review
    remember
    repair_story_granularity
    report_agent_plan
    report_agent_progress
    report_context_selected
    rerun_cataloging_resolution_current
    rerun_failed_deconstruct_chunks
    reset_skill
    restore_chapter_version
    restore_narrative_governance_checkpoint
    resume_cataloging_job
    retry_current_cataloging_chapter
    review_novel_blueprint
    rewrite_text
    roleplay_character
    run_scheduled_task_now
    save_external_cataloging_candidates
    save_external_cataloging_facts
    save_external_chapter_draft
    search_chapters
    search_characters
    search_context
    search_outline
    search_outline_tree
    search_project_files
    search_relationships
    search_task_context
    search_worldbuilding
    set_cataloging_mode
    set_daily_word_goal
    start_agent_run
    start_cataloging_job
    start_deconstruct_job
    start_external_cataloging_job
    start_novel_creation_session
    submit_context_evidence
    submit_novel_creation_stage
    suggest_conflicts
    sync_project_files
    update_cataloging_candidate
    update_chapter
    update_character
    update_narrative_ledger_entry
    update_outline_node
    update_project_info
    update_relationship
    update_scheduled_task
    update_skill
    update_worldbuilding_entry
    verify_external_cataloging_progress
    web_search
    worldbuilding_writer
    write_project_file
    """.split()
)

AVAILABLE_WORKSPACE_TOOLS = ", ".join(sorted(WORKSPACE_TOOL_NAMES))


__all__ = [
    "AVAILABLE_WORKSPACE_TOOLS",
    "SCOPE_LABELS",
    "WORKSPACE_TOOL_NAMES",
]
