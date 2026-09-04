"""Central tool registry for workspace assistant.

Single source of truth for tool metadata, schemas, and handler bindings.
Adding a new tool requires only one change: register a ToolDef here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from ...architecture.tool_categories import (
    TOOL_CATEGORY_BY_NAME,
    normalize_tool_categories,
    tool_category_for_name,
)
from ...architecture.tool_definition import ToolDef, ToolHandler
from ...architecture.tool_permissions import classify_tool_definitions
from ...architecture.tool_result_policy import (
    ModelResultContract,
    ModelResultListProjection,
    ModelResultPolicy,
    ModelResultPreview,
)
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
from ...modules.creation.interfaces.agent_scope import CREATION_DIRECT_MCP_TOOL_NAMES
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
from ...modules.story.domain.outline_contract import OUTLINE_PROPOSAL_MAX_NODES
from ...modules.story.interfaces.tool_definitions import (
    TOOL_DEFINITIONS as STORY_TOOL_DEFINITIONS,
)
from .dynamic_modules import LEGACY_HANDLER_MODULES
from .spec_registry import ToolSpecRegistryMixin

# ---------------------------------------------------------------------------
# ToolDef — metadata for a single tool
# ---------------------------------------------------------------------------


_STATUS_RECEIPT_DATA_FIELDS = (
    "id",
    "revision",
    "current_revision",
    "project_id",
    "created_project_id",
    "chapter_id",
    "outline_node_id",
    "character_id",
    "worldbuilding_id",
    "relationship_id",
    "session_id",
    "artifact",
    "artifact_id",
    "entity_id",
    "operation_id",
    "run_id",
    "job_id",
    "report_id",
    "import_id",
    "file_id",
    "candidate_id",
    "fact_id",
    "task_id",
    "version_id",
    "checkpoint_id",
    "manifest_id",
    "context_manifest_id",
    "draft_id",
    "content_ref",
    "status",
    "current_stage",
    "saved_outline_node_ids",
    "chapter_outline_node_ids",
    "next_actions",
)

_STATUS_ONLY_CONTRACT = ModelResultContract(
    policy=ModelResultPolicy.STATUS_ONLY,
    max_json_bytes=4 * 1024,
    data_fields=_STATUS_RECEIPT_DATA_FIELDS,
)

_CATALOGING_LAUNCH_RECEIPT_CONTRACT = ModelResultContract(
    policy=ModelResultPolicy.STATUS_ONLY,
    max_json_bytes=8 * 1024,
    data_fields=(
        "id",
        "job_id",
        "project_id",
        "operation_id",
        "status",
        "execution_mode",
        "execution_backend",
        "total_chapters",
        "completed_chapters",
        "failed_chapters",
        "started",
        "worker_queued",
        "existing_worker",
        "trigger_source",
        "idempotent_reuse",
        "requested_chapter_ids",
        "already_cataloged_chapter_ids",
        "in_progress_chapter_ids",
        "queued_chapter_ids",
        "deferred_chapter_ids",
        "reused_job_ids",
        "superseded_job_ids",
        "next_action",
        "error",
        "review_warning",
    ),
)

_CHAPTER_DRAFT_RESULT_CONTRACT = ModelResultContract(
    policy=ModelResultPolicy.ARTIFACT_REFERENCE,
    max_json_bytes=16 * 1024,
    data_fields=(
        "draft_id",
        "project_id",
        "content_ref",
        "title",
        "outline_node_id",
        "saved_chapter_id",
        "draft_status",
        "next_actions",
        "word_count",
        "context_manifest_id",
        # A rejected short draft is not an artifact, but the model and author
        # still need the deterministic retry receipt.  Keep only the declared
        # counts and booleans; prose and tokens remain outside the projection.
        "reason_code",
        "actual_han_characters",
        "minimum_han_characters",
        "missing_han_characters",
        "draft_stored",
        "context_selection_token_consumed",
    ),
    reference_fields=("draft_id", "content_ref"),
    preview=ModelResultPreview(
        source_field="content",
        output_field="content_preview",
        max_chars=1_200,
    ),
)
_OUTLINE_DRAFT_RESULT_CONTRACT = ModelResultContract(
    policy=ModelResultPolicy.ARTIFACT_REFERENCE,
    max_json_bytes=16 * 1024,
    data_fields=(
        "draft_id",
        "project_id",
        "context_manifest_id",
        "parent_id",
        "insert_after_id",
        "draft_status",
        "design_notes",
        "saved_outline_node_ids",
        "chapter_outline_node_ids",
        "next_actions",
    ),
    reference_fields=("draft_id",),
    preview=ModelResultPreview(
        source_field="nodes",
        output_field="nodes_preview",
        item_fields=(
            "id",
            "parent_id",
            "node_type",
            "title",
            "summary",
            "parent_title",
            "actual_summary",
            "planned_summary",
            "character_names",
            "status",
        ),
        max_items=OUTLINE_PROPOSAL_MAX_NODES,
    ),
)

_MODEL_RESULT_CONTRACTS_BY_NAME: dict[str, ModelResultContract] = {
    # REST, native-agent and CLI/MCP callers must be able to distinguish a
    # newly queued job from an idempotently reused current-version result.
    # Hiding these launch fields makes repeated catalog clicks look like fresh
    # work on the CLI surface even though the shared launcher did the right
    # thing underneath.
    "start_cataloging_job": _CATALOGING_LAUNCH_RECEIPT_CONTRACT,
    "start_external_cataloging_job": _CATALOGING_LAUNCH_RECEIPT_CONTRACT,
    # These generators persist their complete author-review product before
    # returning. The model receives the durable reference plus a declared
    # preview; the public projection uses the same declaration to build the
    # author-visible draft receipt without exposing arbitrary result fields.
    "chapter_writer": _CHAPTER_DRAFT_RESULT_CONTRACT,
    "save_external_chapter_draft": _CHAPTER_DRAFT_RESULT_CONTRACT,
    "outline_writer": _OUTLINE_DRAFT_RESULT_CONTRACT,
    "save_external_outline_draft": _OUTLINE_DRAFT_RESULT_CONTRACT,
    # Lightweight catalog tools already return only the listed identifiers and
    # labels.  This remains a complete first delivery, not a replay summary.
    "list_characters": ModelResultContract(
        policy=ModelResultPolicy.SUMMARY_AND_IDS,
        max_json_bytes=16 * 1024,
        result_fields=("page",),
        list_projections=(
            ModelResultListProjection(
                source_field=None,
                output_field=None,
                item_fields=("id", "name", "role_type"),
                max_items=10,
            ),
        ),
    ),
    "list_chapters": ModelResultContract(
        policy=ModelResultPolicy.SUMMARY_AND_IDS,
        max_json_bytes=16 * 1024,
        result_fields=("page",),
        list_projections=(
            ModelResultListProjection(
                source_field=None,
                output_field=None,
                item_fields=("id", "title", "outline_node_id"),
                max_items=10,
            ),
        ),
    ),
    "list_worldbuilding": ModelResultContract(
        policy=ModelResultPolicy.SUMMARY_AND_IDS,
        max_json_bytes=16 * 1024,
        result_fields=("page",),
        list_projections=(
            ModelResultListProjection(
                source_field=None,
                output_field=None,
                item_fields=("id", "title", "dimension"),
                max_items=10,
            ),
        ),
    ),
    # Search/read tools must deliver one complete result.  Their handlers own
    # query limits/ranges; the projector never rewrites a fresh search result.
    "search_characters": ModelResultContract(max_json_bytes=16 * 1024),
    "search_chapters": ModelResultContract(max_json_bytes=16 * 1024),
    "search_outline": ModelResultContract(max_json_bytes=16 * 1024),
    "search_outline_tree": ModelResultContract(max_json_bytes=16 * 1024),
    "search_worldbuilding": ModelResultContract(max_json_bytes=16 * 1024),
    "search_relationships": ModelResultContract(max_json_bytes=16 * 1024),
    "search_project_files": ModelResultContract(max_json_bytes=16 * 1024),
    "read_project_file": ModelResultContract(max_json_bytes=32 * 1024),
    "search_context": ModelResultContract(max_json_bytes=16 * 1024),
    "prepare_task_context": ModelResultContract(max_json_bytes=32 * 1024),
    # The external wrapper delivers the same lossless 20 KiB context pages as
    # prepare_task_context, plus target and MCP workflow metadata. Keeping the
    # default 16 KiB read contract makes a valid Chinese page fail projection
    # once a real project has enough current context.
    "prepare_external_writing_context": ModelResultContract(max_json_bytes=32 * 1024),
    # Twelve 600-character evidence previews plus stable IDs/hashes fit this
    # single-call envelope. Two maximum pages must be requested in separate
    # model steps so the native result batch remains bounded.
    "search_task_context": ModelResultContract(max_json_bytes=32 * 1024),
    "submit_context_evidence": ModelResultContract(
        policy=ModelResultPolicy.STATUS_ONLY,
        # The success receipt includes the first lossless context page.  Its
        # page body may use 20 KiB, so reserve a complete 24 KiB envelope.
        max_json_bytes=24 * 1024,
        data_fields=(
            "manifest_id",
            "accepted_count",
            "selection_ready",
            "context_selection_token",
            "context_delivery_ready",
            "context_delivery",
            "estimated_input_tokens",
            "input_budget_tokens",
            "soft_target_tokens",
            "soft_target_exceeded",
            "warnings",
            "context_page", "next_tool", "next_arguments",
            "validation_errors", "validation_error_count", "validation_errors_has_more",
        ),
    ),
    "save_external_cataloging_facts": ModelResultContract(
        policy=ModelResultPolicy.STATUS_ONLY,
        max_json_bytes=8 * 1024,
        data_fields=(
            "job_id", "project_id", "chapter_id", "facts_saved", "chapter_run_status",
            "candidate_generation_allowed", "candidate_gate_note", "blocking_run",
            "validation_errors", "validation_error_count", "validation_errors_has_more",
            "allowed_fact_types", "next_tool", "next_arguments",
        ),
    ),
    "save_external_cataloging_candidates": ModelResultContract(
        policy=ModelResultPolicy.STATUS_ONLY,
        max_json_bytes=16 * 1024,
        data_fields=(
            "job_id", "project_id", "chapter_id", "candidates_saved", "duplicates_skipped",
            "candidates_total", "candidate_set_complete", "missing_required_items",
            "chapter_run_status", "auto_applied", "apply_status", "coverage",
            "candidate_generation_allowed", "blocking_run", "next_tool", "next_arguments",
            "validation_errors", "validation_error_count", "validation_errors_has_more",
        ),
    ),
    "list_imported_files": ModelResultContract(max_json_bytes=16 * 1024),
    "read_imported_file": ModelResultContract(max_json_bytes=32 * 1024),
}


def _model_result_contract_for(tool_def: ToolDef) -> ModelResultContract:
    explicit = _MODEL_RESULT_CONTRACTS_BY_NAME.get(tool_def.name)
    if explicit is not None:
        return explicit
    if tool_def.tool_type in {"write", "scheduler"}:
        if tool_def.name == "create_outline_nodes":
            return replace(
                _STATUS_ONLY_CONTRACT,
                max_json_bytes=12 * 1024,
                list_projections=(
                    ModelResultListProjection(
                        source_field="nodes",
                        output_field="nodes",
                        item_fields=("id", "parent_id", "node_type", "title", "status"),
                        max_items=OUTLINE_PROPOSAL_MAX_NODES,
                    ),
                ),
            )
        return _STATUS_ONLY_CONTRACT
    if tool_def.name in {"remember", "forget"}:
        return _STATUS_ONLY_CONTRACT
    return tool_def.model_result_contract


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

    def get_model_result_contract(self, name: str) -> ModelResultContract | None:
        td = self.get(name)
        return td.model_result_contract if td else None

    def all_names(self) -> list[str]:
        return list(self._tools.keys())

    def get_schemas(
        self,
        *,
        tool_types: set[str] | None = None,
        exclude_types: set[str] | None = None,
        categories: Iterable[str] | None = None,
    ) -> list[dict]:
        """Return OpenAI function-calling format dicts, optionally filtered by type."""
        selected_categories = (
            set(normalize_tool_categories(list(categories))) if categories is not None else None
        )
        result: list[dict] = []
        for td in self._tools.values():
            if selected_categories is not None and td.agent_category not in selected_categories:
                continue
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
        categories: Iterable[str] | None = None,
    ) -> list[ToolDef]:
        """Return tools available to the internal project assistant."""
        selected_categories = (
            set(normalize_tool_categories(list(categories))) if categories is not None else None
        )
        result = []
        for td in self._tools.values():
            if not td.expose_to_internal_agent:
                continue
            if selected_categories is not None and td.agent_category not in selected_categories:
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
        categories: Iterable[str] | None = None,
    ) -> list[ToolDef]:
        """Return tools available to MCP clients for a given permission pack."""
        selected_categories = (
            set(normalize_tool_categories(list(categories))) if categories is not None else None
        )

        def category_allowed(tool: ToolDef) -> bool:
            return selected_categories is None or tool.agent_category in selected_categories

        if permission_pack == "chapter_drafting":
            # A chapter-writing CLI may read project context and persist one
            # unsaved draft. It cannot write official chapters, derived
            # archives, or query/start cataloging in the same model turn.
            allowed_names = {
                name
                for name, tool in self._tools.items()
                if tool.tool_type == "read" and "cataloging" not in name
            } | {
                "prepare_external_writing_context",
                "save_external_chapter_draft",
                "save_external_outline_draft",
                "report_agent_progress",
                "report_context_selected",
            }
            return [
                tool
                for name, tool in self._tools.items()
                if name in allowed_names and tool.expose_to_mcp and category_allowed(tool)
            ]

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
            }
            return [
                td
                for name, td in self._tools.items()
                if name in allowed_names and td.expose_to_mcp and category_allowed(td)
            ]

        if permission_pack == "creation_session":
            # The current CLI writes its own structured result. Model-spawning
            # creation tools are deliberately absent so MCP cannot recursively
            # launch another CLI while the first one waits.
            return [
                td
                for name, td in self._tools.items()
                if name in CREATION_DIRECT_MCP_TOOL_NAMES
                and td.expose_to_mcp
                and category_allowed(td)
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
            if not category_allowed(td):
                continue
            pack = self._derive_mcp_pack(td)
            if pack in allowed_packs:
                result.append(td)
        return result

    def list_for_workspace_direct_mcp(self) -> list[ToolDef]:
        """Return the transaction-safe subset for one managed workspace turn."""

        return [
            definition
            for definition in self.list_for_mcp(permission_pack="project_management")
            if definition.direct_mcp_project_scoped
            and definition.direct_mcp_transactional
        ]

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
    "get_project_creation_brief",
    "get_project_files_info",
    "list_project_files",
    "read_project_file",
    "search_project_files",
    "write_project_file",
    "sync_project_files",
    "create_project",
    "update_project_info",
    "update_project_creation_brief",
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
    "save_external_outline_draft",
    "get_external_chapter_draft",
    "record_external_quality_review",
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
    "get_creation_session",
    "get_creation_snapshot",
    "get_creation_operation",
    "get_creation_artifact",
    "list_creation_artifacts",
    "get_creation_dependencies",
    "get_creation_dependency_graph",
    "validate_creation_consistency",
    "patch_creation_artifact",
    "patch_creation_session",
    "lock_creation_fields",
    "unlock_creation_fields",
    "undo_creation_artifact",
    "list_creation_entities",
    "get_creation_entity",
    "patch_creation_entity",
    "delete_creation_entity",
    "list_creation_artifact_versions",
    "get_creation_artifact_diff",
    "restore_creation_artifact_version",
    "confirm_creation_artifact",
    "generate_creation_artifact",
    "refine_creation_artifact",
    "regenerate_creation_artifact",
    "cancel_creation_operation",
    "pause_creation_operation",
    "resume_creation_operation",
    "retry_creation_operation",
    "validate_creation_session",
    "finalize_creation_session",
    "import_creation_material",
    "preview_creation_import",
    "apply_creation_import",
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
        categorized = replace(
            definition,
            agent_category=tool_category_for_name(definition.name),
            model_result_contract=_model_result_contract_for(definition),
        )
        registry.register(categorized.bind(resolve_handler))

    registered_names = set(registry.all_names())
    catalog_names = set(TOOL_CATEGORY_BY_NAME)
    if registered_names != catalog_names:
        missing = sorted(registered_names - catalog_names)
        extra = sorted(catalog_names - registered_names)
        raise RuntimeError(
            f"Agent 工具类别目录与注册表不一致；未分类={missing}，无对应工具={extra}"
        )


_register_all()
classify_tool_definitions(registry)

# Typed domain specs replace selected legacy projections after permission
# classification. Every unmigrated tool keeps an exact legacy JSON schema.
registry.rebuild_legacy_specs()
registry.bind_specs(
    build_domain_tool_specs({name: registry.get(name) for name in registry.all_names()})
)
