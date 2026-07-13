"""Central tool registry for workspace assistant.

Single source of truth for tool metadata, schemas, and handler bindings.
Adding a new tool requires only one change: register a ToolDef here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from .types import ToolHandler


# ---------------------------------------------------------------------------
# ToolDef — metadata for a single tool
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict  # JSON Schema "properties" dict
    required: list[str] = field(default_factory=list)
    tool_type: str = "read"  # read | write | analysis | generator | web | memory | scheduler
    idempotent: bool = False
    requires_confirmation: bool = False
    estimated_cost: str = "free"  # free | low | medium | high
    handler: ToolHandler | None = None

    # Phase 9: permission pack metadata
    permission_tags: set[str] = field(default_factory=set)
    risk_level: str = "safe"  # safe | low | medium | high | destructive
    writes_project_data: bool = False
    expose_to_internal_agent: bool = True
    expose_to_scheduler: bool = True
    expose_to_mcp: bool = True
    mcp_permission_pack: str = ""  # derived from tool_type if empty


# ---------------------------------------------------------------------------
# ToolRegistry — manages all registered tools
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Central registry for workspace tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool_def: ToolDef) -> None:
        self._tools[tool_def.name] = tool_def

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def get_handler(self, name: str) -> ToolHandler | None:
        td = self._tools.get(name)
        return td.handler if td else None

    def all_names(self) -> list[str]:
        return list(self._tools.keys())

    def get_schemas(
        self,
        *,
        tool_types: set[str] | None = None,
        exclude_types: set[str] | None = None,
    ) -> list[dict]:
        """Return OpenAI function-calling format dicts, optionally filtered by type."""
        result: list[dict] = []
        for td in self._tools.values():
            if tool_types and td.tool_type not in tool_types:
                continue
            if exclude_types and td.tool_type in exclude_types:
                continue
            schema: dict = {"type": "object", "properties": td.input_schema}
            if td.required:
                schema["required"] = td.required
            result.append({
                "type": "function",
                "function": {
                    "name": td.name,
                    "description": td.description,
                    "parameters": schema,
                },
            })
        return result

    def get_names_by_type(self, tool_type: str) -> set[str]:
        return {name for name, td in self._tools.items() if td.tool_type == tool_type}

    def _derive_mcp_pack(self, td: ToolDef) -> str:
        """Derive the MCP permission pack for a tool."""
        if td.mcp_permission_pack:
            return td.mcp_permission_pack

        # Derive from tool_type and risk_level
        if td.tool_type in ("read", "analysis", "web"):
            return "readonly_collaboration"
        if td.tool_type == "memory":
            return "readonly_collaboration" if not td.writes_project_data else "project_writing"
        if td.tool_type == "generator":
            return "internal_llm"
        if td.tool_type == "scheduler":
            return "project_management"
        if td.tool_type == "write":
            if td.risk_level in ("high", "destructive"):
                return "trusted_local_maintenance"
            if td.writes_project_data:
                return "project_writing"
            return "project_management"
        return "readonly_collaboration"

    def list_for_internal_agent(
        self,
        *,
        tool_types: set[str] | None = None,
        exclude_types: set[str] | None = None,
    ) -> list[ToolDef]:
        """Return tools available to the internal project assistant."""
        result = []
        for td in self._tools.values():
            if not td.expose_to_internal_agent:
                continue
            if tool_types and td.tool_type not in tool_types:
                continue
            if exclude_types and td.tool_type in exclude_types:
                continue
            result.append(td)
        return result

    def list_for_scheduler(self) -> list[ToolDef]:
        """Return tools available to scheduled tasks."""
        return [td for td in self._tools.values() if td.expose_to_scheduler]

    def list_for_mcp(
        self,
        *,
        permission_pack: str = "readonly_collaboration",
    ) -> list[ToolDef]:
        """Return tools available to MCP clients for a given permission pack."""
        if permission_pack == "cataloging_worker":
            # Managed single-chapter cataloging CLIs read project files
            # directly. Keep their MCP surface deliberately small so every
            # fresh chapter turn does not pay for the full system tool schema.
            allowed_names = {
                "report_agent_plan",
                "report_agent_progress",
                "report_context_selected",
                "get_next_external_cataloging_chapter",
                "save_external_cataloging_facts",
                "save_external_cataloging_candidates",
                "verify_external_cataloging_progress",
                "get_cataloging_control_state",
                "list_cataloging_facts",
                "apply_pending_cataloging",
            }
            return [
                td
                for name, td in self._tools.items()
                if name in allowed_names and td.expose_to_mcp
            ]

        # Non-linear pack inclusion.
        #
        # Internal LLM tools intentionally do not sit below project_management:
        # external agents should be able to manage/import/write API-free data
        # without also receiving tools that spend the user's configured model
        # credits. Expose those only through the explicit internal_llm pack.
        pack_includes = {
            "readonly_collaboration": {"readonly_collaboration"},
            "draft_generation": {"readonly_collaboration", "draft_generation"},
            "project_writing": {"readonly_collaboration", "project_writing"},
            "project_management": {
                "readonly_collaboration",
                "project_writing",
                "project_management",
            },
            "trusted_local_maintenance": {
                "readonly_collaboration",
                "project_writing",
                "project_management",
                "trusted_local_maintenance",
            },
            "internal_llm": {
                "readonly_collaboration",
                "project_writing",
                "project_management",
                "internal_llm",
            },
        }
        allowed_packs = pack_includes.get(permission_pack, {"readonly_collaboration"})

        result = []
        for td in self._tools.values():
            if not td.expose_to_mcp:
                continue
            pack = self._derive_mcp_pack(td)
            if pack in allowed_packs:
                result.append(td)
        return result

    def list_for_frontend(self) -> list[dict]:
        """Return tool metadata dicts for frontend display."""
        result = []
        for td in self._tools.values():
            result.append({
                "name": td.name,
                "description": td.description,
                "tool_type": td.tool_type,
                "permission_tags": list(td.permission_tags),
                "risk_level": td.risk_level,
                "writes_project_data": td.writes_project_data,
                "expose_to_internal_agent": td.expose_to_internal_agent,
                "expose_to_scheduler": td.expose_to_scheduler,
                "expose_to_mcp": td.expose_to_mcp,
                "mcp_permission_pack": self._derive_mcp_pack(td),
                "requires_confirmation": td.requires_confirmation,
            })
        return result


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
registry = ToolRegistry()


# ---------------------------------------------------------------------------
# Register all tools
# ---------------------------------------------------------------------------

def _register_all() -> None:
    from .tools import (
        chapter_writer,
        apply_pending_cataloging,
        cancel_cataloging_job,
        character_writer,
        continue_text,
        prepare_task_context,
        search_task_context,
        submit_context_evidence,
        create_chapter,
        create_character,
        create_outline_node,
        create_outline_nodes,
        create_project,
        create_relationship,
        create_scheduled_task,
        create_skill,
        create_worldbuilding_entry,
        delete_chapter,
        delete_character,
        delete_outline_node,
        delete_project,
        delete_relationship,
        delete_scheduled_task,
        delete_skill,
        delete_worldbuilding_entry,
        design_plot,
        detect_character_changes,
        detect_forbidden_patterns,
        detect_new_worldbuilding,
        detect_worldbuilding_conflicts,
        diff_chapter_versions,
        dialogue_battle,
        evaluate_chapter,
        expand_text,
        export_project,
        forget,
        get_cataloging_control_state,
        get_cataloging_job,
        get_deconstruct_report,
        get_export_word_count,
        get_project_files_info,
        get_today_writing_stats,
        get_writing_stats_history,
        get_project_info,
        get_narrative_ledger,
        update_narrative_ledger_entry,
        get_narrative_governance,
        apply_narrative_governance_candidates,
        list_narrative_checkpoints,
        diff_narrative_checkpoint,
        restore_narrative_governance_checkpoint,
        ensure_builtin_skills_tool,
        draft_skill,
        import_deconstruct_report_tool,
        import_file_as_chapters,
        import_file_as_project,
        import_text_as_chapters,
        list_cataloging_candidates,
        list_cataloging_facts,
        list_cataloging_jobs,
        list_characters,
        list_chapter_versions,
        list_deconstruct_reports,
        list_duplicate_characters,
        list_memories,
        list_chapters,
        list_project_files,
        list_projects,
        list_scheduled_tasks,
        list_skill_templates_tool,
        list_skill_tools_tool,
        list_skill_versions_tool,
        list_skills,
        list_worldbuilding,
        outline_writer,
        preview_skill_match_tool,
        preview_character_merge,
        preview_deconstruct_source,
        preview_import_splits,
        recall,
        remember,
        rerun_cataloging_resolution_current,
        rerun_failed_deconstruct_chunks,
        rewrite_text,
        roleplay_character,
        run_scheduled_task_now,
        restore_chapter_version,
        search_project_files,
        merge_duplicate_characters,
        pause_cataloging_job,
        resume_cataloging_job,
        retry_current_cataloging_chapter,
        search_characters,
        search_chapters,
        search_outline,
        search_outline_tree,
        search_relationships,
        search_worldbuilding,
        suggest_conflicts,
        update_chapter,
        update_character,
        update_cataloging_candidate,
        update_outline_node,
        update_project_info,
        update_relationship,
        update_scheduled_task,
        update_skill,
        update_worldbuilding_entry,
        read_project_file,
        set_cataloging_mode,
        set_daily_word_goal,
        start_cataloging_job,
        start_deconstruct_job,
        web_search,
        worldbuilding_writer,
        preview_writing_context,
        sync_project_files,
        write_project_file,
        reset_skill,
    )
    from .tools.rag_tools import search_context, preview_rag_context, explain_context_selection

    _r = registry.register

    # ── System / Project Management ─────────────────────────────────────

    _r(ToolDef(
        name="list_projects",
        description="列出系统中的作品。可按标题或简介搜索。用于项目助手帮助用户管理多个作品；不会读取或修改API Key。",
        input_schema={
            "query": {"type": "string", "description": "可选，按作品标题或简介搜索"},
            "limit": {"type": "integer", "description": "返回数量上限，默认50"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=list_projects,
    ))

    _r(ToolDef(
        name="get_project_info",
        description="读取作品设置与基础信息。默认读取当前作品，也可传入作品ID。不会读取API Key。",
        input_schema={
            "id": {"type": "string", "description": "可选，作品ID。不传则读取当前作品"},
            "project_id": {"type": "string", "description": "兼容字段，同id"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=get_project_info,
    ))

    _r(ToolDef(
        name="get_project_files_info",
        description="读取作品的文件源目录信息。用于 Claude/Codex 了解章节、角色、大纲、世界观在本机项目目录中的位置。",
        input_schema={
            "project_id": {"type": "string", "description": "可选，作品ID。不传则使用当前作品"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=get_project_files_info,
    ))

    _r(ToolDef(
        name="list_project_files",
        description="列出作品目录内的文件或子目录。只能访问该作品目录，不能读取 API Key 或系统设置。",
        input_schema={
            "project_id": {"type": "string", "description": "可选，作品ID。不传则使用当前作品"},
            "path": {"type": "string", "description": "相对作品目录的子目录，如 chapters、characters、outline"},
            "limit": {"type": "integer", "description": "返回数量上限，默认200"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=list_project_files,
    ))

    _r(ToolDef(
        name="read_project_file",
        description="读取作品目录内的文本文件，如章节 Markdown、角色 JSON、世界观 JSON。大文件会按 max_chars 截断。",
        input_schema={
            "project_id": {"type": "string", "description": "可选，作品ID。不传则使用当前作品"},
            "path": {"type": "string", "description": "作品目录内的相对文件路径"},
            "max_chars": {"type": "integer", "description": "最多读取字符数，默认200000"},
        },
        required=["path"],
        tool_type="read",
        estimated_cost="free",
        handler=read_project_file,
    ))

    _r(ToolDef(
        name="search_project_files",
        description="在作品目录内搜索文本。适合外部 Agent 快速定位章节正文、角色卡、大纲或世界观中的线索。",
        input_schema={
            "project_id": {"type": "string", "description": "可选，作品ID。不传则使用当前作品"},
            "query": {"type": "string", "description": "要搜索的文本"},
            "path": {"type": "string", "description": "可选，限定搜索子目录或文件"},
            "limit": {"type": "integer", "description": "匹配数量上限，默认50"},
            "context_chars": {"type": "integer", "description": "每个命中前后保留的字符数，默认120"},
        },
        required=["query"],
        tool_type="read",
        estimated_cost="free",
        handler=search_project_files,
    ))

    _r(ToolDef(
        name="write_project_file",
        description=(
            "写入作品目录内的非规范文本文件。Siming 2.1 起数据库是唯一写入源，"
            "chapters/characters/worldbuilding/outline/relationships 目录只是只读镜像；"
            "如需创建或修改章节、角色、大纲、世界观、关系，必须使用对应 create/update/delete 工具。"
        ),
        input_schema={
            "project_id": {"type": "string", "description": "可选，作品ID。不传则使用当前作品"},
            "path": {"type": "string", "description": "作品目录内的相对文件路径；不能位于规范镜像目录"},
            "content": {"type": "string", "description": "要写入的完整文本内容"},
            "overwrite": {"type": "boolean", "description": "是否覆盖已有文件，默认true"},
        },
        required=["path", "content"],
        tool_type="write",
        writes_project_data=True,
        estimated_cost="free",
        handler=write_project_file,
    ))

    _r(ToolDef(
        name="sync_project_files",
        description=(
            "手动同步作品文件目录。默认 db_to_files，将数据库权威数据刷新为文件镜像。"
            "files_to_db/import/both 是危险的修复导入路径，必须传 confirm_import_from_files=true 才会执行。"
        ),
        input_schema={
            "project_id": {"type": "string", "description": "可选，作品ID。不传则使用当前作品"},
            "direction": {"type": "string", "description": "db_to_files|files_to_db|import|export|both，默认 db_to_files"},
            "confirm_import_from_files": {"type": "boolean", "description": "仅在 files_to_db/import/both 时需要；确认允许文件镜像反向覆盖数据库"},
        },
        tool_type="write",
        writes_project_data=True,
        estimated_cost="free",
        handler=sync_project_files,
    ))

    _r(ToolDef(
        name="create_project",
        description="创建新作品。可设置简介、标签、文风、禁用句式、修辞限制、每日字数目标等基础信息。不会创建或修改API Key。",
        input_schema={
            "title": {"type": "string", "description": "作品标题"},
            "description": {"type": "string", "description": "作品简介"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "作品标签"},
            "narrative_perspective": {"type": "string", "description": "叙事视角，如 third_person/first_person"},
            "writing_style": {"type": "string", "description": "文风偏好"},
            "forbidden_sentence_patterns": {"type": "string", "description": "禁用句式，每行一条"},
            "rhetoric_guidelines": {"type": "string", "description": "修辞和比喻使用限制"},
            "short_sentences": {"type": "boolean", "description": "是否启用短句模式"},
            "custom_style_prompt": {"type": "string", "description": "自定义风格提示词"},
            "daily_word_goal": {"type": "integer", "description": "每日字数目标"},
            "context_manifest_id": {"type": "string", "description": "Governed task manifest required for MCP formal writes"},
        },
        required=["title"],
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler=create_project,
    ))

    _r(ToolDef(
        name="update_project_info",
        description="更新当前或指定作品的基础设置。可修改标题、简介、文风、禁用句式等；不能修改API Key或模型密钥。",
        input_schema={
            "id": {"type": "string", "description": "可选，作品ID。不传则更新当前作品"},
            "project_id": {"type": "string", "description": "兼容字段，同id"},
            "title": {"type": "string", "description": "作品标题"},
            "description": {"type": "string", "description": "作品简介"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "作品标签"},
            "narrative_perspective": {"type": "string", "description": "叙事视角"},
            "writing_style": {"type": "string", "description": "文风偏好"},
            "forbidden_sentence_patterns": {"type": "string", "description": "禁用句式，每行一条"},
            "rhetoric_guidelines": {"type": "string", "description": "修辞和比喻限制"},
            "short_sentences": {"type": "boolean", "description": "是否启用短句模式"},
            "custom_style_prompt": {"type": "string", "description": "自定义风格提示词"},
            "daily_word_goal": {"type": "integer", "description": "每日字数目标"},
        },
        tool_type="write",
        estimated_cost="free",
        handler=update_project_info,
    ))

    _r(ToolDef(
        name="delete_project",
        description="删除作品及其全部关联数据。危险操作，必须在用户明确确认删除指定作品后才能调用。",
        input_schema={
            "id": {"type": "string", "description": "要删除的作品ID"},
            "project_id": {"type": "string", "description": "兼容字段，同id"},
        },
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler=delete_project,
    ))

    _r(ToolDef(
        name="export_project",
        description="导出当前作品内容，生成 txt/docx/pdf 文件。可导出章节、大纲、角色、世界观或全部内容。",
        input_schema={
            "scope": {"type": "string", "description": "导出范围：chapters|outline|characters|worldbuilding|all|single|selected，默认all"},
            "format": {"type": "string", "description": "导出格式：txt|docx|pdf，默认txt"},
            "chapter_ids": {"type": "array", "items": {"type": "string"}, "description": "scope=selected/single时要导出的章节ID列表"},
            "include_outline": {"type": "boolean", "description": "导出章节时是否附带大纲"},
            "include_characters": {"type": "boolean", "description": "导出章节时是否附带角色档案"},
            "include_worldbuilding": {"type": "boolean", "description": "导出章节时是否附带世界观"},
        },
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler=export_project,
    ))

    _r(ToolDef(
        name="get_export_word_count",
        description="统计当前作品章节总字数和每章字数，用于导出前检查或写作进度统计。",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=get_export_word_count,
    ))

    # -- System / Import, Cataloging, Deconstruct, Stats --

    _r(ToolDef(
        name="preview_import_splits",
        description="Preview chapter split suggestions for pasted novel text before importing it as chapters.",
        input_schema={
            "text": {"type": "string", "description": "Full text to split into chapters"},
            "model": {"type": "string", "description": "Optional model override for LLM split correction"},
        },
        required=["text"],
        tool_type="analysis",
        estimated_cost="low",
        handler=preview_import_splits,
    ))

    _r(ToolDef(
        name="import_text_as_chapters",
        description="Import pasted text as project chapters. Can use provided split suggestions or auto-split first.",
        input_schema={
            "text": {"type": "string", "description": "Full text to import"},
            "splits": {"type": "array", "items": {"type": "object"}, "description": "Optional split suggestions from preview_import_splits"},
            "outline_node_id": {"type": "string", "description": "Optional outline node ID to link imported chapters"},
            "auto_split": {"type": "boolean", "description": "Auto-detect chapter boundaries when splits are omitted; default true"},
            "model": {"type": "string", "description": "Optional model override for split correction"},
        },
        required=["text"],
        tool_type="write",
        idempotent=True,
        estimated_cost="low",
        handler=import_text_as_chapters,
    ))

    _r(ToolDef(
        name="import_file_as_chapters",
        description="Import a local TXT/DOCX file from file_path into an existing project as chapters. Prefer this over passing a whole novel as text through MCP.",
        input_schema={
            "file_path": {"type": "string", "description": "Local .txt or .docx path on the same machine as Siming"},
            "splits": {"type": "array", "items": {"type": "object"}, "description": "Optional split suggestions from preview_import_splits"},
            "outline_node_id": {"type": "string", "description": "Optional outline node ID to link imported chapters"},
            "auto_split": {"type": "boolean", "description": "Auto-detect chapter boundaries when splits are omitted; default true"},
            "model": {"type": "string", "description": "Optional model override for split correction"},
        },
        required=["file_path"],
        tool_type="write",
        idempotent=True,
        estimated_cost="low",
        handler=import_file_as_chapters,
    ))

    _r(ToolDef(
        name="import_file_as_project",
        description="Create a new project from a local TXT/DOCX file and import the file as chapters in one step. Best tool for requests like '导入这本小说为新作品'.",
        input_schema={
            "file_path": {"type": "string", "description": "Local .txt or .docx path on the same machine as Siming"},
            "title": {"type": "string", "description": "Project title; defaults to the file name without extension"},
            "description": {"type": "string", "description": "Project description"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Project tags"},
            "narrative_perspective": {"type": "string", "description": "Narrative perspective, e.g. third_person/first_person"},
            "writing_style": {"type": "string", "description": "Writing style preference"},
            "forbidden_sentence_patterns": {"type": "string", "description": "Forbidden sentence patterns, one per line"},
            "rhetoric_guidelines": {"type": "string", "description": "Rhetoric and metaphor guidelines"},
            "short_sentences": {"type": "boolean", "description": "Enable short sentence mode"},
            "custom_style_prompt": {"type": "string", "description": "Custom style prompt"},
            "daily_word_goal": {"type": "integer", "description": "Daily word goal"},
            "auto_split": {"type": "boolean", "description": "Auto-detect chapter boundaries; default true"},
            "model": {"type": "string", "description": "Optional model override for split correction"},
        },
        required=["file_path"],
        tool_type="write",
        idempotent=True,
        estimated_cost="low",
        handler=import_file_as_project,
    ))

    _r(ToolDef(
        name="start_cataloging_job",
        description="Start a project cataloging job that initializes or updates chapter summaries, characters, outline, worldbuilding, and links from existing chapters.",
        input_schema={
            "execution_mode": {"type": "string", "description": "auto or manual; manual waits for user confirmation after each chapter"},
            "model": {"type": "string", "description": "Optional model override"},
            "chapter_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional ordered chapter IDs; omit for all chapters"},
            "run_now": {"type": "boolean", "description": "Start processing immediately; default true"},
        },
        tool_type="write",
        idempotent=True,
        estimated_cost="high",
        handler=start_cataloging_job,
    ))

    _r(ToolDef(
        name="list_cataloging_jobs",
        description="List recent project cataloging jobs and their progress.",
        input_schema={"limit": {"type": "integer", "description": "Maximum jobs to return; default 20"}},
        tool_type="read",
        estimated_cost="free",
        handler=list_cataloging_jobs,
    ))

    _r(ToolDef(
        name="get_cataloging_job",
        description="Read a cataloging job with its chapter runs.",
        input_schema={"job_id": {"type": "string", "description": "Cataloging job ID"}},
        required=["job_id"],
        tool_type="read",
        estimated_cost="free",
        handler=get_cataloging_job,
    ))

    _r(ToolDef(
        name="get_cataloging_control_state",
        description="Read the compact live control state for a cataloging job, including auto/manual mode.",
        input_schema={"job_id": {"type": "string", "description": "Cataloging job ID"}},
        required=["job_id"],
        tool_type="read",
        estimated_cost="free",
        handler=get_cataloging_control_state,
    ))

    _r(ToolDef(
        name="set_cataloging_mode",
        description="Switch a cataloging job between auto and manual confirmation mode.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "execution_mode": {"type": "string", "description": "auto or manual"},
        },
        required=["job_id", "execution_mode"],
        tool_type="write",
        estimated_cost="free",
        handler=set_cataloging_mode,
    ))

    _r(ToolDef(
        name="list_cataloging_candidates",
        description="List cataloging candidates for review.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "chapter_run_id": {"type": "string", "description": "Optional chapter run ID"},
            "status": {"type": "string", "description": "Optional candidate status filter"},
            "item_type": {"type": "string", "description": "Optional candidate type filter"},
        },
        required=["job_id"],
        tool_type="read",
        estimated_cost="free",
        handler=list_cataloging_candidates,
    ))

    _r(ToolDef(
        name="list_cataloging_facts",
        description="List saved first-stage cataloging facts.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "chapter_run_id": {"type": "string", "description": "Optional chapter run ID"},
            "fact_type": {"type": "string", "description": "Optional fact type filter"},
        },
        required=["job_id"],
        tool_type="read",
        estimated_cost="free",
        handler=list_cataloging_facts,
    ))

    _r(ToolDef(
        name="update_cataloging_candidate",
        description="Edit or approve/reject a cataloging candidate before it is applied.",
        input_schema={
            "candidate_id": {"type": "string", "description": "Candidate ID"},
            "payload": {"type": "object", "description": "Edited candidate payload"},
            "status": {"type": "string", "description": "pending|edited|approved|rejected|applying|applied|apply_failed"},
        },
        required=["candidate_id"],
        tool_type="write",
        estimated_cost="free",
        handler=update_cataloging_candidate,
    ))

    _r(ToolDef(
        name="apply_pending_cataloging",
        description="Apply the current waiting-confirmation cataloging chapter candidates and continue the job when possible.",
        input_schema={"job_id": {"type": "string", "description": "Cataloging job ID"}},
        required=["job_id"],
        tool_type="write",
        estimated_cost="free",
        handler=apply_pending_cataloging,
    ))

    _r(ToolDef(
        name="retry_current_cataloging_chapter",
        description="Retry the failed or waiting current cataloging chapter from stage one.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "run_now": {"type": "boolean", "description": "Resume processing immediately; default true"},
        },
        required=["job_id"],
        tool_type="write",
        estimated_cost="high",
        handler=retry_current_cataloging_chapter,
    ))

    _r(ToolDef(
        name="rerun_cataloging_resolution_current",
        description="Retry only the second cataloging stage for the current chapter, reusing saved facts.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "run_now": {"type": "boolean", "description": "Resume processing immediately; default true"},
        },
        required=["job_id"],
        tool_type="write",
        estimated_cost="medium",
        handler=rerun_cataloging_resolution_current,
    ))

    _r(ToolDef(
        name="pause_cataloging_job",
        description="Pause a running cataloging job.",
        input_schema={"job_id": {"type": "string", "description": "Cataloging job ID"}},
        required=["job_id"],
        tool_type="write",
        estimated_cost="free",
        handler=pause_cataloging_job,
    ))

    _r(ToolDef(
        name="resume_cataloging_job",
        description="Resume a paused cataloging job and continue processing.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "run_now": {"type": "boolean", "description": "Resume processing immediately; default true"},
        },
        required=["job_id"],
        tool_type="write",
        estimated_cost="high",
        handler=resume_cataloging_job,
    ))

    _r(ToolDef(
        name="cancel_cataloging_job",
        description="Cancel a cataloging job. Requires clear user confirmation.",
        input_schema={"job_id": {"type": "string", "description": "Cataloging job ID"}},
        required=["job_id"],
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler=cancel_cataloging_job,
    ))

    _r(ToolDef(
        name="preview_deconstruct_source",
        description="Preview available chapters and word counts before starting legacy deconstruct analysis.",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=preview_deconstruct_source,
    ))

    _r(ToolDef(
        name="list_deconstruct_reports",
        description="List persisted legacy deconstruct reports.",
        input_schema={"limit": {"type": "integer", "description": "Maximum reports to return; default 20"}},
        tool_type="read",
        estimated_cost="free",
        handler=list_deconstruct_reports,
    ))

    _r(ToolDef(
        name="get_deconstruct_report",
        description="Read a persisted legacy deconstruct report.",
        input_schema={"report_id": {"type": "string", "description": "Deconstruct report ID"}},
        required=["report_id"],
        tool_type="read",
        estimated_cost="free",
        handler=get_deconstruct_report,
    ))

    _r(ToolDef(
        name="start_deconstruct_job",
        description="Start legacy deconstruct analysis for selected chapters or pasted text. Prefer cataloging for project initialization.",
        input_schema={
            "text": {"type": "string", "description": "Optional text to analyze"},
            "chapter_ids": {"type": "array", "items": {"type": "string"}, "description": "Existing chapters to analyze"},
            "title": {"type": "string", "description": "Report title"},
            "model": {"type": "string", "description": "Optional model override"},
            "map_model": {"type": "string", "description": "Optional map model"},
            "reduce_model": {"type": "string", "description": "Optional reduce model"},
            "analysis_mode": {"type": "string", "description": "fast or detailed; default fast"},
            "include_golden_three": {"type": "boolean", "description": "Whether to analyze first three chapters"},
            "include_rhythm": {"type": "boolean", "description": "Whether to include rhythm analysis"},
            "include_patterns": {"type": "boolean", "description": "Whether to include writing-pattern analysis"},
            "map_concurrency": {"type": "integer", "description": "Map concurrency 1-12"},
            "run_now": {"type": "boolean", "description": "Start processing immediately; default true"},
        },
        tool_type="write",
        idempotent=True,
        estimated_cost="high",
        handler=start_deconstruct_job,
    ))

    _r(ToolDef(
        name="rerun_failed_deconstruct_chunks",
        description="Rerun only failed chunks for an existing legacy deconstruct report.",
        input_schema={
            "report_id": {"type": "string", "description": "Deconstruct report ID"},
            "model": {"type": "string", "description": "Optional model override"},
            "map_model": {"type": "string", "description": "Optional map model"},
            "reduce_model": {"type": "string", "description": "Optional reduce model"},
        },
        required=["report_id"],
        tool_type="write",
        estimated_cost="high",
        handler=rerun_failed_deconstruct_chunks,
    ))

    _r(ToolDef(
        name="import_deconstruct_report",
        description="Import selected sections from a deconstruct report into outline, characters, and/or worldbuilding.",
        input_schema={
            "report_id": {"type": "string", "description": "Deconstruct report ID"},
            "import_outline": {"type": "boolean", "description": "Import outline nodes"},
            "import_characters": {"type": "boolean", "description": "Import characters"},
            "import_worldbuilding": {"type": "boolean", "description": "Import worldbuilding entries"},
        },
        required=["report_id"],
        tool_type="write",
        estimated_cost="low",
        handler=import_deconstruct_report_tool,
    ))

    _r(ToolDef(
        name="get_today_writing_stats",
        description="Read today's writing statistics for the current project.",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=get_today_writing_stats,
    ))

    _r(ToolDef(
        name="get_writing_stats_history",
        description="Read daily writing statistics history for the current project.",
        input_schema={"days": {"type": "integer", "description": "Number of days, 1-365; default 7"}},
        tool_type="read",
        estimated_cost="free",
        handler=get_writing_stats_history,
    ))

    _r(ToolDef(
        name="set_daily_word_goal",
        description="Set the current project's daily word-count goal.",
        input_schema={"daily_word_goal": {"type": "integer", "description": "Daily target word count"}},
        required=["daily_word_goal"],
        tool_type="write",
        estimated_cost="free",
        handler=set_daily_word_goal,
    ))

    _r(ToolDef(
        name="list_duplicate_characters",
        description="Find likely duplicate character cards for manual review or merge.",
        input_schema={"limit": {"type": "integer", "description": "Maximum duplicate pairs to return; default 80"}},
        tool_type="analysis",
        estimated_cost="free",
        handler=list_duplicate_characters,
    ))

    _r(ToolDef(
        name="preview_character_merge",
        description="Preview how two duplicate character cards would be merged.",
        input_schema={
            "primary_id": {"type": "string", "description": "Character ID to keep"},
            "secondary_id": {"type": "string", "description": "Character ID to merge into primary"},
            "canonical_name": {"type": "string", "description": "Optional final canonical name"},
        },
        required=["primary_id", "secondary_id"],
        tool_type="analysis",
        estimated_cost="free",
        handler=preview_character_merge,
    ))

    _r(ToolDef(
        name="merge_duplicate_characters",
        description="Merge a duplicate character into the primary character card. Requires clear user confirmation.",
        input_schema={
            "primary_id": {"type": "string", "description": "Character ID to keep"},
            "secondary_id": {"type": "string", "description": "Character ID to merge into primary"},
            "canonical_name": {"type": "string", "description": "Optional final canonical name"},
            "reason": {"type": "string", "description": "Why these two cards are the same character"},
        },
        required=["primary_id", "secondary_id"],
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler=merge_duplicate_characters,
    ))

    # ── System / Scheduled Tasks ────────────────────────────────────────

    _r(ToolDef(
        name="list_scheduled_tasks",
        description="列出当前作品的自动任务/定时任务。",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=list_scheduled_tasks,
    ))

    _r(ToolDef(
        name="create_scheduled_task",
        description="创建自动任务/定时任务。适用于用户要求定时搜索、定时整理资料、周期性提醒或定期执行项目助手任务。",
        input_schema={
            "name": {"type": "string", "description": "任务名称"},
            "prompt": {"type": "string", "description": "任务执行提示词，描述AI到点后要做什么"},
            "cron_expr": {"type": "string", "description": "Cron表达式，如 0 22 * * * 表示每天22点"},
            "interval_minutes": {"type": "integer", "description": "间隔分钟。若cron_expr存在则优先使用cron_expr"},
            "tool_policy": {"type": "array", "items": {"type": "string"}, "description": "可选，任务可使用的工具名"},
            "status": {"type": "string", "description": "active|paused，默认active"},
        },
        required=["name", "prompt"],
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler=create_scheduled_task,
    ))

    _r(ToolDef(
        name="update_scheduled_task",
        description="更新自动任务/定时任务。可按ID或名称定位，修改提示词、周期、状态等。",
        input_schema={
            "id": {"type": "string", "description": "任务ID"},
            "task_id": {"type": "string", "description": "兼容字段，同id"},
            "name": {"type": "string", "description": "任务名称，可用于定位或重命名"},
            "prompt": {"type": "string", "description": "新的执行提示词"},
            "cron_expr": {"type": "string", "description": "新的Cron表达式，空值表示清除"},
            "interval_minutes": {"type": "integer", "description": "新的间隔分钟"},
            "tool_policy": {"type": "array", "items": {"type": "string"}, "description": "任务可使用工具名"},
            "status": {"type": "string", "description": "active|paused"},
        },
        tool_type="write",
        estimated_cost="free",
        handler=update_scheduled_task,
    ))

    _r(ToolDef(
        name="delete_scheduled_task",
        description="删除自动任务/定时任务。必须在用户明确确认删除后调用。",
        input_schema={
            "id": {"type": "string", "description": "任务ID"},
            "task_id": {"type": "string", "description": "兼容字段，同id"},
            "name": {"type": "string", "description": "任务名称，id为空时用于定位"},
        },
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler=delete_scheduled_task,
    ))

    _r(ToolDef(
        name="run_scheduled_task_now",
        description="立即执行一个自动任务。可能触发LLM、联网搜索或写入项目资料；用户明确要求立即运行时使用。",
        input_schema={
            "id": {"type": "string", "description": "任务ID"},
            "task_id": {"type": "string", "description": "兼容字段，同id"},
            "name": {"type": "string", "description": "任务名称，id为空时用于定位"},
        },
        tool_type="write",
        estimated_cost="medium",
        handler=run_scheduled_task_now,
    ))

    # ── System / Skills ─────────────────────────────────────────────────

    _r(ToolDef(
        name="list_skills",
        description="列出当前作品的AI技能，包括内置技能和自定义技能。",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=list_skills,
    ))

    _r(ToolDef(
        name="list_skill_templates",
        description="列出可用于创建技能的模板。",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=list_skill_templates_tool,
    ))

    _r(ToolDef(
        name="list_skill_tools",
        description="列出技能中可推荐或禁用的工具名及元数据。",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=list_skill_tools_tool,
    ))

    _r(ToolDef(
        name="draft_skill",
        description="根据用户需求生成一个可编辑的技能草案，不会保存。用户想先看看技能怎么写时使用。",
        input_schema={
            "requirements": {"type": "string", "description": "用户想创建的技能需求"},
            "template_key": {"type": "string", "description": "可选模板key"},
            "scope": {"type": "string", "description": "global|project|writing|outline|characters|worldbuilding|cataloging|research"},
        },
        required=["requirements"],
        tool_type="generator",
        estimated_cost="free",
        handler=draft_skill,
    ))

    _r(ToolDef(
        name="create_skill",
        description="创建AI技能。可直接提供完整字段；也可只提供requirements，系统会用模板生成技能并保存。",
        input_schema={
            "requirements": {"type": "string", "description": "用户想创建的技能需求，可用于自动生成技能草案"},
            "template_key": {"type": "string", "description": "可选模板key"},
            "name": {"type": "string", "description": "技能名称"},
            "description": {"type": "string", "description": "技能描述"},
            "trigger_examples": {"type": "array", "items": {"type": "string"}, "description": "触发关键词/示例"},
            "system_prompt": {"type": "string", "description": "技能系统提示词"},
            "recommended_tools": {"type": "array", "items": {"type": "string"}, "description": "推荐工具"},
            "forbidden_tools": {"type": "array", "items": {"type": "string"}, "description": "禁用工具"},
            "scope": {"type": "string", "description": "global|project|writing|outline|characters|worldbuilding|cataloging|research"},
            "priority": {"type": "integer", "description": "优先级0-100"},
            "enabled": {"type": "boolean", "description": "是否启用"},
        },
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler=create_skill,
    ))

    _r(ToolDef(
        name="update_skill",
        description="更新AI技能。可按ID或名称定位，修改触发词、提示词、范围、优先级、启用状态等。",
        input_schema={
            "id": {"type": "string", "description": "技能ID"},
            "skill_id": {"type": "string", "description": "兼容字段，同id"},
            "name": {"type": "string", "description": "技能名称，可用于定位或重命名"},
            "description": {"type": "string", "description": "技能描述"},
            "trigger_examples": {"type": "array", "items": {"type": "string"}, "description": "触发关键词/示例"},
            "system_prompt": {"type": "string", "description": "技能系统提示词"},
            "recommended_tools": {"type": "array", "items": {"type": "string"}, "description": "推荐工具"},
            "forbidden_tools": {"type": "array", "items": {"type": "string"}, "description": "禁用工具"},
            "scope": {"type": "string", "description": "技能适用范围"},
            "priority": {"type": "integer", "description": "优先级0-100"},
            "enabled": {"type": "boolean", "description": "是否启用"},
        },
        tool_type="write",
        estimated_cost="free",
        handler=update_skill,
    ))

    _r(ToolDef(
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
        handler=delete_skill,
    ))

    _r(ToolDef(
        name="reset_skill",
        description="将内置技能恢复默认值。仅适用于内置技能。",
        input_schema={
            "id": {"type": "string", "description": "技能ID"},
            "skill_id": {"type": "string", "description": "兼容字段，同id"},
            "name": {"type": "string", "description": "技能名称，id为空时用于定位"},
        },
        tool_type="write",
        estimated_cost="free",
        handler=reset_skill,
    ))

    _r(ToolDef(
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
        handler=preview_skill_match_tool,
    ))

    _r(ToolDef(
        name="list_skill_versions",
        description="列出技能版本历史。可按ID或名称定位。",
        input_schema={
            "id": {"type": "string", "description": "技能ID"},
            "skill_id": {"type": "string", "description": "兼容字段，同id"},
            "name": {"type": "string", "description": "技能名称，id为空时用于定位"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=list_skill_versions_tool,
    ))

    _r(ToolDef(
        name="ensure_builtin_skills",
        description="确保当前作品已初始化全部内置技能。通常系统会自动处理，用户要求恢复内置技能入口时使用。",
        input_schema={},
        tool_type="write",
        estimated_cost="free",
        handler=ensure_builtin_skills_tool,
    ))

    # ── Read: Search & Catalog ───────────────────────────────────────────

    _r(ToolDef(
        name="search_characters",
        description="按角色名片段搜索角色完整档案。返回角色姓名、外貌、性格、背景、能力列表、角色类型。内容截断至8000字。",
        input_schema={
            "query": {"type": "string", "description": "角色名片段，支持模糊匹配"},
            "limit": {"type": "integer", "description": "返回条数上限，默认10，最大30"},
        },
        required=["query"],
        tool_type="read",
        estimated_cost="free",
        handler=search_characters,
    ))

    _r(ToolDef(
        name="search_chapters",
        description="搜索章节全文。按标题搜索，可选限定大纲节点。正文截断至8000字。",
        input_schema={
            "query": {"type": "string", "description": "章节标题片段，支持模糊匹配"},
            "outline_node_id": {"type": "string", "description": "限定大纲节点ID，传入后忽略query直接返回该节点下所有章节"},
            "limit": {"type": "integer", "description": "返回条数上限，默认5，最大20"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=search_chapters,
    ))

    _r(ToolDef(
        name="search_outline",
        description="按标题搜索大纲节点，或查看指定节点的子树。",
        input_schema={
            "query": {"type": "string", "description": "大纲标题片段，支持模糊匹配"},
            "node_id": {"type": "string", "description": "指定节点ID，传入后返回该节点及所有子孙节点（忽略query）"},
            "limit": {"type": "integer", "description": "返回条数上限，默认10，最大60"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=search_outline,
    ))

    _r(ToolDef(
        name="search_outline_tree",
        description="获取完整大纲树结构（仅标题和层级），或指定子树。",
        input_schema={
            "root_id": {"type": "string", "description": "可选，子树根节点ID。不传则返回完整大纲树"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=search_outline_tree,
    ))

    _r(ToolDef(
        name="search_worldbuilding",
        description="按标题搜索世界观条目完整内容。可按维度过滤。",
        input_schema={
            "query": {"type": "string", "description": "设定标题片段，支持模糊匹配"},
            "dimension": {"type": "string", "description": "限定维度：geography|history|factions|power_system|races|culture"},
            "limit": {"type": "integer", "description": "返回条数上限，默认10，最大30"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=search_worldbuilding,
    ))

    _r(ToolDef(
        name="search_relationships",
        description="查询角色的所有关系（与谁有关系、方向、关系类型、描述）。",
        input_schema={
            "character_id": {"type": "string", "description": "角色ID，优先使用"},
            "character_name": {"type": "string", "description": "角色名，character_id为空时使用"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=search_relationships,
    ))

    _r(ToolDef(
        name="list_characters",
        description="快速概览全部角色（仅返回姓名、ID、角色类型）。轻量级，先调此工具确认角色是否存在，再决定是否需要 search_characters 查详情。",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=list_characters,
    ))

    _r(ToolDef(
        name="list_chapters",
        description="快速概览全部章节（仅返回标题、ID、对应大纲节点ID）。轻量级，不含正文。",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=list_chapters,
    ))

    _r(ToolDef(
        name="list_worldbuilding",
        description="快速概览全部世界观条目（仅返回标题、ID、维度）。轻量级，不含正文。",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=list_worldbuilding,
    ))

    # ── Write: Worldbuilding CRUD ────────────────────────────────────────

    _r(ToolDef(
        name="create_worldbuilding_entry",
        description="创建一条新的世界观设定条目。",
        input_schema={
            "title": {"type": "string", "description": "条目标题"},
            "content": {"type": "string", "description": "条目正文内容"},
            "dimension": {"type": "string", "description": "所属维度：geography|history|factions|power_system|races|culture，默认culture"},
            "sort_order": {"type": "integer", "description": "排序序号"},
            "related_characters": {"type": "array", "items": {"type": "string"}, "description": "关联角色名列表"},
            "plot_usage": {"type": "string", "description": "剧情用途说明"},
            "constraints": {"type": "array", "items": {"type": "string"}, "description": "设定约束列表"},
            "status": {"type": "string", "description": "条目状态：active|archived|draft，默认active"},
            "confidence": {"type": "number", "description": "置信度评分，0-1"},
            "first_seen_chapter_id": {"type": "string", "description": "首次出现的章节ID"},
            "last_updated_chapter_id": {"type": "string", "description": "最后更新的章节ID"},
        },
        required=["title", "content"],
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler=create_worldbuilding_entry,
    ))

    _r(ToolDef(
        name="update_worldbuilding_entry",
        description="更新一条世界观条目。用ID或标题定位。",
        input_schema={
            "id": {"type": "string", "description": "条目ID（优先使用）"},
            "title": {"type": "string", "description": "条目标题（id为空时用于定位；也可用于重命名）"},
            "dimension": {"type": "string", "description": "更新维度"},
            "content": {"type": "string", "description": "更新正文"},
            "sort_order": {"type": "integer", "description": "更新排序"},
            "status": {"type": "string", "description": "更新状态：active|archived|draft"},
            "confidence": {"type": "number", "description": "更新置信度"},
            "first_seen_chapter_id": {"type": "string", "description": "更新首次出现章节ID"},
            "last_updated_chapter_id": {"type": "string", "description": "更新最后更新章节ID"},
        },
        required=["id"],
        tool_type="write",
        estimated_cost="free",
        handler=update_worldbuilding_entry,
    ))

    _r(ToolDef(
        name="delete_worldbuilding_entry",
        description="删除一条世界观条目。用ID或标题定位。",
        input_schema={
            "id": {"type": "string", "description": "条目ID（优先使用）"},
            "title": {"type": "string", "description": "条目标题（id为空时使用）"},
        },
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler=delete_worldbuilding_entry,
    ))

    # ── Write: Outline CRUD ──────────────────────────────────────────────

    _r(ToolDef(
        name="create_outline_node",
        description="创建新的大纲节点。",
        input_schema={
            "title": {"type": "string", "description": "节点标题"},
            "parent_id": {"type": "string", "description": "父节点ID，可空（作为根节点）"},
            "node_type": {"type": "string", "description": "节点类型：volume|chapter|section，默认chapter"},
            "summary": {"type": "string", "description": "本节点剧情摘要"},
            "status": {"type": "string", "description": "状态：pending|in_progress|completed，默认pending"},
            "character_names": {"type": "array", "items": {"type": "string"}, "description": "本节点涉及的角色名列表"},
            "source_chapter_id": {"type": "string", "description": "关联的源章节ID"},
            "actual_summary": {"type": "string", "description": "实际完成后的摘要"},
            "planned_summary": {"type": "string", "description": "计划中的摘要"},
            "cataloging_status": {"type": "string", "description": "编目状态"},
            "metadata": {"type": "object", "description": "section 场景元数据：scene_number/purpose/location/timeline/pov_character/characters/entry_state/exit_state/emotional_residue/unresolved_actions"},
        },
        required=["title"],
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler=create_outline_node,
    ))

    _r(ToolDef(
        name="create_outline_nodes",
        description="批量创建新的大纲节点。通常用于保存 outline_writer 生成的一组节点。",
        input_schema={
            "nodes": {
                "type": "array",
                "items": {"type": "object"},
                "description": "大纲节点列表，每个节点可包含 title/node_type/summary/status/character_names/parent_id",
            },
            "parent_id": {"type": "string", "description": "可选，批量节点的默认父节点ID"},
        },
        required=["nodes"],
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler=create_outline_nodes,
    ))

    _r(ToolDef(
        name="update_outline_node",
        description="更新大纲节点。用ID或标题定位。",
        input_schema={
            "id": {"type": "string", "description": "节点ID（优先使用）。也可用 title/outline_node_id/node_id/outline_node_title/current_title/old_title 定位"},
            "title": {"type": "string", "description": "更新标题"},
            "summary": {"type": "string", "description": "更新摘要"},
            "status": {"type": "string", "description": "更新状态：pending|in_progress|completed"},
            "node_type": {"type": "string", "description": "更新节点类型：volume|chapter|section"},
            "character_names": {"type": "array", "items": {"type": "string"}, "description": "更新涉及的角色名列表（替换全部已有关联）"},
            "source_chapter_id": {"type": "string", "description": "更新关联的源章节ID"},
            "actual_summary": {"type": "string", "description": "更新实际完成后的摘要"},
            "planned_summary": {"type": "string", "description": "更新计划中的摘要"},
            "cataloging_status": {"type": "string", "description": "更新编目状态"},
            "metadata": {"type": "object", "description": "更新 section 场景元数据"},
        },
        tool_type="write",
        estimated_cost="free",
        handler=update_outline_node,
    ))

    _r(ToolDef(
        name="delete_outline_node",
        description="删除大纲节点（级联删除所有子节点）。用ID或标题定位。",
        input_schema={
            "id": {"type": "string", "description": "节点ID（优先使用）。也可用 node_id/outline_node_id/title 定位"},
        },
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler=delete_outline_node,
    ))

    # ── Write: Character CRUD ────────────────────────────────────────────

    _r(ToolDef(
        name="create_character",
        description="创建新角色，含完整人物卡片。",
        input_schema={
            "name": {"type": "string", "description": "角色名（必填，最长100字）"},
            "appearance": {"type": "string", "description": "外貌描写"},
            "personality": {"type": "string", "description": "性格特征"},
            "background": {"type": "string", "description": "背景故事"},
            "abilities": {"type": "array", "items": {"type": "string"}, "description": "能力/技能列表"},
            "role_type": {"type": "string", "description": "角色类型：protagonist|supporting|antagonist|mentor|other，默认supporting"},
            "age": {"type": "string", "description": "年龄/时间状态，如 3岁、约16岁、外表16岁实际200岁、成年"},
            "speech_style": {"type": "string", "description": "说话风格，可合并进背景/AI提示词"},
            "motivation": {"type": "string", "description": "当前动机，可合并进背景/AI提示词"},
            "conflict": {"type": "string", "description": "核心冲突，可合并进背景/AI提示词"},
            "ai_config": {"type": "object", "description": "角色AI扮演配置，含 tone_style/catchphrases/verbosity/emotion_tendency/custom_system_prompt"},
            "custom_system_prompt": {"type": "string", "description": "角色AI扮演提示词，可直接存入角色AI配置"},
            # Current-state fields
            "life_status": {"type": "string", "description": "生死状态，如 存活/死亡/失踪/重伤"},
            "current_location": {"type": "string", "description": "当前位置"},
            "realm_or_level": {"type": "string", "description": "境界/等级/修为"},
            "physical_state": {"type": "string", "description": "身体状况"},
            "mental_state": {"type": "string", "description": "心理状态"},
            "current_goal": {"type": "string", "description": "当前目标"},
            "active_conflict": {"type": "string", "description": "当前冲突/困境"},
            "abilities_state": {"type": "string", "description": "能力当前状态（如封印中、受伤无法使用等）"},
            "items_or_assets": {"type": "string", "description": "持有物/资源"},
            "profile": {"type": "object", "description": "稳定写作锁：core_motivation/inner_lack/core_belief/public_persona/hidden_persona/reveal_chapter/moral_taboo/voice/action_habit/trauma_trigger"},
        },
        required=["name"],
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler=create_character,
    ))

    _r(ToolDef(
        name="update_character",
        description="更新角色信息。用ID或角色名定位。只有传入的字段才会被更新。appearance、age 和所有 current_state 字段是逐章覆盖的当前状态，不是追加。",
        input_schema={
            "id": {"type": "string", "description": "角色ID（优先使用）"},
            "name": {"type": "string", "description": "角色名（id为空时用于定位）"},
            "personality": {"type": "string", "description": "更新性格（覆盖）"},
            "background": {"type": "string", "description": "更新背景（必须是重写合并后的完整版本，不是追加片段）"},
            "abilities": {"type": "array", "items": {"type": "string"}, "description": "更新能力列表（替换全部）"},
            "role_type": {"type": "string", "description": "更新角色类型：protagonist|supporting|antagonist|mentor|other"},
            "ai_config": {"type": "object", "description": "更新角色AI扮演配置，含 tone_style/catchphrases/verbosity/emotion_tendency/custom_system_prompt"},
            "custom_system_prompt": {"type": "string", "description": "更新角色AI扮演提示词（必须是完整版本）"},
            # Current-state fields (per-chapter, overwrite old state)
            "appearance": {"type": "string", "description": "当前外貌（逐章覆盖，如受伤/换装/成长等变化）"},
            "age": {"type": "string", "description": "当前年龄/时间状态，如 3岁、约16岁、外表16岁实际200岁"},
            "life_status": {"type": "string", "description": "生死状态，如 存活/死亡/失踪/重伤"},
            "current_location": {"type": "string", "description": "当前位置"},
            "realm_or_level": {"type": "string", "description": "境界/等级/修为"},
            "physical_state": {"type": "string", "description": "身体状况"},
            "mental_state": {"type": "string", "description": "心理状态"},
            "current_goal": {"type": "string", "description": "当前目标"},
            "active_conflict": {"type": "string", "description": "当前冲突/困境"},
            "abilities_state": {"type": "string", "description": "能力当前状态（如封印中、受伤无法使用等）"},
            "items_or_assets": {"type": "string", "description": "持有物/资源"},
            "profile": {"type": "object", "description": "更新稳定写作锁；提交完整 profile 对象"},
        },
        tool_type="write",
        estimated_cost="free",
        handler=update_character,
    ))

    _r(ToolDef(
        name="delete_character",
        description="删除角色。用ID或角色名定位。",
        input_schema={
            "id": {"type": "string", "description": "角色ID（优先使用）"},
            "name": {"type": "string", "description": "角色名（id为空时使用）"},
        },
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler=delete_character,
    ))

    # ── Write: Relationship CRUD ─────────────────────────────────────────

    _r(ToolDef(
        name="create_relationship",
        description="在两个角色之间创建关系。",
        input_schema={
            "source": {"type": "string", "description": "角色A的名字或ID（也可用 from 字段）"},
            "target": {"type": "string", "description": "角色B的名字或ID（也可用 to 字段）"},
            "relationship_type": {"type": "string", "description": "关系类型，如 父子/师徒/恋人/仇敌，默认'关联'"},
            "description": {"type": "string", "description": "关系描述"},
        },
        required=["source", "target"],
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler=create_relationship,
    ))

    _r(ToolDef(
        name="update_relationship",
        description="更新两个角色之间的关系类型或描述。用source+target定位。",
        input_schema={
            "source": {"type": "string", "description": "角色A的名字或ID（必填，也可用 from）"},
            "target": {"type": "string", "description": "角色B的名字或ID（必填，也可用 to）"},
            "relationship_type": {"type": "string", "description": "更新关系类型"},
            "description": {"type": "string", "description": "更新关系描述"},
        },
        required=["source", "target"],
        tool_type="write",
        estimated_cost="free",
        handler=update_relationship,
    ))

    _r(ToolDef(
        name="delete_relationship",
        description="删除两个角色之间的关系。用source+target定位。",
        input_schema={
            "source": {"type": "string", "description": "角色A的名字或ID（必填，也可用 from）"},
            "target": {"type": "string", "description": "角色B的名字或ID（必填，也可用 to）"},
        },
        required=["source", "target"],
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler=delete_relationship,
    ))

    # ── Write: Chapter CRUD ──────────────────────────────────────────────

    _r(ToolDef(
        name="create_chapter",
        description="创建新章节。正文将自动修复禁用句式。创建前须已有对应大纲节点。若正文来自chapter_writer，优先传draft_id/content_ref，避免复制长正文导致截断。",
        input_schema={
            "title": {"type": "string", "description": "章节标题"},
            "content": {"type": "string", "description": "章节正文，1800-2500字。内部换行用\\n。对白可自由使用引号。"},
            "draft_id": {"type": "string", "description": "chapter_writer返回的草稿ID。优先使用它保存完整正文，避免长正文在工具参数中截断。"},
            "content_ref": {"type": "string", "description": "同draft_id，chapter_writer返回的正文引用。"},
            "skip_style_repair": {"type": "boolean", "description": "是否跳过保存时禁用句式自动修复，默认false。"},
            "outline_node_id": {"type": "string", "description": "对应的大纲节点ID（优先）。也可用 outline_node_title/outline_title"},
            "summary": {"type": "string", "description": "章节摘要，可选"},
            "involved_characters": {"type": "array", "items": {"type": "string"}, "description": "本章出场的角色名列表"},
        },
        required=["title"],
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler=create_chapter,
    ))

    _r(ToolDef(
        name="update_chapter",
        description="更新章节。用ID或标题定位。正文将自动修复禁用句式。若正文来自chapter_writer，优先传draft_id/content_ref，避免复制长正文导致截断。",
        input_schema={
            "id": {"type": "string", "description": "章节ID（优先使用）。也可用 chapter_id/title/chapter_title/outline_node_id 定位"},
            "title": {"type": "string", "description": "更新章节标题"},
            "content": {"type": "string", "description": "更新章节正文"},
            "draft_id": {"type": "string", "description": "chapter_writer返回的草稿ID。优先使用它保存完整正文，避免长正文在工具参数中截断。"},
            "content_ref": {"type": "string", "description": "同draft_id，chapter_writer返回的正文引用。"},
            "skip_style_repair": {"type": "boolean", "description": "是否跳过保存时禁用句式自动修复，默认false。"},
            "summary": {"type": "string", "description": "更新章节摘要"},
            "outline_node_id": {"type": "string", "description": "更新关联大纲节点"},
            "involved_characters": {"type": "array", "items": {"type": "string"}, "description": "更新出场角色名列表（替换全部关联）"},
            "context_manifest_id": {"type": "string", "description": "Governed task manifest required for MCP formal writes"},
        },
        tool_type="write",
        estimated_cost="free",
        handler=update_chapter,
    ))

    _r(ToolDef(
        name="list_chapter_versions",
        description="列出章节版本历史。用户说对新章节不满意、想看历史版本、想回退上一版时先用它确认可恢复版本。",
        input_schema={
            "id": {"type": "string", "description": "章节ID（优先）。也可用 chapter_id/title/chapter_title/outline_node_id 定位"},
            "chapter_id": {"type": "string", "description": "章节ID"},
            "title": {"type": "string", "description": "章节标题"},
            "chapter_title": {"type": "string", "description": "章节标题"},
            "outline_node_id": {"type": "string", "description": "关联大纲节点ID"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=list_chapter_versions,
    ))

    _r(ToolDef(
        name="restore_chapter_version",
        description="将章节正文恢复到指定快照，默认回退到上一版，并自动生成新的恢复快照。用户明确说'回退版本''恢复上一版''这章不满意用旧版'时使用。",
        input_schema={
            "id": {"type": "string", "description": "章节ID（优先）。也可用 chapter_id/title/chapter_title/outline_node_id 定位"},
            "chapter_id": {"type": "string", "description": "章节ID"},
            "title": {"type": "string", "description": "章节标题"},
            "chapter_title": {"type": "string", "description": "章节标题"},
            "outline_node_id": {"type": "string", "description": "关联大纲节点ID"},
            "snapshot_id": {"type": "string", "description": "要恢复的快照ID"},
            "version_id": {"type": "string", "description": "同 snapshot_id"},
            "version_number": {"type": "integer", "description": "要恢复的版本号，例如 1"},
            "target": {"type": "string", "description": "previous/first/latest，默认 previous"},
        },
        tool_type="write",
        writes_project_data=True,
        risk_level="medium",
        estimated_cost="free",
        handler=restore_chapter_version,
    ))

    _r(ToolDef(
        name="diff_chapter_versions",
        description="对比章节两个历史版本的正文差异。需要 from_snapshot_id/to_snapshot_id 或 from_version/to_version。",
        input_schema={
            "id": {"type": "string", "description": "章节ID（优先）。也可用 chapter_id/title/chapter_title/outline_node_id 定位"},
            "chapter_id": {"type": "string", "description": "章节ID"},
            "title": {"type": "string", "description": "章节标题"},
            "from_snapshot_id": {"type": "string", "description": "起始快照ID"},
            "to_snapshot_id": {"type": "string", "description": "目标快照ID"},
            "from_version": {"type": "integer", "description": "起始版本号"},
            "to_version": {"type": "integer", "description": "目标版本号"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=diff_chapter_versions,
    ))

    _r(ToolDef(
        name="delete_chapter",
        description="删除章节。自动回退该章节中角色的状态变更。用ID或标题定位。",
        input_schema={
            "id": {"type": "string", "description": "章节ID（优先使用）。也可用 chapter_id/title/chapter_title 定位"},
        },
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler=delete_chapter,
    ))

    # ── Analysis ─────────────────────────────────────────────────────────

    _r(ToolDef(
        name="suggest_conflicts",
        description="基于当前剧情状态生成3种情节冲突建议（人物冲突/势力冲突/内心冲突）。用户说'设计冲突''加点矛盾'时使用。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
            "prompt": {"type": "string", "description": "用户倾向或额外上下文，可选"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler=suggest_conflicts,
    ))

    _r(ToolDef(
        name="design_plot",
        description="设计完整章节剧情——含场景拆解、角色行为、冲突张力、情绪曲线、一致性检查等7个维度。用户说'设计剧情''这章怎么写'时使用。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
            "involved_characters": {"type": "array", "items": {"type": "string"}, "description": "出场的角色名或ID列表，可选"},
            "requirements": {"type": "string", "description": "用户的额外要求，可选"},
            "feedback": {"type": "string", "description": "对上一轮设计的反馈（迭代时使用），可选"},
            "previous_plot": {"type": "string", "description": "上一轮设计的剧情（迭代时使用），可选"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler=design_plot,
    ))

    _r(ToolDef(
        name="detect_character_changes",
        description="检测章节中追踪角色的变化（技能/经历/关系/性格）。三种模式：1) 传draft_id/content_ref检测chapter_writer草稿；2) 传content+title检测未保存正文；3) 传chapter_id检测已保存章节（自动保存变化日志和时间线）。",
        input_schema={
            "content": {"type": "string", "description": "章节正文（检测未保存的正文时使用，与title配合）"},
            "draft_id": {"type": "string", "description": "chapter_writer返回的草稿ID，可替代content"},
            "content_ref": {"type": "string", "description": "同draft_id"},
            "title": {"type": "string", "description": "章节标题（与content配合使用）"},
            "chapter_id": {"type": "string", "description": "已保存的章节ID（检测已保存章节时使用，会自动写入变化日志）"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler=detect_character_changes,
    ))

    _r(ToolDef(
        name="detect_new_worldbuilding",
        description="检测章节正文中引入的新世界观设定——对照已有设定条目，找出正文中出现但尚未录入数据库的地点、规则、势力、种族、文化习俗等。只读不写，返回建议条目列表和原文参考。可传draft_id/content_ref或chapter_id，避免复制长正文。",
        input_schema={
            "content": {"type": "string", "description": "章节正文（可选；优先用draft_id或chapter_id）"},
            "draft_id": {"type": "string", "description": "chapter_writer返回的草稿ID，可替代content"},
            "content_ref": {"type": "string", "description": "同draft_id"},
            "chapter_id": {"type": "string", "description": "已保存章节ID，可替代content"},
            "title": {"type": "string", "description": "章节标题（可选）"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler=detect_new_worldbuilding,
    ))

    _r(ToolDef(
        name="detect_worldbuilding_conflicts",
        description="检测全部世界观条目之间的逻辑矛盾、规则冲突、时间线不一致。",
        input_schema={},
        tool_type="analysis",
        estimated_cost="medium",
        handler=detect_worldbuilding_conflicts,
    ))

    _r(ToolDef(
        name="detect_forbidden_patterns",
        description="检测文本中的禁用句式（如'仿佛''不由得''很愤怒'等70+种AI高频套话）。纯规则匹配，不调LLM。",
        input_schema={
            "text": {"type": "string", "description": "要检测的文本"},
        },
        required=["text"],
        tool_type="analysis",
        estimated_cost="free",
        handler=detect_forbidden_patterns,
    ))

    _r(ToolDef(
        name="preview_writing_context",
        description="写作前上下文预检。显示本次章节写作将读取的大纲、近期摘要、角色当前状态、关系和世界观，并给出缺失/风险提示。质量模式创建或重写章节前应先调用。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "目标大纲节点ID"},
            "requirements": {"type": "string", "description": "用户的写作方向或额外要求，可用于筛选世界观"},
            "involved_characters": {"type": "array", "items": {"type": "string"}, "description": "本章预计出场的角色名或别名列表"},
            "recent_limit": {"type": "integer", "description": "读取最近章节摘要数量，默认5，最大12"},
            "character_limit": {"type": "integer", "description": "返回角色状态数量，默认8，最大16"},
            "worldbuilding_limit": {"type": "integer", "description": "返回世界观条目数量，默认16，最大32"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler=preview_writing_context,
    ))

    # ── RAG Context Tools ───────────────────────────────────────────────

    _r(ToolDef(
        name="search_context",
        description="全文检索项目中所有已索引的内容（章节、大纲、角色、世界观、记忆等）。返回相关度排序的结果列表。适用于跨类型模糊搜索。",
        input_schema={
            "query": {"type": "string", "description": "搜索关键词，支持中英文"},
            "source_types": {"type": "array", "items": {"type": "string"}, "description": "限定搜索范围：chapter|chapter_summary|outline|character|character_timeline|worldbuilding|assistant_memory"},
            "limit": {"type": "integer", "description": "返回条数上限，默认20，最大50"},
        },
        required=["query"],
        tool_type="read",
        estimated_cost="free",
        handler=search_context,
    ))

    _r(ToolDef(
        name="preview_rag_context",
        description="预算感知的上下文打包预览。展示本次写作将使用的大纲、摘要、角色、世界观、记忆等上下文分区，每分区含选取原因、字符预算和相关性评分。与preview_writing_context不同，此工具使用RAG检索。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "目标大纲节点ID"},
            "requirements": {"type": "string", "description": "写作方向或额外要求"},
            "budget_override": {"type": "object", "description": "预算覆盖：max_chapter_chars/max_summary_chars/max_character_chars/max_worldbuilding_chars/max_memory_chars/max_outline_chars/reserve_chars"},
            "pinned_chunk_ids": {"type": "array", "items": {"type": "string"}, "description": "固定选取的内容块ID列表，无论如何都会被包含"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler=preview_rag_context,
    ))

    _r(ToolDef(
        name="explain_context_selection",
        description="解释为什么特定来源被选入或未选入上下文。传入来源ID列表，返回每个来源的评分详情和选取原因。用于理解上下文打包决策。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "目标大纲节点ID"},
            "source_ids": {"type": "array", "items": {"type": "string"}, "description": "要解释的来源ID列表"},
            "requirements": {"type": "string", "description": "写作方向或额外要求"},
        },
        required=["source_ids"],
        tool_type="analysis",
        estimated_cost="free",
        handler=explain_context_selection,
    ))

    _r(ToolDef(
        name="evaluate_chapter",
        description="对章节正文进行8维度80分评估（开头吸引力/情节推进/角色塑造/对话质量/悬念设置/节奏控制/展示性描写/语言质量）。传入draft_id/content_ref或content+title评估未保存正文，或传入chapter_id评估已保存章节。",
        input_schema={
            "content": {"type": "string", "description": "章节正文（评估未保存的正文时使用，与chapter_id二选一）"},
            "draft_id": {"type": "string", "description": "chapter_writer返回的草稿ID，可替代content"},
            "content_ref": {"type": "string", "description": "同draft_id"},
            "title": {"type": "string", "description": "章节标题（与content配合使用）"},
            "chapter_id": {"type": "string", "description": "已保存的章节ID（评估已保存的章节时使用）"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler=evaluate_chapter,
    ))

    # ── Generator: LLM content generation ────────────────────────────────

    _r(ToolDef(
        name="prepare_task_context",
        description="Prepare an auditable, budgeted baseline context manifest for an Agent task.",
        input_schema={
            "task_type": {"type": "string", "description": "writing|cataloging|review|rewrite|new_project|planning"},
            "context_manifest_id": {"type": "string", "description": "Existing manifest ID from a Siming MCP prompt or prior task preparation"},
            "manifest_id": {"type": "string", "description": "Compatibility alias for context_manifest_id"},
            "model": {"type": "string", "description": "Provider:model used for context-window budgeting"},
            "execution_route": {"type": "string", "description": "external_mcp|local_cli_agent|internal_api"},
            "arguments": {"type": "object", "description": "Task arguments used to resolve contract anchors"},
            "run_id": {"type": "string", "description": "Optional Agent run to bind to this manifest"},
            "pinned_chunk_ids": {"type": "array", "items": {"type": "string"}},
            "pinned_source_ids": {"type": "array", "items": {"type": "string"}},
        },
        required=["task_type"],
        tool_type="read",
        estimated_cost="free",
        handler=prepare_task_context,
    ))

    _r(ToolDef(
        name="search_task_context",
        description="Search a baseline task manifest and return source-hash verified evidence candidates.",
        input_schema={
            "context_manifest_id": {"type": "string", "description": "Baseline manifest ID"},
            "run_id": {"type": "string", "description": "Agent run bound to a baseline manifest"},
            "query": {"type": "string", "description": "Task-specific retrieval query"},
            "limit": {"type": "integer", "description": "Maximum verified sources; default 12"},
        },
        required=["query"],
        tool_type="read",
        estimated_cost="free",
        handler=search_task_context,
    ))

    _r(ToolDef(
        name="submit_context_evidence",
        description="Submit Agent-selected baseline/search sources for server-side hash verification before a formal write.",
        input_schema={
            "context_manifest_id": {"type": "string", "description": "Baseline manifest ID"},
            "run_id": {"type": "string", "description": "Agent run bound to a baseline manifest"},
            "sources": {"type": "array", "items": {"type": "object"}, "description": "chunk_id/source_type/source_id/source_hash evidence"},
        },
        required=["sources"],
        tool_type="read",
        estimated_cost="free",
        handler=submit_context_evidence,
    ))

    _r(ToolDef(
        name="chapter_writer",
        description="生成章节正文。加载完整写作规则（行文/对话/去AI味/钩子/技法），将剧情设计和对白素材织成章节正文。创建章节前必须先调用此工具生成正文。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "对应的大纲节点ID（必填）"},
            "requirements": {"type": "string", "description": "写作要求或方向（可选）"},
            "involved_characters": {"type": "array", "items": {"type": "string"}, "description": "本章出场的角色名列表"},
            "previous_plot": {"type": "object", "description": "design_plot 返回的剧情设计JSON（可选，如有则传入）"},
            "previous_roleplay": {"type": "array", "items": {"type": "object"}, "description": "roleplay_character 或 dialogue_battle 返回的对白结果（可选，如有则传入）"},
            "mode": {"type": "string", "enum": ["fast", "quality"], "description": "写作模式。fast 使用精简直写提示词和更少外围轮次；quality 使用完整技法流程。两者都必须遵守角色、设定、时间线一致性和写后归档契约。默认由系统注入。"},
        },
        required=["outline_node_id"],
        tool_type="generator",
        estimated_cost="high",
        handler=chapter_writer,
    ))

    _r(ToolDef(
        name="character_writer",
        description="生成角色卡片。加载完整角色设计规则（深度、一致性、反套路），根据项目上下文和用户要求创造出立体、有记忆点的角色。创建角色前必须先调用此工具生成角色卡片。",
        input_schema={
            "name": {"type": "string", "description": "角色名（可选，不传则由AI生成）"},
            "role_type": {"type": "string", "description": "建议角色类型：protagonist|supporting|antagonist|mentor|other"},
            "requirements": {"type": "string", "description": "用户对角色的要求或方向（可选）"},
        },
        tool_type="generator",
        estimated_cost="high",
        handler=character_writer,
    ))

    _r(ToolDef(
        name="outline_writer",
        description="生成大纲节点。加载故事结构规则，根据已有大纲、角色和世界观设计有因果推进和节奏变化的大纲节点。创建大纲前应先调用此工具生成大纲。",
        input_schema={
            "parent_id": {"type": "string", "description": "父节点ID（可选）"},
            "requirements": {"type": "string", "description": "用户对大纲的要求或方向（可选）"},
            "batch_count": {"type": "integer", "description": "生成节点数量，默认1，上限8"},
        },
        tool_type="generator",
        estimated_cost="high",
        handler=outline_writer,
    ))

    _r(ToolDef(
        name="worldbuilding_writer",
        description="生成世界观设定条目。加载维度专属设计规则（地理/历史/势力/规则体系/种族/文化），创造有深度、逻辑自洽、服务于剧情的世界观设定。创建世界观前应先调用此工具生成设定。",
        input_schema={
            "dimension": {"type": "string", "description": "维度：geography|history|factions|power_system|races|culture，默认culture"},
            "title": {"type": "string", "description": "建议标题（可选）"},
            "requirements": {"type": "string", "description": "用户对设定的要求或方向（可选）"},
        },
        tool_type="generator",
        estimated_cost="high",
        handler=worldbuilding_writer,
    ))

    _r(ToolDef(
        name="rewrite_text",
        description="按指定风格改写文本。自动修复禁用句式。用户说'改写''重写''润色'时使用。",
        input_schema={
            "text": {"type": "string", "description": "要改写的原文"},
            "style": {"type": "string", "description": "目标风格：vivid|concise|serious|humorous|poetic，可选"},
            "prompt": {"type": "string", "description": "额外的改写要求，可选"},
        },
        required=["text"],
        tool_type="generator",
        estimated_cost="low",
        handler=rewrite_text,
    ))

    _r(ToolDef(
        name="expand_text",
        description="扩充文本细节。自动修复禁用句式。用户说'扩写''丰富''展开'时使用。",
        input_schema={
            "text": {"type": "string", "description": "要扩写的原文"},
            "prompt": {"type": "string", "description": "扩写方向提示，可选"},
        },
        required=["text"],
        tool_type="generator",
        estimated_cost="low",
        handler=expand_text,
    ))

    _r(ToolDef(
        name="continue_text",
        description="从指定文本结尾处继续写作。自动修复禁用句式。用户说'续写''继续写'时使用。",
        input_schema={
            "text": {"type": "string", "description": "上文内容"},
            "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
            "prompt": {"type": "string", "description": "续写方向提示，可选"},
        },
        required=["text"],
        tool_type="generator",
        estimated_cost="low",
        handler=continue_text,
    ))

    _r(ToolDef(
        name="roleplay_character",
        description="让单个角色对场景做出回应（对话/动作/内心独白）。AI扮演该角色，结果可直接用于章节正文。",
        input_schema={
            "character_id": {"type": "string", "description": "角色ID（优先使用）"},
            "character_name": {"type": "string", "description": "角色名（character_id为空时使用）"},
            "situation": {"type": "string", "description": "场景描述——告诉角色当前发生了什么"},
            "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
        },
        required=["situation"],
        tool_type="generator",
        estimated_cost="medium",
        handler=roleplay_character,
    ))

    _r(ToolDef(
        name="dialogue_battle",
        description="多个角色按回合制对戏。每个角色依次发言并承接上文，适用于需要自然对话的场景。",
        input_schema={
            "character_names": {"type": "array", "items": {"type": "string"}, "description": "参与对戏的角色名列表"},
            "character_ids": {"type": "array", "items": {"type": "string"}, "description": "参与对戏的角色ID列表（与character_names二选一或互补）"},
            "scene": {"type": "string", "description": "场景描述——正在发生什么"},
            "turns": {"type": "integer", "description": "对戏回合数，默认2，最大4"},
            "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
        },
        required=["scene"],
        tool_type="generator",
        estimated_cost="medium",
        handler=dialogue_battle,
    ))

    # ── Web ──────────────────────────────────────────────────────────────

    _r(ToolDef(
        name="web_search",
        description="搜索互联网获取最新信息。适用于查证事实、获取参考资料（历史/地理/文化/科技等）。只读，可在任何阶段使用。",
        input_schema={
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "最大结果数，默认5，上限10"},
        },
        required=["query"],
        tool_type="web",
        estimated_cost="low",
        handler=web_search,
    ))

    # ── Memory ───────────────────────────────────────────────────────────

    _r(ToolDef(
        name="remember",
        description="保存一条持久化记忆。用户表达偏好或搜索到有用资料后使用。同key自动覆盖。回复中不要提及已保存。",
        input_schema={
            "key": {"type": "string", "description": "简短的记忆标识"},
            "value": {"type": "string", "description": "记忆内容"},
            "category": {"type": "string", "description": "分类：user_preference|project_fact|writing_style|research_note|workflow_preference，默认user_preference"},
            "importance": {"type": "integer", "description": "重要性0-10，默认5。≥7才会被优先召回"},
        },
        required=["key", "value"],
        tool_type="memory",
        estimated_cost="low",
        handler=remember,
    ))

    _r(ToolDef(
        name="recall",
        description="按关键词查询已保存的记忆。每次新对话开始时先查询相关记忆。",
        input_schema={
            "query": {"type": "string", "description": "搜索记忆的关键词"},
            "category": {"type": "string", "description": "可选分类过滤：user_preference|project_fact|writing_style|research_note|workflow_preference"},
            "limit": {"type": "integer", "description": "返回条数上限，默认10，最大20"},
        },
        tool_type="memory",
        estimated_cost="low",
        handler=recall,
    ))

    _r(ToolDef(
        name="forget",
        description="删除记忆。用户说'不要记住''忘掉'时使用。按ID或key定位。",
        input_schema={
            "id": {"type": "string", "description": "记忆记录ID（优先使用）"},
            "key": {"type": "string", "description": "记忆标识（id为空时使用，删除所有匹配key的记忆）"},
        },
        tool_type="memory",
        estimated_cost="low",
        handler=forget,
    ))

    _r(ToolDef(
        name="list_memories",
        description="列出已保存的记忆。可按分类筛选。用于浏览和管理记忆。",
        input_schema={
            "category": {"type": "string", "description": "可选分类过滤：user_preference|project_fact|writing_style|research_note|workflow_preference"},
            "limit": {"type": "integer", "description": "返回条数上限，默认30，最大100"},
        },
        tool_type="memory",
        estimated_cost="free",
        handler=list_memories,
    ))

    # ── External Agent Reporting Tools ───────────────────────────────────
    from .tools.external_agent import (
        start_agent_run,
        report_agent_plan,
        report_agent_progress,
        report_context_selected,
        append_draft_chunk,
        mark_draft_ready,
        finish_agent_run,
    )
    from .tools.local_cli_agent import start_local_cli_agent_run, wait_local_cli_agent_run

    _r(ToolDef(
        name="start_agent_run",
        description="Start a new external Agent run. Returns run_id for subsequent reporting.",
        input_schema={
            "client_name": {"type": "string", "description": "Client name: claude-code, codex, etc."},
            "title": {"type": "string", "description": "Optional run title"},
        },
        tool_type="read",  # Telemetry only, not project content
        estimated_cost="free",
        handler=start_agent_run,
    ))

    _r(ToolDef(
        name="report_agent_plan",
        description="Report the execution plan for an Agent run.",
        input_schema={
            "run_id": {"type": "string", "description": "Agent run ID"},
            "plan": {"type": "array", "items": {"type": "string"}, "description": "Plan steps"},
        },
        required=["run_id"],
        tool_type="read",
        estimated_cost="free",
        handler=report_agent_plan,
    ))

    _r(ToolDef(
        name="report_agent_progress",
        description="Report a progress update for an Agent run.",
        input_schema={
            "run_id": {"type": "string", "description": "Agent run ID"},
            "message": {"type": "string", "description": "Progress message"},
            "step": {"type": "integer", "description": "Optional step index"},
        },
        required=["run_id"],
        tool_type="read",
        estimated_cost="free",
        handler=report_agent_progress,
    ))

    _r(ToolDef(
        name="report_context_selected",
        description="Report which context was selected for reasoning.",
        input_schema={
            "run_id": {"type": "string", "description": "Agent run ID"},
            "sources": {"type": "array", "items": {"type": "object"}, "description": "Selected sources"},
        },
        required=["run_id"],
        tool_type="read",
        estimated_cost="free",
        handler=report_context_selected,
    ))

    _r(ToolDef(
        name="append_draft_chunk",
        description="Stream a draft content chunk to the Agent run.",
        input_schema={
            "run_id": {"type": "string", "description": "Agent run ID"},
            "content": {"type": "string", "description": "Draft content chunk"},
            "chunk_index": {"type": "integer", "description": "Chunk sequence number"},
        },
        required=["run_id", "content"],
        tool_type="read",
        estimated_cost="free",
        handler=append_draft_chunk,
    ))

    _r(ToolDef(
        name="mark_draft_ready",
        description="Signal that a draft is complete.",
        input_schema={
            "run_id": {"type": "string", "description": "Agent run ID"},
            "content_type": {"type": "string", "description": "Content type: chapter, outline, character, worldbuilding"},
            "summary": {"type": "string", "description": "Brief description of the draft"},
        },
        required=["run_id"],
        tool_type="read",
        estimated_cost="free",
        handler=mark_draft_ready,
    ))

    _r(ToolDef(
        name="finish_agent_run",
        description="Signal Agent run completion with a summary.",
        input_schema={
            "run_id": {"type": "string", "description": "Agent run ID"},
            "summary": {"type": "string", "description": "Final summary of what was accomplished"},
        },
        required=["run_id"],
        tool_type="read",
        estimated_cost="free",
        handler=finish_agent_run,
    ))

    _r(ToolDef(
        name="start_local_cli_agent_run",
        description=(
            "Start a Siming-managed local CLI Agent worker (Claude/Codex/opencode). "
            "The CLI reads project files directly but must write/delete/update only through Siming MCP tools. "
            "Returns an Agent run_id whose events can be streamed in the UI."
        ),
        input_schema={
            "task_type": {"type": "string", "description": "general|cataloging|writing"},
            "user_request": {"type": "string", "description": "User request for the local CLI agent"},
            "provider": {"type": "string", "description": "Optional local CLI provider id, e.g. claude_cli/codex_cli/opencode_cli/mimocode_cli/cursor_cli/kilocode_cli/qwen_code_cli/hermes_cli/openclaw_cli"},
            "outline_node_id": {"type": "string", "description": "Writing target outline node for the governed baseline"},
            "chapter_id": {"type": "string", "description": "Cataloging/review target chapter for the governed baseline"},
            "context_manifest_id": {"type": "string", "description": "Optional previously prepared baseline manifest"},
            "pinned_chunk_ids": {"type": "array", "items": {"type": "string"}, "description": "Author-pinned context chunks"},
            "pinned_source_ids": {"type": "array", "items": {"type": "string"}, "description": "Author-pinned context source ids"},
        },
        tool_type="scheduler",
        estimated_cost="local_cli",
        handler=start_local_cli_agent_run,
    ))

    _r(ToolDef(
        name="wait_local_cli_agent_run",
        description=(
            "Wait for a Siming-managed local CLI Agent run to finish and validate that writes landed in the database. "
            "For writing runs, detects direct file edits/orphan chapter mirror files and fails the plan instead of reporting false success."
        ),
        input_schema={
            "run_id": {"type": "string", "description": "Agent run ID returned by start_local_cli_agent_run"},
            "task_type": {"type": "string", "description": "general|cataloging|writing"},
            "outline_node_id": {"type": "string", "description": "Expected target outline node for writing validation"},
            "timeout_seconds": {"type": "integer", "description": "Maximum wait time; default 1800"},
            "startup_timeout_seconds": {"type": "integer", "description": "Maximum time to wait for cli_started; default 10"},
            "poll_seconds": {"type": "number", "description": "Polling interval; default 2"},
        },
        required=["run_id"],
        tool_type="scheduler",
        estimated_cost="free",
        handler=wait_local_cli_agent_run,
    ))

    # ── External Writing Tools ───────────────────────────────────────────
    from .tools.external_writing import (
        prepare_external_writing_context,
        save_external_chapter_draft,
        get_external_chapter_draft,
        record_external_quality_review,
    )
    from .tools.external_story_updates import (
        apply_external_story_updates,
    )
    from .tools.story_granularity import (
        archive_chapter_after_write,
        inspect_story_granularity,
        repair_story_granularity,
    )
    from .tools.novel_creation import (
        start_novel_creation_session,
        draft_novel_blueprint,
        review_novel_blueprint,
        apply_novel_blueprint,
        list_imported_files,
        read_imported_file,
    )
    from .tools.novel_creation_v2 import (
        get_novel_creation_session,
        generate_novel_creation_stage,
        submit_novel_creation_stage,
    )
    from .tools.mcp_status import (
        get_mcp_permission_status,
    )
    from .tools.external_cataloging import (
        start_external_cataloging_job,
        get_next_external_cataloging_chapter,
        save_external_cataloging_facts,
        save_external_cataloging_candidates,
        verify_external_cataloging_progress,
    )
    from .tools.project_status import get_project_archive_status

    _r(ToolDef(
        name="prepare_external_writing_context",
        description="Build a complete writing context package for external agents. API-free: does not call LLM. Returns prompt pack, outline, characters, worldbuilding, summaries, quality rubric, and forbidden patterns.",
        input_schema={
            "outline_node_id": {"type": "string", "description": "Target outline node ID"},
            "mode": {"type": "string", "description": "Writing mode: quality|fast"},
            "include_prompt_pack": {"type": "boolean", "description": "Include public prompt pack (default true)"},
            "requirements": {"type": "string", "description": "Additional writing requirements"},
            "context_manifest_id": {"type": "string", "description": "Prepared governed baseline manifest ID"},
            "model": {"type": "string", "description": "Model identity used to resolve the context budget"},
            "pinned_chunk_ids": {"type": "array", "items": {"type": "string"}},
            "pinned_source_ids": {"type": "array", "items": {"type": "string"}},
        },
        tool_type="read",
        estimated_cost="free",
        handler=prepare_external_writing_context,
    ))

    _r(ToolDef(
        name="save_external_chapter_draft",
        description="Save an externally generated chapter draft. API-free. Returns draft_id/content_ref for use with create_chapter.",
        input_schema={
            "content": {"type": "string", "description": "Chapter content to save"},
            "title": {"type": "string", "description": "Chapter title"},
            "outline_node_id": {"type": "string", "description": "Linked outline node ID"},
            "context_manifest_id": {"type": "string", "description": "Prepared governed baseline manifest ID"},
            "source_agent": {"type": "string", "description": "Source agent name (e.g. claude-code)"},
            "quality_review_json": {"type": "string", "description": "Optional quality review JSON"},
        },
        required=["content"],
        tool_type="read",
        estimated_cost="free",
        handler=save_external_chapter_draft,
    ))

    _r(ToolDef(
        name="get_external_chapter_draft",
        description="Get a saved chapter draft by ID. API-free.",
        input_schema={
            "draft_id": {"type": "string", "description": "Draft ID to retrieve"},
            "content_ref": {"type": "string", "description": "Alias for draft_id"},
        },
        required=["draft_id"],
        tool_type="read",
        estimated_cost="free",
        handler=get_external_chapter_draft,
    ))

    _r(ToolDef(
        name="record_external_quality_review",
        description="Record a quality review from an external agent. API-free. Stores review scores, issues, and suggestions.",
        input_schema={
            "draft_id": {"type": "string", "description": "Draft ID to review"},
            "content_ref": {"type": "string", "description": "Alias for draft_id"},
            "chapter_id": {"type": "string", "description": "Chapter ID to review"},
            "scores": {"type": "object", "description": "Score dict: {dimension: score}"},
            "issues": {"type": "array", "items": {"type": "string"}, "description": "List of issues found"},
            "revision_suggestions": {"type": "array", "items": {"type": "string"}, "description": "Suggested revisions"},
            "pass": {"type": "boolean", "description": "Whether the review passes"},
            "reviewer_model": {"type": "string", "description": "Model that did the review"},
            "prompt_pack_version": {"type": "string", "description": "Prompt pack version used"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=record_external_quality_review,
    ))

    _r(ToolDef(
        name="apply_external_story_updates",
        description="Apply character/worldbuilding/outline updates after external writing. Supports manual (preview) and auto (apply) modes.",
        input_schema={
            "chapter_id": {"type": "string", "description": "Chapter ID for context"},
            "updates": {"type": "object", "description": "Updates grouped by characters, worldbuilding, outline, chapter_summary"},
            "mode": {"type": "string", "description": "manual|auto. Manual returns candidates, auto applies them."},
            "context_manifest_id": {"type": "string", "description": "Governed task manifest for external writing updates"},
        },
        tool_type="write",
        writes_project_data=True,
        risk_level="medium",
        estimated_cost="free",
        handler=apply_external_story_updates,
    ))

    _r(ToolDef(
        name="archive_chapter_after_write",
        description="Create and optionally apply standard cataloging candidates after a chapter is written. Unifies chapter summary, chapter/section outline, section scene state, narrative_state, character state, worldbuilding, and links.",
        input_schema={
            "chapter_id": {"type": "string", "description": "Saved chapter ID"},
            "draft_id": {"type": "string", "description": "Optional draft ID/content_ref used to generate the chapter"},
            "content_ref": {"type": "string", "description": "Alias for draft_id"},
            "outline_node_id": {"type": "string", "description": "Linked outline node ID"},
            "candidates": {"type": "array", "items": {"type": "object"}, "description": "Optional standard cataloging candidates. chapter_summary may include narrative_state; section outlines may include scene state fields."},
            "mode": {"type": "string", "description": "auto|manual. Auto applies candidates; manual stores them for review."},
            "source": {"type": "string", "description": "internal_writer|local_cli|external_agent|repair"},
            "generate_if_missing": {"type": "boolean", "description": "Generate fallback candidates when none or required candidates are missing. Default true."},
            "model": {"type": "string", "description": "Optional model for post-write archive candidate generation"},
            "context_manifest_id": {"type": "string", "description": "Governed task manifest required for MCP formal writes"},
        },
        tool_type="write",
        writes_project_data=True,
        risk_level="medium",
        estimated_cost="model_or_free",
        handler=archive_chapter_after_write,
    ))

    _r(ToolDef(
        name="update_narrative_ledger_entry",
        description="Manually revise or invalidate one narrative ledger entry while preserving its prior fact version.",
        input_schema={
            "entry_id": {"type": "string", "description": "Narrative ledger entry ID"},
            "title": {"type": "string", "description": "Optional corrected title"},
            "status": {"type": "string", "description": "Optional lifecycle status, such as active, open, fulfilled, invalidated"},
            "storyline": {"type": "string", "description": "Optional corrected storyline"},
            "note": {"type": "string", "description": "Reason or evidence for this manual revision"},
        },
        tool_type="write",
        writes_project_data=True,
        risk_level="medium",
        estimated_cost="free",
        handler=update_narrative_ledger_entry,
    ))

    _r(ToolDef(
        name="get_narrative_ledger",
        description="Read active completed beats, revealed clues, narrative promises, and storyline states.",
        input_schema={
            "chapter_id": {"type": "string", "description": "Optional chapter ID"},
            "types": {"type": "array", "items": {"type": "string"}, "description": "completed_beat|revealed_clue|narrative_promise|storyline_state"},
            "statuses": {"type": "array", "items": {"type": "string"}, "description": "Optional lifecycle statuses"},
            "storyline": {"type": "string", "description": "Optional storyline filter"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=get_narrative_ledger,
    ))

    _r(ToolDef(
        name="get_narrative_governance",
        description="Read structured foreshadowings, causal edges, narrative debts, character dynamic state, quality metrics, and narrative checkpoints.",
        input_schema={
            "chapter_id": {"type": "string", "description": "Optional current chapter ID"},
            "view": {"type": "string", "enum": ["all", "chapter", "due", "risk"], "description": "Dashboard filter"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=get_narrative_governance,
    ))

    _r(ToolDef(
        name="apply_narrative_governance_candidates",
        description="Preview or apply structured narrative governance candidates after cataloging or chapter writing.",
        input_schema={
            "chapter_id": {"type": "string", "description": "Source chapter ID"},
            "mode": {"type": "string", "enum": ["preview", "apply"], "description": "Preview is non-mutating; apply writes project state"},
            "candidates": {"type": "array", "items": {"type": "object"}, "description": "foreshadowing, causal_edge, narrative_debt, character_state, or quality_metric candidates"},
        },
        required=["candidates"],
        tool_type="write",
        writes_project_data=True,
        risk_level="medium",
        estimated_cost="free",
        handler=apply_narrative_governance_candidates,
    ))

    _r(ToolDef(name="list_narrative_checkpoints", description="List linear project-level narrative checkpoints.", input_schema={"limit": {"type": "integer"}}, tool_type="read", estimated_cost="free", handler=list_narrative_checkpoints))
    _r(ToolDef(name="diff_narrative_checkpoint", description="Compare current structured narrative state with a saved checkpoint.", input_schema={"checkpoint_id": {"type": "string"}}, required=["checkpoint_id"], tool_type="read", estimated_cost="free", handler=diff_narrative_checkpoint))
    _r(ToolDef(name="restore_narrative_governance_checkpoint", description="Restore structured narrative state from a project checkpoint. Requires write confirmation under the active MCP permission policy.", input_schema={"checkpoint_id": {"type": "string"}}, required=["checkpoint_id"], tool_type="write", writes_project_data=True, risk_level="high", estimated_cost="free", handler=restore_narrative_governance_checkpoint))

    _r(ToolDef(
        name="inspect_story_granularity",
        description="Audit project or chapter story granularity: summaries, chapter outline, section events, narrative facts, character states, and links.",
        input_schema={
            "chapter_id": {"type": "string", "description": "Optional chapter ID to audit"},
            "level": {"type": "string", "description": "basic|narrative. Default narrative."},
            "limit": {"type": "integer", "description": "Maximum chapters to audit, default 200"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=inspect_story_granularity,
    ))

    _r(ToolDef(
        name="repair_story_granularity",
        description="Create post-write archive runs to repair missing story granularity. Defaults to manual candidate review.",
        input_schema={
            "chapter_id": {"type": "string", "description": "Optional chapter ID to repair"},
            "limit": {"type": "integer", "description": "Maximum chapters to inspect/repair, default 20"},
            "mode": {"type": "string", "description": "manual|auto. Manual is default."},
            "repair_level": {"type": "string", "description": "basic|narrative. Basic is default; narrative only when explicitly requested."},
            "force": {"type": "boolean", "description": "Repair even chapters that currently pass the audit"},
            "model": {"type": "string", "description": "Optional model for repair candidate generation"},
        },
        tool_type="write",
        writes_project_data=True,
        risk_level="medium",
        estimated_cost="model_or_free",
        handler=repair_story_granularity,
    ))

    # ── Novel Creation Tools ─────────────────────────────────────────────
    _r(ToolDef(
        name="start_novel_creation_session",
        description="Start a new novel creation session. API-free. Returns interview checklist and prompt pack.",
        input_schema={
            "mode": {"type": "string", "description": "internal_llm|external_agent"},
            "user_brief": {"type": "string", "description": "User's novel brief"},
            "target_audience": {"type": "string", "description": "Target audience"},
            "genre": {"type": "string", "description": "Novel genre"},
            "platform": {"type": "string", "description": "Publishing platform"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=start_novel_creation_session,
    ))

    _r(ToolDef(
        name="draft_novel_blueprint",
        description="Draft novel blueprints for a creation session. Supports template, hybrid, internal_llm and external_agent modes.",
        input_schema={
            "session_id": {"type": "string", "description": "Creation session ID"},
            "execution_mode": {"type": "string", "description": "template|hybrid|internal_llm|external_agent. hybrid uses template+LLM for creative output."},
            "user_brief": {"type": "string", "description": "Additional user brief"},
            "feedback": {"type": "string", "description": "User feedback for refining or regenerating previous blueprint options"},
            "revision_mode": {"type": "string", "description": "initial|refine|regenerate. Use refine to adjust current direction, regenerate to restart options from feedback."},
            "enhance_with_llm": {"type": "boolean", "description": "Optional slow LLM enhancement. Default false keeps template drafting instant."},
            "skip_questions": {"type": "boolean", "description": "Skip clarifying questions and generate blueprints directly. Default false."},
            "depth": {"type": "string", "description": "concept|full. Concept returns three lightweight cards and keeps full source inside the session."},
        },
        required=["session_id"],
        tool_type="read",
        estimated_cost="free",
        handler=draft_novel_blueprint,
    ))

    _r(ToolDef(
        name="review_novel_blueprint",
        description="Review novel blueprints. Supports hybrid, internal_llm and external_agent modes.",
        input_schema={
            "session_id": {"type": "string", "description": "Creation session ID"},
            "execution_mode": {"type": "string", "description": "hybrid|internal_llm|external_agent"},
            "blueprint": {"type": "object", "description": "Blueprint to review (optional, saves to session)"},
        },
        required=["session_id"],
        tool_type="read",
        estimated_cost="free",
        handler=review_novel_blueprint,
    ))

    _r(ToolDef(
        name="apply_novel_blueprint",
        description="Apply a confirmed blueprint to create a real Siming project with characters, worldbuilding, and outline.",
        input_schema={
            "session_id": {"type": "string", "description": "Creation session ID"},
            "blueprint_index": {"type": "integer", "description": "Which blueprint to apply (default 0)"},
            "mode": {"type": "string", "description": "manual|auto. Manual returns candidates, auto creates project."},
            "blueprint": {"type": "object", "description": "Optional blueprint override to apply directly."},
        },
        required=["session_id"],
        tool_type="write",
        writes_project_data=True,
        risk_level="medium",
        estimated_cost="free",
        handler=apply_novel_blueprint,
    ))

    _r(ToolDef(
        name="get_novel_creation_session",
        description="Read a resumable V2 novel creation session, its stage states, checkpoints, and recent runs.",
        input_schema={
            "session_id": {"type": "string", "description": "Creation session ID"},
        },
        required=["session_id"],
        tool_type="read",
        estimated_cost="free",
        handler=get_novel_creation_session,
    ))

    _r(ToolDef(
        name="generate_novel_creation_stage",
        description="Generate one V2 creation stage or the complete quick pipeline. Saves only to the session draft; it never writes project files.",
        input_schema={
            "session_id": {"type": "string", "description": "Creation session ID"},
            "stage": {"type": "string", "description": "constraints|concepts|world_style|characters|locations|macro_outline|opening_outline|final_review|all"},
            "model": {"type": "string", "description": "Optional model override for this stage"},
            "use_model": {"type": "boolean", "description": "Use the selected model to deepen the contract baseline"},
            "auto_confirm": {"type": "boolean", "description": "Confirm generated stages automatically; intended for quick mode"},
            "session_patch": {"type": "object", "description": "Optional editable form or selected concept update before generation"},
        },
        required=["session_id", "stage"],
        tool_type="write",
        writes_project_data=False,
        risk_level="low",
        estimated_cost="model_or_free",
        handler=generate_novel_creation_stage,
    ))

    _r(ToolDef(
        name="submit_novel_creation_stage",
        description="Submit and optionally confirm an edited V2 creation stage. Changes remain in the session until apply_novel_blueprint.",
        input_schema={
            "session_id": {"type": "string", "description": "Creation session ID"},
            "stage": {"type": "string", "description": "Stage identifier"},
            "data": {"type": "object", "description": "Author or external-agent stage result"},
            "confirm": {"type": "boolean", "description": "Confirm this stage and continue"},
            "source": {"type": "string", "description": "author|local_cli|external_agent|model"},
        },
        required=["session_id", "stage", "data"],
        tool_type="write",
        writes_project_data=False,
        risk_level="low",
        estimated_cost="free",
        handler=submit_novel_creation_stage,
    ))

    _r(ToolDef(
        name="list_imported_files",
        description="List all imported files in the working directory. Returns file names, paths, sizes, and modification times.",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=list_imported_files,
    ))

    _r(ToolDef(
        name="read_imported_file",
        description="Read the content of a specific imported file from the working directory.",
        input_schema={
            "filename": {"type": "string", "description": "Name of the file to read (from list_imported_files)"},
            "max_size": {"type": "integer", "description": "Max characters to read (default 50000)"},
        },
        required=["filename"],
        tool_type="read",
        estimated_cost="free",
        handler=read_imported_file,
    ))

    _r(ToolDef(
        name="get_mcp_permission_status",
        description="Report current MCP permission status: effective pack, source, CLI override status.",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=get_mcp_permission_status,
    ))

    # ── External Cataloging Tools ────────────────────────────────────────
    _r(ToolDef(
        name="start_external_cataloging_job",
        description="Create a cataloging job for external agent mode. API-free. Creates one chapter run per chapter.",
        input_schema={
            "chapter_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional chapter IDs to catalog. Omit for all chapters."},
        },
        tool_type="read",
        estimated_cost="free",
        handler=start_external_cataloging_job,
    ))

    _r(ToolDef(
        name="get_next_external_cataloging_chapter",
        description="Get the next pending chapter for external cataloging. Returns chapter text, character/wb indexes, and prompt pack. API-free.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "phase": {
                "type": "string",
                "description": "facts, candidates, or merged. merged is the experimental single-stage flow that directly saves candidates in chapter_order.",
            },
            "include_content": {
                "type": "boolean",
                "description": "Whether to return chapter text in the tool result. Set false when the Agent can read content_file_path directly.",
            },
            "include_prompt_pack": {
                "type": "boolean",
                "description": "Whether to include the full prompt pack in the tool result. Set false when the task file already contains the shared prompt.",
            },
            "include_context_indexes": {
                "type": "boolean",
                "description": "Whether to return character/worldbuilding/outline indexes. Set false when the Agent can search the project mirror directly.",
            },
        },
        required=["job_id"],
        tool_type="read",
        estimated_cost="free",
        handler=get_next_external_cataloging_chapter,
    ))

    _r(ToolDef(
        name="save_external_cataloging_facts",
        description="Save facts extracted by the external model. API-free.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "chapter_id": {"type": "string", "description": "Chapter ID"},
            "facts": {"type": "array", "items": {"type": "object"}, "description": "Extracted facts"},
        },
        required=["job_id", "chapter_id"],
        tool_type="write",
        writes_project_data=True,
        risk_level="low",
        estimated_cost="free",
        handler=save_external_cataloging_facts,
    ))

    _r(ToolDef(
        name="save_external_cataloging_candidates",
        description="Save candidates proposed by the external model. API-free.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "chapter_id": {"type": "string", "description": "Chapter ID"},
            "phase": {"type": "string", "description": "Optional. Use merged for the experimental single-stage flow."},
            "candidates": {"type": "array", "items": {"type": "object"}, "description": "Proposed candidates"},
        },
        required=["job_id", "chapter_id"],
        tool_type="write",
        writes_project_data=True,
        risk_level="low",
        estimated_cost="free",
        handler=save_external_cataloging_candidates,
    ))

    _r(ToolDef(
        name="verify_external_cataloging_progress",
        description="Verify cataloging progress with counts and samples. API-free.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
        },
        required=["job_id"],
        tool_type="read",
        estimated_cost="free",
        handler=verify_external_cataloging_progress,
    ))

    _r(ToolDef(
        name="get_project_archive_status",
        description="Get project archive status: chapter/character/outline/worldbuilding counts, last cataloging job, warnings, and recommended next steps. Use to verify project data exists before reporting completion.",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=get_project_archive_status,
    ))

    # ── Prompt Pack Tools ────────────────────────────────────────────────
    from .tools.prompt_packs import (
        get_moshu_usage_guide,
        list_prompt_packs,
        get_prompt_pack,
        get_tool_playbook,
        get_quality_rubric,
    )

    _r(ToolDef(
        name="get_moshu_usage_guide",
        description="First-stop guide for Claude Code/Codex/external agents. Explains the correct Siming workflow for importing, API-free cataloging, internal cataloging, external writing, and verification. API-free; call this when unsure which tools to use.",
        input_schema={
            "scenario": {"type": "string", "description": "quickstart|import_file|cataloging_no_api|cataloging_internal|writing_no_api|writing_internal"},
            "no_api": {"type": "boolean", "description": "True when Siming internal API is unavailable or the external agent should do the reasoning itself."},
        },
        tool_type="read",
        estimated_cost="free",
        handler=get_moshu_usage_guide,
    ))

    _r(ToolDef(
        name="list_prompt_packs",
        description="List available public prompt packs. Returns pack_id, scope, title, summary.",
        input_schema={
            "scope": {"type": "string", "description": "Filter by scope: new_project|chapter_writing|chapter_review|character_design|worldbuilding|outline_planning|cataloging|anti_ai_review"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=list_prompt_packs,
    ))

    _r(ToolDef(
        name="get_prompt_pack",
        description="Get a specific prompt pack with full system prompt, workflow, quality rubric, and forbidden patterns.",
        input_schema={
            "scope": {"type": "string", "description": "Prompt scope: chapter_writing|chapter_review|new_project|character_design|worldbuilding|outline_planning|cataloging|anti_ai_review"},
            "mode": {"type": "string", "description": "Mode: quality|fast|external_no_api"},
            "pack_id": {"type": "string", "description": "Direct pack_id lookup (overrides scope/mode)"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=get_prompt_pack,
    ))

    _r(ToolDef(
        name="get_tool_playbook",
        description="Get a tool usage playbook explaining how to use a specific tool in a given scenario.",
        input_schema={
            "tool_name": {"type": "string", "description": "Tool name to get playbook for"},
            "scenario": {"type": "string", "description": "Scenario: external_writing|internal_writing|external_cataloging|internal_cataloging"},
        },
        required=["tool_name"],
        tool_type="read",
        estimated_cost="free",
        handler=get_tool_playbook,
    ))

    _r(ToolDef(
        name="get_quality_rubric",
        description="Get quality rubric with scoring dimensions and passing criteria.",
        input_schema={
            "scope": {"type": "string", "description": "Scope: chapter_writing|chapter_review"},
            "pack_id": {"type": "string", "description": "Direct pack_id lookup (overrides scope)"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=get_quality_rubric,
    ))


_register_all()


# ---------------------------------------------------------------------------
# Classify all tools into permission packs
# ---------------------------------------------------------------------------

def _classify_all() -> None:
    """Assign permission metadata to all registered tools.

    This is a post-registration step that adds permission_tags, risk_level,
    writes_project_data, and exposure flags based on tool_type and name patterns.
    """
    _WRITE_PROJECT_DATA = {
        "create_chapter", "update_chapter", "delete_chapter", "restore_chapter_version",
        "create_character", "update_character", "delete_character",
        "create_outline_node", "create_outline_nodes", "update_outline_node", "delete_outline_node",
        "create_worldbuilding_entry", "update_worldbuilding_entry", "delete_worldbuilding_entry",
        "create_relationship", "update_relationship", "delete_relationship",
        "remember", "forget",
        "update_cataloging_candidate", "apply_pending_cataloging",
        "set_cataloging_mode", "set_daily_word_goal",
        "apply_external_story_updates",
        "archive_chapter_after_write", "repair_story_granularity",
        "apply_novel_blueprint",
        "submit_novel_creation_stage",
        "save_external_cataloging_facts",
        "save_external_cataloging_candidates",
        "write_project_file", "sync_project_files",
    }

    _MANAGEMENT_TOOLS = {
        "create_project", "update_project_info", "delete_project",
        "import_text_as_chapters", "import_file_as_chapters", "import_file_as_project",
        "import_deconstruct_report", "export_project",
        "create_scheduled_task", "update_scheduled_task", "delete_scheduled_task",
        "run_scheduled_task_now",
        "create_skill", "update_skill", "delete_skill", "reset_skill", "ensure_builtin_skills",
        "start_cataloging_job", "start_deconstruct_job",
        "resume_cataloging_job", "retry_current_cataloging_chapter",
        "rerun_cataloging_resolution_current", "rerun_failed_deconstruct_chunks",
        "cancel_cataloging_job", "pause_cataloging_job",
        "set_cataloging_mode",
    }

    _DESTRUCTIVE_TOOLS = {
        "delete_project", "delete_chapter", "delete_character",
        "delete_outline_node", "delete_worldbuilding_entry",
        "delete_relationship", "delete_scheduled_task", "delete_skill",
        "merge_duplicate_characters",
    }

    _HIGH_RISK_TOOLS = {
        "start_cataloging_job", "start_deconstruct_job",
        "resume_cataloging_job", "retry_current_cataloging_chapter",
        "rerun_cataloging_resolution_current", "rerun_failed_deconstruct_chunks",
        "run_scheduled_task_now", "cancel_cataloging_job",
    }

    _INTERNAL_LLM_TOOLS = {
        # Internal generation.
        "chapter_writer", "character_writer", "outline_writer", "worldbuilding_writer",
        "rewrite_text", "expand_text", "continue_text",
        "roleplay_character", "dialogue_battle", "draft_skill",
        # Internal analysis/evaluation that calls the configured model.
        "suggest_conflicts", "design_plot", "evaluate_chapter",
        "detect_character_changes", "detect_new_worldbuilding",
        "detect_worldbuilding_conflicts",
        # Long-running internal model jobs.
        "start_cataloging_job", "start_deconstruct_job",
        "resume_cataloging_job", "retry_current_cataloging_chapter",
        "rerun_cataloging_resolution_current", "rerun_failed_deconstruct_chunks",
    }

    _READ_TAGS = {"read", "search"}
    _ANALYSIS_TAGS = {"read", "analysis"}
    _GENERATOR_TAGS = {"generator", "draft"}
    _WRITE_TAGS = {"write", "create"}
    _DELETE_TAGS = {"write", "delete"}
    _MGMT_TAGS = {"write", "management"}
    _TELEMETRY_TAGS = {"read", "telemetry"}

    for name in registry.all_names():
        td = registry.get(name)
        if not td:
            continue
        if td.permission_tags:
            continue

        tags: set[str] = set()
        risk = "safe"
        writes = False

        if td.tool_type in ("read", "analysis", "web"):
            tags = _ANALYSIS_TAGS if td.tool_type == "analysis" else _READ_TAGS
            risk = "safe"
        elif td.tool_type == "generator":
            tags = _GENERATOR_TAGS
            risk = "low"
        elif td.tool_type == "memory":
            if name == "remember":
                tags = {"memory", "write"}
                writes = True
                risk = "low"
            elif name == "forget":
                tags = {"memory", "delete"}
                writes = True
                risk = "medium"
            else:
                tags = {"memory", "read"}
        elif td.tool_type == "scheduler":
            tags = _MGMT_TAGS
            risk = "medium"
            writes = True
        elif td.tool_type == "write":
            if name in _DESTRUCTIVE_TOOLS:
                tags = _DELETE_TAGS
                risk = "destructive"
                writes = True
            elif name in _MANAGEMENT_TOOLS:
                tags = _MGMT_TAGS
                risk = "high" if name in _HIGH_RISK_TOOLS else "medium"
                writes = True
            elif name in _WRITE_PROJECT_DATA:
                tags = _WRITE_TAGS
                risk = "medium"
                writes = True
            else:
                tags = _MGMT_TAGS
                risk = "low"

        if name in _INTERNAL_LLM_TOOLS or td.tool_type == "generator":
            tags = {"internal_llm", "model"}
            if td.tool_type == "generator":
                tags.add("generator")
            if td.tool_type == "analysis":
                tags.add("analysis")
            if td.tool_type == "write":
                tags.update({"write", "management"})
                writes = True
            risk = "high" if name in _HIGH_RISK_TOOLS or td.tool_type == "write" else "medium"

        # External agent reporting tools
        if name in ("start_agent_run", "finish_agent_run", "append_draft_chunk", "mark_draft_ready") or name.startswith("report_"):
            tags = _TELEMETRY_TAGS
            risk = "safe"
            writes = False

        # Derive MCP permission pack
        if name in _INTERNAL_LLM_TOOLS or td.tool_type == "generator":
            pack = "internal_llm"
        elif name in _DESTRUCTIVE_TOOLS:
            pack = "trusted_local_maintenance"
        elif name in _MANAGEMENT_TOOLS:
            pack = "project_management"
        elif td.tool_type in ("read", "analysis", "web"):
            pack = "readonly_collaboration"
        elif td.tool_type == "memory":
            pack = "readonly_collaboration" if not writes else "project_writing"
        elif td.tool_type == "generator":
            pack = "internal_llm"
        elif td.tool_type == "scheduler":
            pack = "project_management"
        elif writes:
            pack = "project_writing"
        else:
            pack = "project_management"

        # External agent reporting tools
        if name in ("start_agent_run", "finish_agent_run", "append_draft_chunk", "mark_draft_ready") or name.startswith("report_"):
            pack = "readonly_collaboration"

        object.__setattr__(td, 'permission_tags', tags)
        object.__setattr__(td, 'risk_level', risk)
        object.__setattr__(td, 'writes_project_data', writes)
        object.__setattr__(td, 'mcp_permission_pack', pack)


_classify_all()
