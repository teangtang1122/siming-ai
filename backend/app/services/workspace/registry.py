"""Central tool registry for workspace assistant.

Single source of truth for tool metadata, schemas, and handler bindings.
Adding a new tool requires only one change: register a ToolDef here.
"""

from __future__ import annotations

from ...architecture.tool_definition import ToolDef, ToolHandler
from ...architecture.tool_permissions import classify_tool_definitions
from ...architecture.tool_spec import ToolSpec
from ...modules.assistant.application.tool_catalog import build_domain_tool_specs
from ...modules.assistant.interfaces.tool_definitions import (
    TOOL_DEFINITIONS as ASSISTANT_TOOL_DEFINITIONS,
)
from ...modules.context.interfaces.tool_definitions import (
    TOOL_DEFINITIONS as CONTEXT_TOOL_DEFINITIONS,
)
from ...modules.continuity.interfaces.tool_definitions import (
    TOOL_DEFINITIONS as CONTINUITY_TOOL_DEFINITIONS,
)
from ...modules.creation.interfaces.tool_definitions import (
    TOOL_DEFINITIONS as CREATION_TOOL_DEFINITIONS,
)
from ...modules.integrations.interfaces.tool_definitions import (
    TOOL_DEFINITIONS as INTEGRATIONS_TOOL_DEFINITIONS,
)
from ...modules.model_runtime.interfaces.tool_definitions import (
    TOOL_DEFINITIONS as MODEL_RUNTIME_TOOL_DEFINITIONS,
)
from ...modules.operations.interfaces.tool_definitions import (
    TOOL_DEFINITIONS as OPERATIONS_TOOL_DEFINITIONS,
)
from ...modules.story.interfaces.tool_definitions import (
    TOOL_DEFINITIONS as STORY_TOOL_DEFINITIONS,
)
from .dynamic_modules import LEGACY_HANDLER_MODULES
from .spec_registry import ToolSpecRegistryMixin

# ---------------------------------------------------------------------------
# ToolDef — metadata for a single tool
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ToolRegistry — manages all registered tools
# ---------------------------------------------------------------------------


class ToolRegistry(ToolSpecRegistryMixin):
    """Central registry for workspace tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}
        self._specs: dict[str, ToolSpec] = {}
        self._aliases: dict[str, str] = {}

    def register(self, tool_def: ToolDef) -> None:
        self._tools[tool_def.name] = tool_def
        self._specs[tool_def.name] = self._legacy_spec(tool_def)

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(self._aliases.get(name, name))

    def get_handler(self, name: str) -> ToolHandler | None:
        td = self.get(name)
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
            spec = self._specs[td.name]
            result.append(spec.openai_schema())
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
                td for name, td in self._tools.items() if name in allowed_names and td.expose_to_mcp
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
            metadata = self._specs[td.name].frontend_metadata()
            metadata["mcp_permission_pack"] = self._derive_mcp_pack(td)
            result.append(metadata)
        return result


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
registry = ToolRegistry()


# ---------------------------------------------------------------------------
# Register all tools
# ---------------------------------------------------------------------------

_TOOL_REGISTRATION_ORDER = (
    "list_projects",
    "get_project_info",
    "get_project_files_info",
    "list_project_files",
    "read_project_file",
    "search_project_files",
    "write_project_file",
    "sync_project_files",
    "create_project",
    "update_project_info",
    "delete_project",
    "export_project",
    "get_export_word_count",
    "preview_import_splits",
    "import_text_as_chapters",
    "import_file_as_chapters",
    "import_file_as_project",
    "start_cataloging_job",
    "list_cataloging_jobs",
    "get_cataloging_job",
    "get_cataloging_control_state",
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
    "import_deconstruct_report",
    "get_today_writing_stats",
    "get_writing_stats_history",
    "set_daily_word_goal",
    "list_duplicate_characters",
    "preview_character_merge",
    "merge_duplicate_characters",
    "list_scheduled_tasks",
    "create_scheduled_task",
    "update_scheduled_task",
    "delete_scheduled_task",
    "run_scheduled_task_now",
    "list_skills",
    "list_skill_templates",
    "list_skill_tools",
    "draft_skill",
    "create_skill",
    "update_skill",
    "delete_skill",
    "reset_skill",
    "preview_skill_match",
    "list_skill_versions",
    "ensure_builtin_skills",
    "search_characters",
    "search_chapters",
    "search_outline",
    "search_outline_tree",
    "search_worldbuilding",
    "search_relationships",
    "list_characters",
    "list_chapters",
    "list_worldbuilding",
    "create_worldbuilding_entry",
    "update_worldbuilding_entry",
    "delete_worldbuilding_entry",
    "create_outline_node",
    "create_outline_nodes",
    "update_outline_node",
    "delete_outline_node",
    "create_character",
    "update_character",
    "delete_character",
    "create_relationship",
    "update_relationship",
    "delete_relationship",
    "create_chapter",
    "update_chapter",
    "list_chapter_versions",
    "restore_chapter_version",
    "diff_chapter_versions",
    "delete_chapter",
    "suggest_conflicts",
    "design_plot",
    "detect_character_changes",
    "detect_new_worldbuilding",
    "detect_worldbuilding_conflicts",
    "detect_forbidden_patterns",
    "preview_writing_context",
    "search_context",
    "preview_rag_context",
    "explain_context_selection",
    "evaluate_chapter",
    "prepare_task_context",
    "search_task_context",
    "submit_context_evidence",
    "chapter_writer",
    "character_writer",
    "outline_writer",
    "worldbuilding_writer",
    "rewrite_text",
    "expand_text",
    "continue_text",
    "roleplay_character",
    "dialogue_battle",
    "web_search",
    "remember",
    "recall",
    "forget",
    "list_memories",
    "start_agent_run",
    "report_agent_plan",
    "report_agent_progress",
    "report_context_selected",
    "append_draft_chunk",
    "mark_draft_ready",
    "finish_agent_run",
    "start_local_cli_agent_run",
    "wait_local_cli_agent_run",
    "prepare_external_writing_context",
    "save_external_chapter_draft",
    "get_external_chapter_draft",
    "record_external_quality_review",
    "apply_external_story_updates",
    "archive_chapter_after_write",
    "update_narrative_ledger_entry",
    "get_narrative_ledger",
    "get_narrative_governance",
    "apply_narrative_governance_candidates",
    "list_narrative_checkpoints",
    "diff_narrative_checkpoint",
    "restore_narrative_governance_checkpoint",
    "inspect_story_granularity",
    "repair_story_granularity",
    "start_novel_creation_session",
    "draft_novel_blueprint",
    "review_novel_blueprint",
    "apply_novel_blueprint",
    "get_novel_creation_session",
    "generate_novel_creation_stage",
    "submit_novel_creation_stage",
    "list_imported_files",
    "read_imported_file",
    "get_mcp_permission_status",
    "start_external_cataloging_job",
    "get_next_external_cataloging_chapter",
    "save_external_cataloging_facts",
    "save_external_cataloging_candidates",
    "verify_external_cataloging_progress",
    "get_project_archive_status",
    "get_moshu_usage_guide",
    "list_prompt_packs",
    "get_prompt_pack",
    "get_tool_playbook",
    "get_quality_rubric",
)


def _handler_resolver():
    from importlib import import_module

    modules = [import_module(name) for name in LEGACY_HANDLER_MODULES]

    def resolve(name: str):
        for module in modules:
            handler = getattr(module, name, None)
            if callable(handler):
                return handler
        raise KeyError(f"Workspace tool handler is not registered: {name}")

    return resolve


def _register_all() -> None:
    resolve_handler = _handler_resolver()
    definitions = [
        *ASSISTANT_TOOL_DEFINITIONS,
        *CONTEXT_TOOL_DEFINITIONS,
        *CONTINUITY_TOOL_DEFINITIONS,
        *CREATION_TOOL_DEFINITIONS,
        *INTEGRATIONS_TOOL_DEFINITIONS,
        *MODEL_RUNTIME_TOOL_DEFINITIONS,
        *OPERATIONS_TOOL_DEFINITIONS,
        *STORY_TOOL_DEFINITIONS,
    ]
    order = {name: index for index, name in enumerate(_TOOL_REGISTRATION_ORDER)}
    for definition in sorted(definitions, key=lambda item: order[item.name]):
        registry.register(definition.bind(resolve_handler))


_register_all()
classify_tool_definitions(registry)

# Typed domain specs replace selected legacy projections after permission
# classification. Every unmigrated tool keeps an exact legacy JSON schema.
registry.rebuild_legacy_specs()
registry.bind_specs(
    build_domain_tool_specs({name: registry.get(name) for name in registry.all_names()})
)
