"""External cataloging tools — API-free tools for external agents to catalog imported chapters.

These tools work without any Siming model API configured. They allow
Claude Code / Codex to extract characters, worldbuilding, outline,
and chapter summaries from imported text.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session
from app.core.legacy_env import compatible_env_prefixes
from app.database.models import (
    CatalogingCandidate,
    CatalogingChapterRun,
    CatalogingFact,
    CatalogingJob,
    Chapter,
    Character,
    CharacterRelationship,
    OutlineNode,
    Project,
    PublicPromptPack,
    WorldbuildingEntry,
)
from app.database.query_filters import current_worldbuilding_clause
from app.modules.story.application.content_sync import ensure_chapter_mirror
from app.modules.continuity.domain.cataloging_contract import (
    CATALOGING_FACT_TYPES,
    CHAPTER_LINK_REPLACE_LIST_FIELDS,
)
from app.prompts.cataloging_source import get_outline_granularity_rules
from app.services.cataloging.launcher import create_and_queue_cataloging_job

logger = logging.getLogger(__name__)


COMPLETED_RUN_STATUSES = {"completed", "completed_with_warnings"}
CANONICAL_FACT_TYPES = frozenset(CATALOGING_FACT_TYPES)
_COVERAGE_MANIFEST_LIST_FIELDS = (
    "characters",
    "worldbuilding",
    "relationships",
    "character_profiles",
)
_FACT_OVERVIEW_SCOPE_FIELDS = (
    "cataloging_characters",
    "anonymous_participants",
    "cataloging_worldbuilding_titles",
    "incidental_worldbuilding_mentions",
)
_CHARACTER_ARCHIVE_IDENTITIES = frozenset({
    "stable_character",
    "anonymous_role",
    "mention_only",
})
_WORLDBUILDING_ARCHIVE_IDENTITIES = frozenset({
    "stable_setting",
    "mention_only",
})


def _normalized_fact_identity(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _validate_identity_list(
    payload: dict[str, Any],
    field: str,
    *,
    fact_index: int,
) -> tuple[list[str], list[str]]:
    value = payload.get(field)
    if field not in payload:
        return [], [f"facts[{fact_index}].payload.{field} is required (use [] when empty)"]
    if not isinstance(value, list):
        return [], [f"facts[{fact_index}].payload.{field} must be an array of non-empty strings"]

    result: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    for item_index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(
                f"facts[{fact_index}].payload.{field}[{item_index}] must be a non-empty string"
            )
            continue
        display = " ".join(item.split())
        identity = _normalized_fact_identity(display)
        if identity in seen:
            errors.append(
                f"facts[{fact_index}].payload.{field} contains duplicate identity: {display}"
            )
            continue
        seen.add(identity)
        result.append(display)
    return result, errors


def _first_fact_identity(payload: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return " ".join(item.split())
    return ""


def _format_identity_set(values: set[str]) -> str:
    return ", ".join(sorted(values))


def _candidate_payload(candidate: CatalogingCandidate) -> dict[str, Any]:
    try:
        payload = json.loads(candidate.edited_payload or candidate.raw_payload or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _source_overview_scene_count(
    db: Session,
    chapter_run: CatalogingChapterRun,
) -> int | None:
    counts: list[int] = []
    overview_rows = db.query(CatalogingFact).filter(
        CatalogingFact.chapter_run_id == chapter_run.id,
        CatalogingFact.fact_type == "chapter_overview",
        CatalogingFact.status == "active",
    ).all()
    for overview in overview_rows:
        try:
            overview_payload = json.loads(overview.raw_payload or "{}")
        except (TypeError, ValueError):
            continue
        scenes = overview_payload.get("scenes") if isinstance(overview_payload, dict) else None
        if isinstance(scenes, list):
            counts.append(len(scenes))
    return max(counts) if counts else None


def _managed_cataloging_bindings() -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = []
    for prefix in compatible_env_prefixes():
        managed_kind = os.environ.get(f"{prefix}_MANAGED_AGENT_KIND", "")
        if managed_kind.strip().lower() != "cataloging":
            continue
        bindings.append({
            "project_id": os.environ.get(f"{prefix}_MANAGED_CATALOGING_PROJECT_ID", "").strip(),
            "job_id": os.environ.get(f"{prefix}_MANAGED_CATALOGING_JOB_ID", "").strip(),
            "chapter_id": os.environ.get(f"{prefix}_MANAGED_CATALOGING_CHAPTER_ID", "").strip(),
            "chapter_run_id": os.environ.get(f"{prefix}_MANAGED_CATALOGING_CHAPTER_RUN_ID", "").strip(),
            "stage": os.environ.get(f"{prefix}_MANAGED_CATALOGING_STAGE", "").strip(),
        })
    return bindings


def _managed_cataloging_binding(
    *,
    project_id: str = "",
    job_id: str = "",
    chapter_id: str = "",
) -> dict[str, str] | None:
    bindings = _managed_cataloging_bindings()
    if not (project_id or job_id or chapter_id):
        return bindings[0] if bindings else None
    for binding in bindings:
        if project_id and binding["project_id"] and binding["project_id"] != project_id:
            continue
        if job_id and binding["job_id"] and binding["job_id"] != job_id:
            continue
        if chapter_id and binding["chapter_id"] and binding["chapter_id"] != chapter_id:
            continue
        return binding
    return None


def _managed_binding_error(
    *,
    project_id: str,
    job_id: str,
    chapter_id: str = "",
) -> str | None:
    bindings = _managed_cataloging_bindings()
    if not bindings:
        return None
    binding = _managed_cataloging_binding(
        project_id=project_id,
        job_id=job_id,
        chapter_id=chapter_id,
    ) or bindings[0]
    if binding["project_id"] and binding["project_id"] != project_id:
        return "Managed cataloging turn is bound to a different project"
    if binding["job_id"] and binding["job_id"] != job_id:
        return "Managed cataloging turn is bound to a different job"
    if chapter_id and binding["chapter_id"] and binding["chapter_id"] != chapter_id:
        return "Managed cataloging turn may only write its bound chapter"
    return None


def _managed_stop_result(job_id: str, project_id: str, detail: str) -> dict[str, Any]:
    return {
        "tool": "get_next_external_cataloging_chapter",
        "status": "skipped",
        "detail": detail,
        "data": {
            "job_id": job_id,
            "project_id": project_id,
            "managed_turn_complete": True,
            "next_tool": None,
            "workflow_reminder": {
                "mode": "managed_single_chapter",
                "note": "This CLI turn handles exactly one chapter. End the turn now; Siming will start a fresh turn for the next chapter.",
            },
        },
    }


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _validated_external_fact_type(raw_type: Any) -> str:
    fact_type = str(raw_type or "").strip()
    return fact_type if fact_type in CANONICAL_FACT_TYPES else ""


def _job_project_id(job: Any, provided_project_id: str) -> tuple[str, str | None]:
    effective_project_id = str(getattr(job, "project_id", "") or "").strip()
    provided = str(provided_project_id or "").strip()
    if provided and effective_project_id and provided != effective_project_id:
        return effective_project_id, (
            f"project_id mismatch: provided {provided}, but job {getattr(job, 'id', '')} belongs to {effective_project_id}"
        )
    return effective_project_id or provided, None


def _workflow_reminder(next_tool: str, *, note: str = "") -> dict[str, Any]:
    """Return a compact workflow reminder for long-context external agents."""
    return {
        "mode": "external_cataloging_no_api",
        "phase_policy": {
            "facts": "Read exactly one chapter and save canonical facts first.",
            "candidates": "Resolve that chapter's saved facts against the current archive, then save one complete candidate batch.",
            "apply": "Apply and verify the current chapter before fetching the next chapter.",
            "why": "Candidates merge into cumulative character, outline, and worldbuilding cards. Later chapters must see earlier applied cards to avoid scrambled backgrounds and duplicate entities.",
        },
        "language_rule": (
            "Use the novel/source language for archive data. For Chinese novels, "
            "save Chinese names, titles, summaries, facts, candidates, aliases, "
            "outline nodes, and worldbuilding. Do not translate to English unless the user explicitly asks."
        ),
        "outline_granularity_policy": get_outline_granularity_rules(),
        "no_api_rule": (
            "When the user says Siming API is unavailable, do not call internal LLM tools such as "
            "start_cataloging_job, chapter_writer, character_writer, outline_writer, worldbuilding_writer, "
            "design_plot, or evaluate_chapter."
        ),
        "standard_flow": [
            "get_moshu_usage_guide(scenario='cataloging_no_api', no_api=true)",
            "get_prompt_pack(pack_id='cataloging_external_no_api')",
            "start_external_cataloging_job",
            "For each chapter in order: get_next_external_cataloging_chapter(phase='facts') -> save_external_cataloging_facts -> get_next_external_cataloging_chapter(phase='candidates') -> list_cataloging_facts -> save_external_cataloging_candidates -> apply_pending_cataloging -> verify_external_cataloging_progress",
            "Finish with get_project_archive_status and verify counts before reporting completion",
        ],
        "next_tool": next_tool,
        "note": note,
    }


def _run_summary(run: Any | None) -> dict[str, Any] | None:
    if not run:
        return None
    return {
        "chapter_run_id": getattr(run, "id", None),
        "chapter_id": getattr(run, "chapter_id", None),
        "chapter_order": getattr(run, "chapter_order", None),
        "status": getattr(run, "status", None),
    }


def _earliest_unfinished_run(db: Session, job_id: str) -> Any | None:
    return (
        db.query(CatalogingChapterRun)
        .filter(CatalogingChapterRun.job_id == job_id)
        .filter(CatalogingChapterRun.status.notin_(list(COMPLETED_RUN_STATUSES)))
        .order_by(CatalogingChapterRun.chapter_order.asc())
        .first()
    )


def _previous_unfinished_run(db: Session, run: Any) -> Any | None:
    return (
        db.query(CatalogingChapterRun)
        .filter(CatalogingChapterRun.job_id == run.job_id)
        .filter(CatalogingChapterRun.chapter_order < run.chapter_order)
        .filter(CatalogingChapterRun.status.notin_(list(COMPLETED_RUN_STATUSES)))
        .order_by(CatalogingChapterRun.chapter_order.asc())
        .first()
    )


def _next_candidate_run(db: Session, job_id: str) -> Any | None:
    """Return the earliest run whose candidates may be generated now."""
    first = _earliest_unfinished_run(db, job_id)
    if first and first.status == "facts_saved":
        return first
    return None


def _candidate_gate(db: Session, run: Any) -> tuple[bool, dict[str, Any] | None, str]:
    previous = _previous_unfinished_run(db, run)
    if previous:
        return (
            False,
            _run_summary(previous),
            "A previous chapter has not been applied. Candidate generation must follow chapter_order.",
        )
    if run.status == "awaiting_confirmation":
        return (
            False,
            _run_summary(run),
            "This chapter already has staged candidates. Call apply_pending_cataloging before generating more candidates.",
        )
    if run.status in COMPLETED_RUN_STATUSES:
        return (
            False,
            _run_summary(run),
            "This chapter is already applied. Do not generate duplicate candidates.",
        )
    if run.status != "facts_saved":
        return (
            False,
            _run_summary(run),
            "Save facts for this chapter before generating candidates.",
        )
    return True, None, "This chapter is the current sequential candidate turn."


async def start_external_cataloging_job(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Create a cataloging job for external agent mode.

    API-free: creates a CatalogingJob and CatalogingChapterRun per chapter.
    Does not call LLMGateway.
    """
    if not str(project_id or "").strip():
        return {
            "tool": "start_external_cataloging_job",
            "status": "skipped",
            "detail": "project_id is required to start an external cataloging job",
            "data": None,
        }

    chapter_ids = [
        str(item)
        for item in (args.get("chapter_ids") or [])
        if str(item or "").strip()
    ]
    chapter_query = db.query(Chapter).filter(Chapter.project_id == project_id)
    if chapter_ids:
        chapter_query = chapter_query.filter(Chapter.id.in_(chapter_ids))
    chapter_count = chapter_query.count()
    if not chapter_count:
        return {
            "tool": "start_external_cataloging_job",
            "status": "skipped",
            "detail": "No chapters found for this project",
            "data": None,
        }

    job, launch = create_and_queue_cataloging_job(
        db,
        project_id,
        chapter_ids,
        execution_mode="auto",
        backend_override="external_agent",
        provider_override="external_agent",
        trigger_source="external_agent",
        run_now=False,
    )
    idempotent = bool(launch.get("idempotent_reuse"))

    return {
        "tool": "start_external_cataloging_job",
        "status": "ok",
        "detail": (
            f"Current chapter versions already have a cataloging job; reused {job.id}"
            if idempotent
            else f"Job created with {job.total_chapters or chapter_count} chapters"
        ),
        "data": {
            "job_id": job.id,
            "chapter_count": chapter_count,
            "status": job.status,
            "chapter_versions_recorded": True,
            "idempotent_reuse": idempotent,
            "launch": launch,
            "next_tool": "get_prompt_pack",
            "workflow_reminder": _workflow_reminder(
                "get_prompt_pack",
                note=(
                    "Read the cataloging_external_no_api prompt pack before cataloging. "
                    "Process each chapter through facts, candidates, apply, and verification in chapter_order."
                ),
            ),
        },
    }


async def get_next_external_cataloging_chapter(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Get the next pending chapter for external cataloging.

    API-free: returns chapter text, context, and prompt pack.
    """
    from app.services.prompt_packs.seed import ensure_builtin_packs

    ensure_builtin_packs(db)

    job_id = str(args.get("job_id") or "").strip()
    phase = str(args.get("phase") or "facts").strip().lower()
    include_content = bool(args.get("include_content", True))
    include_prompt_pack = bool(args.get("include_prompt_pack", True))
    include_context_indexes = bool(args.get("include_context_indexes", True))
    if phase not in {"facts", "candidates"}:
        return {
            "tool": "get_next_external_cataloging_chapter",
            "status": "skipped",
            "detail": "phase must be 'facts' or 'candidates'",
            "data": None,
        }
    if not job_id:
        return {
            "tool": "get_next_external_cataloging_chapter",
            "status": "skipped",
            "detail": "job_id is required",
            "data": None,
        }

    job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
    if not job:
        return {
            "tool": "get_next_external_cataloging_chapter",
            "status": "skipped",
            "detail": "Job not found",
            "data": None,
        }
    effective_project_id, mismatch = _job_project_id(job, project_id)
    if mismatch:
        return {
            "tool": "get_next_external_cataloging_chapter",
            "status": "skipped",
            "detail": mismatch,
            "data": None,
        }
    binding_error = _managed_binding_error(
        project_id=effective_project_id,
        job_id=job_id,
    )
    if binding_error:
        return _managed_stop_result(job_id, effective_project_id, binding_error)

    managed_binding = _managed_cataloging_binding(
        project_id=effective_project_id,
        job_id=job_id,
    )
    managed_run = None
    if managed_binding and managed_binding["chapter_run_id"]:
        managed_run = db.query(CatalogingChapterRun).filter(
            CatalogingChapterRun.id == managed_binding["chapter_run_id"],
            CatalogingChapterRun.job_id == job_id,
        ).first()
        if not managed_run:
            return _managed_stop_result(
                job_id,
                effective_project_id,
                "Managed cataloging chapter run no longer exists",
            )
        if managed_run.status in COMPLETED_RUN_STATUSES | {"skipped_by_user"}:
            return _managed_stop_result(
                job_id,
                effective_project_id,
                "The bound chapter is complete. Do not fetch or process another chapter in this CLI turn.",
            )

    if phase == "candidates":
        awaiting_run = db.query(CatalogingChapterRun).filter(
            CatalogingChapterRun.job_id == job_id,
            CatalogingChapterRun.status == "awaiting_confirmation",
        ).order_by(CatalogingChapterRun.chapter_order).first()
        if awaiting_run:
            return {
                "tool": "get_next_external_cataloging_chapter",
                "status": "ok",
                "detail": "A chapter already has staged candidates and must be applied before continuing",
                "data": {
                    "job_id": job_id,
                    "project_id": effective_project_id,
                    "phase": "candidates",
                    "chapter_id": awaiting_run.chapter_id,
                    "chapter_index": awaiting_run.chapter_order,
                    "all_done": False,
                    "waiting_for_apply": True,
                    "next_tool": "apply_pending_cataloging",
                    "workflow_reminder": _workflow_reminder(
                        "apply_pending_cataloging",
                        note="Apply the current chapter's candidates before generating candidates for any later chapter.",
                    ),
                },
            }

        candidate_run = _next_candidate_run(db, job_id)
        if not candidate_run:
            first_unfinished = _earliest_unfinished_run(db, job_id)
            if first_unfinished:
                return {
                    "tool": "get_next_external_cataloging_chapter",
                    "status": "ok",
                    "detail": "No chapter is ready for candidate generation yet",
                    "data": {
                        "job_id": job_id,
                        "project_id": effective_project_id,
                        "phase": "candidates",
                        "all_done": False,
                        "waiting_for_facts": True,
                        "blocking_run": _run_summary(first_unfinished),
                        "next_tool": "get_next_external_cataloging_chapter",
                        "next_arguments": {"job_id": job_id, "phase": "facts"},
                        "workflow_reminder": _workflow_reminder(
                            "get_next_external_cataloging_chapter",
                            note="Finish saving facts for the earliest unfinished chapter before generating candidates.",
                        ),
                    },
                }
            return {
                "tool": "get_next_external_cataloging_chapter",
                "status": "ok",
                "detail": "No more chapters to process",
                "data": {
                    "job_id": job_id,
                    "project_id": effective_project_id,
                    "phase": "candidates",
                    "all_done": True,
                    "next_tool": "get_project_archive_status",
                    "workflow_reminder": _workflow_reminder(
                        "get_project_archive_status",
                        note="Verify archive counts before reporting the cataloging job complete.",
                    ),
                },
            }

        chapter = db.query(Chapter).filter(Chapter.id == candidate_run.chapter_id).first()
        if not chapter:
            return {
                "tool": "get_next_external_cataloging_chapter",
                "status": "skipped",
                "detail": "Chapter not found",
                "data": None,
            }
        chapter_run = candidate_run
    else:
        chapter_run = _earliest_unfinished_run(db, job_id)
        if chapter_run and chapter_run.status == "facts_saved":
            return {
                "tool": "get_next_external_cataloging_chapter",
                "status": "ok",
                "detail": "The current chapter's facts are saved; resolve its candidates before continuing",
                "data": {
                    "job_id": job_id,
                    "project_id": effective_project_id,
                    "phase": "facts",
                    "all_done": False,
                    "next_candidate_run": _run_summary(chapter_run),
                    "next_tool": "get_next_external_cataloging_chapter",
                    "next_arguments": {"job_id": job_id, "phase": "candidates"},
                    "workflow_reminder": _workflow_reminder(
                        "get_next_external_cataloging_chapter",
                        note="Resolve and apply this chapter before fetching facts for the next chapter.",
                    ),
                },
            }
        if chapter_run and chapter_run.status == "awaiting_confirmation":
            return {
                "tool": "get_next_external_cataloging_chapter",
                "status": "ok",
                "detail": "The current chapter's candidates must be applied before continuing",
                "data": {
                    "job_id": job_id,
                    "project_id": effective_project_id,
                    "phase": "facts",
                    "chapter_id": chapter_run.chapter_id,
                    "chapter_index": chapter_run.chapter_order,
                    "all_done": False,
                    "waiting_for_apply": True,
                    "next_tool": "apply_pending_cataloging",
                    "workflow_reminder": _workflow_reminder(
                        "apply_pending_cataloging",
                        note="Apply and verify this chapter before fetching the next chapter.",
                    ),
                },
            }
        if not chapter_run:
            return {
                "tool": "get_next_external_cataloging_chapter",
                "status": "ok",
                "detail": "No more chapters to process",
                "data": {
                    "job_id": job_id,
                    "project_id": effective_project_id,
                    "phase": "facts",
                    "all_done": True,
                    "next_tool": "get_project_archive_status",
                    "workflow_reminder": _workflow_reminder(
                        "get_project_archive_status",
                        note="Verify archive counts before reporting the cataloging job complete.",
                    ),
                },
            }
        if chapter_run.status not in {"pending", "in_progress", "extracting"}:
            return {
                "tool": "get_next_external_cataloging_chapter",
                "status": "skipped",
                "detail": "The earliest unfinished chapter must be retried or resolved before continuing",
                "data": {
                    "job_id": job_id,
                    "project_id": effective_project_id,
                    "blocking_run": _run_summary(chapter_run),
                    "next_tool": "retry_current_cataloging_chapter",
                },
            }

        chapter = db.query(Chapter).filter(Chapter.id == chapter_run.chapter_id).first()
        if not chapter:
            return {
                "tool": "get_next_external_cataloging_chapter",
                "status": "skipped",
                "detail": "Chapter not found",
                "data": None,
            }

    if managed_binding:
        if managed_binding["chapter_run_id"] and chapter_run.id != managed_binding["chapter_run_id"]:
            return _managed_stop_result(
                job_id,
                effective_project_id,
                "The next available chapter belongs to another CLI turn. End this turn now.",
            )
        if managed_binding["chapter_id"] and chapter_run.chapter_id != managed_binding["chapter_id"]:
            return _managed_stop_result(
                job_id,
                effective_project_id,
                "The next available chapter is not the chapter bound to this CLI turn.",
            )

    char_index: dict[str, str] = {}
    wb_index: dict[str, str] = {}
    worldbuilding_identity_review_required: list[dict[str, str]] = []
    outline_neighborhood: list[dict[str, Any]] = []
    if include_context_indexes:
        characters = db.query(Character).filter(
            Character.project_id == effective_project_id,
        ).all()
        char_index = {c.name: c.id for c in characters}
        for c in characters:
            if hasattr(c, "aliases") and c.aliases:
                for alias in c.aliases:
                    alias_name = getattr(alias, "alias", None) or getattr(alias, "alias_name", None)
                    if alias_name:
                        char_index[alias_name] = c.id

        wb_entries = db.query(WorldbuildingEntry).filter(
            WorldbuildingEntry.project_id == effective_project_id,
            current_worldbuilding_clause(WorldbuildingEntry.status),
        ).all()
        wb_index = {e.title: e.id for e in wb_entries}
        if phase == "candidates":
            from app.services.cataloging.fact_store import load_facts_for_run
            from app.services.cataloging.targeted_context import (
                worldbuilding_identity_review_candidates,
            )

            worldbuilding_identity_review_required = [
                {
                    "id": entry.id,
                    "title": entry.title,
                    "dimension": entry.dimension,
                }
                for entry in worldbuilding_identity_review_candidates(
                    db,
                    effective_project_id,
                    load_facts_for_run(db, chapter_run),
                )
            ]

        outline_nodes = db.query(OutlineNode).filter(
            OutlineNode.project_id == effective_project_id,
        ).order_by(OutlineNode.sort_order).limit(20).all()
        outline_neighborhood = [
            {"id": n.id, "title": n.title, "node_type": n.node_type, "parent_id": n.parent_id}
            for n in outline_nodes
        ]

    pack = db.query(PublicPromptPack).filter(
        PublicPromptPack.pack_id == "cataloging_external_no_api",
        PublicPromptPack.enabled == True,
    ).first()

    prompt_pack_data = None
    if pack and include_prompt_pack:
        prompt_pack_data = {
            "pack_id": pack.pack_id,
            "version": pack.version,
            "system_prompt": pack.system_prompt,
            "workflow": pack.workflow_json,
        }

    if phase == "facts":
        chapter_run.status = "in_progress"
        job.status = "running"
        job.current_chapter_id = chapter.id
        commit_session(db)

    project = db.query(Project).filter(Project.id == effective_project_id).first()
    project_folder = ""
    content_file_path = ""
    if project:
        folder, file_path = ensure_chapter_mirror(
            db,
            project,
            chapter,
            index=chapter_run.chapter_order + 1,
            source="external_cataloging",
        )
        project_folder = str(folder)
        content_file_path = str(file_path)

    return {
        "tool": "get_next_external_cataloging_chapter",
        "status": "ok",
        "detail": f"Chapter: {chapter.title}",
        "data": {
            "job_id": job_id,
            "project_id": effective_project_id,
            "phase": phase,
            "chapter_id": chapter.id,
            "chapter_run_id": chapter_run.id,
            "chapter_index": chapter_run.chapter_order,
            "title": chapter.title,
            "content": chapter.content if include_content else None,
            "content_included": include_content,
            "context_indexes_included": include_context_indexes,
            "content_file_path": content_file_path,
            "project_folder": project_folder,
            "character_alias_index": char_index,
            "worldbuilding_title_index": wb_index,
            "worldbuilding_identity_review_required": worldbuilding_identity_review_required,
            "outline_neighborhood": outline_neighborhood,
            "outline_granularity_policy": get_outline_granularity_rules(),
            "prompt_pack": prompt_pack_data,
            "next_tool": "save_external_cataloging_facts" if phase == "facts" else "save_external_cataloging_candidates",
            "workflow_reminder": _workflow_reminder(
                "save_external_cataloging_facts" if phase == "facts" else "save_external_cataloging_candidates",
                note=(
                    "Read this chapter with the prompt pack, then save extracted facts in the source language."
                    if phase == "facts"
                    else "Resolve this chapter's saved facts against the current archive; do not skip ahead."
                ),
            ),
        },
    }


def _validate_external_fact_records(
    facts: Any,
) -> tuple[list[tuple[str, dict[str, Any], dict[str, Any], int]], list[str]]:
    validated: list[tuple[str, dict[str, Any], dict[str, Any], int]] = []
    errors: list[str] = []
    if not isinstance(facts, list):
        return [], ["facts must be a JSON array of objects, not a JSON-encoded string"]
    for index, fact_data in enumerate(facts):
        if not isinstance(fact_data, dict):
            errors.append(f"facts[{index}] must be an object")
            continue
        fact_type = _validated_external_fact_type(fact_data.get("fact_type"))
        payload = fact_data.get("payload")
        if not fact_type:
            errors.append(
                f"facts[{index}].fact_type must be one of {sorted(CANONICAL_FACT_TYPES)}"
            )
        if not isinstance(payload, dict):
            errors.append(f"facts[{index}].payload must be an object")
        if fact_type and isinstance(payload, dict):
            validated.append((fact_type, payload, fact_data, index))
    overview_records = [
        (payload, index)
        for fact_type, payload, _fact_data, index in validated
        if fact_type == "chapter_overview"
    ]
    if not overview_records:
        errors.append("facts must include one chapter_overview record")
    elif len(overview_records) > 1:
        errors.append(
            f"facts must include exactly one chapter_overview record; received {len(overview_records)}"
        )
    overview_scopes: dict[str, set[str]] = {}
    for payload, index in overview_records:
        scenes = payload.get("scenes")
        if isinstance(scenes, list) and len(scenes) > 6:
            errors.append(
                f"facts[{index}].payload.scenes must contain at most 6 grouped story "
                f"scenes; received {len(scenes)}"
            )
        for field in _FACT_OVERVIEW_SCOPE_FIELDS:
            values, field_errors = _validate_identity_list(
                payload,
                field,
                fact_index=index,
            )
            errors.extend(field_errors)
            overview_scopes[field] = {
                _normalized_fact_identity(value) for value in values
            }

    stable_characters: dict[str, tuple[str, int]] = {}
    non_archival_characters: dict[str, tuple[str, int]] = {}
    stable_worldbuilding: dict[str, tuple[str, int]] = {}
    incidental_worldbuilding: dict[str, tuple[str, int]] = {}
    relationship_pairs: dict[tuple[str, str], int] = {}

    for fact_type, payload, _fact_data, index in validated:
        if fact_type == "character_fact":
            archive_identity = str(payload.get("archive_identity") or "").strip()
            if archive_identity not in _CHARACTER_ARCHIVE_IDENTITIES:
                errors.append(
                    f"facts[{index}].payload.archive_identity must be one of "
                    f"{sorted(_CHARACTER_ARCHIVE_IDENTITIES)}"
                )
                continue
            if not isinstance(payload.get("stable_profile_change"), bool):
                errors.append(
                    f"facts[{index}].payload.stable_profile_change must be a boolean"
                )
            name = _first_fact_identity(
                payload,
                ("primary_name", "character_name", "name", "names"),
            )
            if not name:
                errors.append(
                    f"facts[{index}].payload must identify the character with primary_name, "
                    "character_name, name, or names"
                )
                continue
            normalized = _normalized_fact_identity(name)
            target = (
                stable_characters
                if archive_identity == "stable_character"
                else non_archival_characters
            )
            other = (
                non_archival_characters
                if archive_identity == "stable_character"
                else stable_characters
            )
            if normalized in target:
                errors.append(
                    f"facts[{index}] duplicates character_fact identity: {name}"
                )
            elif normalized in other:
                errors.append(
                    f"facts[{index}] conflicts with another character_fact archive_identity: {name}"
                )
            else:
                target[normalized] = (name, index)

        elif fact_type == "worldbuilding_fact":
            archive_identity = str(payload.get("archive_identity") or "").strip()
            if archive_identity not in _WORLDBUILDING_ARCHIVE_IDENTITIES:
                errors.append(
                    f"facts[{index}].payload.archive_identity must be one of "
                    f"{sorted(_WORLDBUILDING_ARCHIVE_IDENTITIES)}"
                )
                continue
            if not isinstance(payload.get("stable_setting_change"), bool):
                errors.append(
                    f"facts[{index}].payload.stable_setting_change must be a boolean"
                )
            title = _first_fact_identity(
                payload,
                ("canonical_title_hint", "title_hint", "title", "entry_title"),
            )
            if not title:
                errors.append(
                    f"facts[{index}].payload must identify the setting with "
                    "canonical_title_hint or title_hint"
                )
                continue
            normalized = _normalized_fact_identity(title)
            target = (
                stable_worldbuilding
                if archive_identity == "stable_setting"
                else incidental_worldbuilding
            )
            other = (
                incidental_worldbuilding
                if archive_identity == "stable_setting"
                else stable_worldbuilding
            )
            if normalized in target:
                errors.append(
                    f"facts[{index}] duplicates worldbuilding_fact identity: {title}"
                )
            elif normalized in other:
                errors.append(
                    f"facts[{index}] conflicts with another worldbuilding_fact archive_identity: {title}"
                )
            else:
                target[normalized] = (title, index)

        elif fact_type == "relationship_fact":
            source_name = _first_fact_identity(payload, ("source_name",))
            target_name = _first_fact_identity(payload, ("target_name",))
            relationship_type = _first_fact_identity(payload, ("relationship_type",))
            if not source_name or not target_name or not relationship_type:
                errors.append(
                    f"facts[{index}].payload relationship_fact requires non-empty "
                    "source_name, target_name, and relationship_type"
                )
                continue
            pair = (
                _normalized_fact_identity(source_name),
                _normalized_fact_identity(target_name),
            )
            if pair in relationship_pairs:
                errors.append(
                    f"facts[{index}] duplicates directed relationship_fact pair: "
                    f"{source_name} -> {target_name}"
                )
            else:
                relationship_pairs[pair] = index

    if overview_scopes:
        expected_scope_pairs = (
            ("cataloging_characters", stable_characters),
            ("anonymous_participants", non_archival_characters),
            ("cataloging_worldbuilding_titles", stable_worldbuilding),
            ("incidental_worldbuilding_mentions", incidental_worldbuilding),
        )
        for field, fact_identities in expected_scope_pairs:
            overview_identities = overview_scopes.get(field, set())
            fact_identity_set = set(fact_identities)
            missing = fact_identity_set - overview_identities
            extra = overview_identities - fact_identity_set
            if missing:
                names = {fact_identities[item][0] for item in missing}
                errors.append(
                    f"chapter_overview.payload.{field} is missing fact identities: "
                    f"{_format_identity_set(names)}"
                )
            if extra:
                errors.append(
                    f"chapter_overview.payload.{field} has identities without matching facts: "
                    f"{_format_identity_set(extra)}"
                )

        stable_scope = overview_scopes.get("cataloging_characters", set())
        for (source_name, target_name), index in relationship_pairs.items():
            missing_endpoints = {
                name for name in (source_name, target_name) if name not in stable_scope
            }
            if missing_endpoints:
                errors.append(
                    f"facts[{index}].payload relationship endpoints must be stable_character "
                    "facts listed in chapter_overview.payload.cataloging_characters; missing: "
                    f"{_format_identity_set(missing_endpoints)}"
                )
    return validated, errors


def _persist_external_fact_records(
    db: Session,
    job: CatalogingJob,
    chapter_run: CatalogingChapterRun,
    chapter_id: str,
    records: list[tuple[str, dict[str, Any], dict[str, Any], int]],
) -> int:
    for fact_type, payload, fact_data, index in records:
        db.add(CatalogingFact(
            job_id=job.id,
            chapter_run_id=chapter_run.id,
            project_id=job.project_id,
            chapter_id=chapter_id,
            fact_type=fact_type,
            raw_payload=json.dumps(payload, ensure_ascii=False),
            confidence=_float_or_none(fact_data.get("confidence")),
            evidence=str(fact_data.get("evidence") or "")[:2000] or None,
            sort_order=index,
        ))
    chapter_run.status = "facts_saved"
    if job.status == "waiting_confirmation":
        job.status = "running"
    job.updated_at = datetime.utcnow()
    commit_session(db)
    return len(records)


def _external_facts_skip(detail: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "tool": "save_external_cataloging_facts",
        "status": "skipped",
        "detail": detail,
        "data": data,
    }


async def save_external_cataloging_facts(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Save facts extracted by the external model.

    API-free: stores facts in CatalogingFact table.
    """
    job_id = str(args.get("job_id") or "").strip()
    chapter_id = str(args.get("chapter_id") or "").strip()
    facts = args.get("facts", [])

    if not job_id or not chapter_id:
        return _external_facts_skip("job_id and chapter_id are required")

    job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
    if not job:
        return _external_facts_skip("Job not found")
    effective_project_id, mismatch = _job_project_id(job, project_id)
    if mismatch:
        return _external_facts_skip(mismatch)
    binding_error = _managed_binding_error(
        project_id=effective_project_id,
        job_id=job_id,
        chapter_id=chapter_id,
    )
    if binding_error:
        return _external_facts_skip(binding_error)

    # Find the chapter run
    chapter_run = db.query(CatalogingChapterRun).filter(
        CatalogingChapterRun.job_id == job_id,
        CatalogingChapterRun.chapter_id == chapter_id,
    ).first()

    if not chapter_run:
        return _external_facts_skip("Chapter run not found")

    earliest_run = _earliest_unfinished_run(db, job_id)
    if earliest_run and earliest_run.id != chapter_run.id:
        return {
            "tool": "save_external_cataloging_facts",
            "status": "skipped",
            "detail": "Facts must be saved for the earliest unfinished chapter",
            "data": {
                "job_id": job_id,
                "project_id": effective_project_id,
                "chapter_id": chapter_id,
                "blocking_run": _run_summary(earliest_run),
            },
        }
    if chapter_run.status not in {"pending", "in_progress", "extracting"}:
        return {
            "tool": "save_external_cataloging_facts",
            "status": "skipped",
            "detail": "This chapter is not in the facts stage",
            "data": {
                "job_id": job_id,
                "project_id": effective_project_id,
                "chapter_id": chapter_id,
                "chapter_run_status": chapter_run.status,
                "next_tool": (
                    "get_next_external_cataloging_chapter"
                    if chapter_run.status == "facts_saved"
                    else "verify_external_cataloging_progress"
                ),
            },
        }

    validated_facts, validation_errors = _validate_external_fact_records(facts)
    if validation_errors:
        return {
            "tool": "save_external_cataloging_facts",
            "status": "skipped",
            "detail": "Facts do not match the canonical facts contract",
            "data": {
                "job_id": job_id,
                "project_id": effective_project_id,
                "chapter_id": chapter_id,
                "validation_errors": validation_errors[:12],
                "validation_error_count": len(validation_errors),
                "validation_errors_has_more": len(validation_errors) > 12,
                "allowed_fact_types": sorted(CANONICAL_FACT_TYPES),
                "next_tool": "save_external_cataloging_facts",
            },
        }

    # Standalone MCP jobs do not pass through the managed CLI launcher. Their
    # first fact write must establish the same chapter-local archive baseline.
    chapter_run.started_at = chapter_run.started_at or datetime.utcnow()
    saved = _persist_external_fact_records(
        db,
        job,
        chapter_run,
        chapter_id,
        validated_facts,
    )

    allowed, blocking_run, gate_note = _candidate_gate(db, chapter_run)
    if allowed:
        next_tool = "save_external_cataloging_candidates"
        next_arguments = None
        note = (
            "This chapter is now the current sequential candidate turn. "
            "Convert the saved facts into concrete write candidates in the source language."
        )
    else:
        next_tool = "get_next_external_cataloging_chapter"
        next_arguments = {"job_id": job_id, "phase": "candidates"}
        note = (
            "Facts saved. Do not generate candidates for this chapter yet unless it is the earliest unapplied chapter. "
            "Use get_next_external_cataloging_chapter with phase='candidates' to get the allowed chapter."
        )

    return {
        "tool": "save_external_cataloging_facts",
        "status": "ok",
        "detail": f"Saved {saved} facts",
        "data": {
            "job_id": job_id,
            "project_id": effective_project_id,
            "chapter_id": chapter_id,
            "facts_saved": saved,
            "chapter_run_status": chapter_run.status,
            "candidate_generation_allowed": allowed,
            "candidate_gate_note": gate_note,
            "blocking_run": blocking_run,
            "next_tool": next_tool,
            "next_arguments": next_arguments,
            "workflow_reminder": _workflow_reminder(
                next_tool,
                note=note,
            ),
        },
    }


async def save_external_cataloging_candidates(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Save candidates proposed by the external model.

    API-free: stores candidates in CatalogingCandidate table.
    """
    from app.services.cataloging.candidate_store import (
        create_candidate_from_raw,
    )
    from app.services.cataloging.candidate_validation import inspect_candidate_coverage
    from app.services.cataloging.jsonl import expand_candidate_records

    job_id = str(args.get("job_id") or "").strip()
    chapter_id = str(args.get("chapter_id") or "").strip()
    candidates = args.get("candidates", [])

    if not job_id or not chapter_id:
        return {
            "tool": "save_external_cataloging_candidates",
            "status": "skipped",
            "detail": "job_id and chapter_id are required",
            "data": None,
        }

    job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
    if not job:
        return {
            "tool": "save_external_cataloging_candidates",
            "status": "skipped",
            "detail": "Job not found",
            "data": None,
        }
    effective_project_id, mismatch = _job_project_id(job, project_id)
    if mismatch:
        return {
            "tool": "save_external_cataloging_candidates",
            "status": "skipped",
            "detail": mismatch,
            "data": None,
        }
    binding_error = _managed_binding_error(
        project_id=effective_project_id,
        job_id=job_id,
        chapter_id=chapter_id,
    )
    if binding_error:
        return {
            "tool": "save_external_cataloging_candidates",
            "status": "skipped",
            "detail": binding_error,
            "data": None,
        }

    chapter_run = db.query(CatalogingChapterRun).filter(
        CatalogingChapterRun.job_id == job_id,
        CatalogingChapterRun.chapter_id == chapter_id,
    ).first()

    if not chapter_run:
        return {
            "tool": "save_external_cataloging_candidates",
            "status": "skipped",
            "detail": "Chapter run not found",
            "data": None,
        }

    managed_binding = _managed_cataloging_binding(
        project_id=effective_project_id,
        job_id=job_id,
        chapter_id=chapter_id,
    )
    allowed, blocking_run, gate_note = _candidate_gate(db, chapter_run)
    if not allowed:
        if chapter_run.status == "awaiting_confirmation":
            next_tool = "apply_pending_cataloging"
            next_arguments = {"job_id": job_id}
        elif blocking_run and blocking_run.get("status") == "facts_saved":
            next_tool = "get_next_external_cataloging_chapter"
            next_arguments = {"job_id": job_id, "phase": "candidates"}
        elif blocking_run and blocking_run.get("status") == "awaiting_confirmation":
            next_tool = "apply_pending_cataloging"
            next_arguments = {"job_id": job_id}
        else:
            next_tool = "get_next_external_cataloging_chapter"
            next_arguments = {"job_id": job_id, "phase": "facts"}
        return {
            "tool": "save_external_cataloging_candidates",
            "status": "skipped",
            "detail": gate_note,
            "data": {
                "job_id": job_id,
                "project_id": effective_project_id,
                "chapter_id": chapter_id,
                "chapter_run_status": chapter_run.status,
                "candidate_generation_allowed": False,
                "blocking_run": blocking_run,
                "next_tool": next_tool,
                "next_arguments": next_arguments,
                "workflow_reminder": _workflow_reminder(
                    next_tool,
                    note=(
                        "Candidate generation is serialized. Process and apply the earliest chapter first, "
                        "then ask for phase='candidates' again."
                    ),
                ),
            },
        }

    validation_errors = (
        ["candidates must be a JSON array of objects, not a JSON-encoded string"]
        if not isinstance(candidates, list)
        else [f"candidates[{index}] must be an object"
              for index, item in enumerate(candidates) if not isinstance(item, dict)]
    )
    expanded_candidates: list[dict[str, Any]] = []
    if not validation_errors:
        from app.modules.continuity.domain.cataloging_contract import (
            validate_coverage_manifest_relationships,
        )
        from app.services.cataloging.character_targets import (
            validate_character_profile_target,
            validate_character_state_target,
        )
        from app.services.cataloging.candidate_validation import (
            validate_candidate_source_character_grounding,
        )
        from app.services.cataloging.jsonl import normalize_candidate

        for index, item in enumerate(candidates):
            for record in expand_candidate_records(item):
                expanded_candidates.append(record)
                try:
                    normalized = normalize_candidate(record)
                    validate_coverage_manifest_relationships(normalized["payload"])
                    validate_character_profile_target(
                        db, effective_project_id, normalized["item_type"], normalized["payload"],
                    )
                    validate_character_state_target(
                        db,
                        effective_project_id,
                        normalized["item_type"],
                        normalized["payload"],
                        chapter_content=str(chapter_run.chapter.content or "")
                        if chapter_run.chapter is not None
                        else "",
                    )
                    validate_candidate_source_character_grounding(
                        db,
                        effective_project_id,
                        chapter_run,
                        normalized,
                    )
                except ValueError as exc:
                    validation_errors.append(f"candidates[{index}]: {exc}")
    managed_cli_job = bool(
        managed_binding and job.execution_backend == "local_cli_agent"
    )
    if managed_cli_job and not validation_errors:
        from app.services.cataloging.jsonl import normalize_candidate

        normalized_batch = [normalize_candidate(record) for record in expanded_candidates]
        if len(normalized_batch) > 3:
            validation_errors.append(
                "Siming-managed cataloging accepts at most 3 candidate records per call; "
                "submit only the next missing items"
            )

        existing_candidates = (
            db.query(CatalogingCandidate)
            .filter(CatalogingCandidate.chapter_run_id == chapter_run.id)
            .filter(CatalogingCandidate.status != "rejected")
            .all()
        )
        source_scene_count = _source_overview_scene_count(db, chapter_run)
        chapter = db.query(Chapter).filter(
            Chapter.id == chapter_id,
            Chapter.project_id == effective_project_id,
        ).first()
        if not existing_candidates:
            expected_outline_type = (
                "outline_update"
                if chapter is not None and chapter.outline_node_id
                else "outline_create"
            )
            batch_types = [str(item.get("item_type") or "") for item in normalized_batch]
            if batch_types != ["chapter_summary", expected_outline_type]:
                validation_errors.append(
                    "The first Siming-managed candidate call must contain exactly 2 records "
                    f"in order: chapter_summary, {expected_outline_type}"
                )
            elif expected_outline_type == "outline_update" and chapter is not None:
                outline_payload = normalized_batch[1].get("payload") or {}
                outline_id = str(
                    outline_payload.get("id")
                    or normalized_batch[1].get("target_id")
                    or ""
                ).strip()
                if outline_id != str(chapter.outline_node_id or ""):
                    validation_errors.append(
                        "The chapter outline already exists; outline_update must carry its "
                        f"exact id {chapter.outline_node_id}"
                    )

            if batch_types and batch_types[0] == "chapter_summary":
                summary_payload = normalized_batch[0].get("payload") or {}
                if str(summary_payload.get("coverage_manifest_mode") or "").strip():
                    validation_errors.append(
                        "coverage_manifest_mode is only valid when amending an already accepted "
                        "chapter_summary"
                    )
                summary_text = str(
                    summary_payload.get("summary_text")
                    or summary_payload.get("summary")
                    or ""
                )
                if len("".join(summary_text.split())) < 40:
                    validation_errors.append(
                        "chapter_summary must contain at least 40 non-whitespace characters"
                    )
                manifest = summary_payload.get("coverage_manifest")
                declared_scene_count = (
                    manifest.get("scene_count") if isinstance(manifest, dict) else None
                )
                if source_scene_count is not None and declared_scene_count != source_scene_count:
                    validation_errors.append(
                        "coverage_manifest.scene_count must equal chapter_overview.scenes "
                        f"exactly: facts={source_scene_count}, "
                        f"manifest={declared_scene_count}"
                    )
        else:
            # CatalogingCandidate has exactly one run-local summary and one
            # aggregate chapter link. Both may be amended idempotently after
            # a missing-items response, while the chapter outline cannot be
            # submitted twice.
            repeated_chapter_outlines = [
                item
                for item in normalized_batch
                if str(item.get("item_type") or "") in {"outline_create", "outline_update"}
                and str((item.get("payload") or {}).get("node_type") or "chapter")
                == "chapter"
            ]
            if repeated_chapter_outlines:
                validation_errors.append(
                    "the chapter-level outline was already accepted; subsequent calls may "
                    "contain only missing supplemental candidates, one chapter_summary "
                    "coverage_manifest amendment, or one aggregate chapter_link amendment"
                )

            summary_items = [
                item
                for item in normalized_batch
                if str(item.get("item_type") or "") == "chapter_summary"
            ]
            if len(summary_items) > 1:
                validation_errors.append(
                    "Submit at most one chapter_summary amendment per call"
                )
            for summary_item in summary_items:
                summary_payload = summary_item.get("payload") or {}
                manifest_mode = str(
                    summary_payload.get("coverage_manifest_mode") or ""
                ).strip().lower()
                if manifest_mode and manifest_mode != "replace":
                    validation_errors.append(
                        "coverage_manifest_mode must be exactly 'replace' when provided"
                    )
                    continue
                if manifest_mode != "replace":
                    continue
                if len(normalized_batch) != 1:
                    validation_errors.append(
                        "A coverage_manifest replacement call must contain only the one "
                        "chapter_summary amendment"
                    )
                manifest = summary_payload.get("coverage_manifest")
                if not isinstance(manifest, dict):
                    validation_errors.append(
                        "coverage_manifest replacement requires a complete coverage_manifest object"
                    )
                    continue
                required_fields = {"scene_count", *_COVERAGE_MANIFEST_LIST_FIELDS}
                missing_fields = sorted(required_fields - set(manifest))
                if missing_fields:
                    validation_errors.append(
                        "coverage_manifest replacement is missing required fields: "
                        + ", ".join(missing_fields)
                    )
                invalid_list_fields = [
                    field
                    for field in _COVERAGE_MANIFEST_LIST_FIELDS
                    if field in manifest and not isinstance(manifest.get(field), list)
                ]
                if invalid_list_fields:
                    validation_errors.append(
                        "coverage_manifest replacement fields must be arrays: "
                        + ", ".join(invalid_list_fields)
                    )
                replacement_scene_count = manifest.get("scene_count")
                if (
                    not isinstance(replacement_scene_count, int)
                    or isinstance(replacement_scene_count, bool)
                    or replacement_scene_count <= 0
                ):
                    validation_errors.append(
                        "coverage_manifest replacement scene_count must be a positive integer"
                    )
                existing_summary = next(
                    (
                        item
                        for item in existing_candidates
                        if item.item_type == "chapter_summary"
                    ),
                    None,
                )
                existing_manifest = (
                    _candidate_payload(existing_summary).get("coverage_manifest")
                    if existing_summary is not None
                    else None
                )
                existing_scene_count = (
                    existing_manifest.get("scene_count")
                    if isinstance(existing_manifest, dict)
                    else None
                )
                if existing_summary is None:
                    validation_errors.append(
                        "coverage_manifest replacement requires an already accepted chapter_summary"
                    )
                elif replacement_scene_count != existing_scene_count:
                    validation_errors.append(
                        "coverage_manifest replacement cannot change scene_count: "
                        f"existing={existing_scene_count}, replacement={replacement_scene_count}"
                    )
                if source_scene_count is None:
                    validation_errors.append(
                        "coverage_manifest replacement requires chapter_overview.scenes source facts"
                    )
                elif replacement_scene_count != source_scene_count:
                    validation_errors.append(
                        "coverage_manifest replacement scene_count must equal "
                        f"chapter_overview.scenes: facts={source_scene_count}, "
                        f"replacement={replacement_scene_count}"
                    )

        existing_link_count = sum(
            1 for item in existing_candidates if item.item_type == "chapter_link"
        )
        new_link_items = [
            item
            for item in normalized_batch
            if str(item.get("item_type") or "") == "chapter_link"
        ]
        if len(new_link_items) > 1:
            validation_errors.append(
                "Submit at most one aggregate chapter_link per call; a later call may "
                "idempotently add missing characters, worldbuilding, locations, items, "
                "and events to the same staged record"
            )
        for link_item in new_link_items:
            link_payload = link_item.get("payload") or {}
            link_mode = str(
                link_payload.get("chapter_link_mode") or ""
            ).strip().lower()
            if link_mode and link_mode != "replace":
                validation_errors.append(
                    "chapter_link_mode must be exactly 'replace' when provided"
                )
                continue
            if link_mode != "replace":
                continue
            if existing_link_count != 1:
                validation_errors.append(
                    "chapter_link replacement requires exactly one already accepted "
                    "aggregate chapter_link"
                )
            if len(normalized_batch) != 1:
                validation_errors.append(
                    "A chapter_link replacement call must contain only the one "
                    "chapter_link amendment"
                )
            missing_fields = sorted(
                set(CHAPTER_LINK_REPLACE_LIST_FIELDS) - set(link_payload)
            )
            if missing_fields:
                validation_errors.append(
                    "chapter_link replacement is missing required fields: "
                    + ", ".join(missing_fields)
                )
            invalid_list_fields = [
                field
                for field in CHAPTER_LINK_REPLACE_LIST_FIELDS
                if field in link_payload and not isinstance(link_payload.get(field), list)
            ]
            if invalid_list_fields:
                validation_errors.append(
                    "chapter_link replacement fields must be arrays: "
                    + ", ".join(invalid_list_fields)
                )
    if validation_errors:
        preview_limit = 3
        preview = "; ".join(validation_errors[:preview_limit])
        remaining = len(validation_errors) - preview_limit
        detail = (
            "Candidates do not match the canonical candidates contract: "
            + preview
            + (f"; and {remaining} more validation errors" if remaining > 0 else "")
        )
        return {
            "tool": "save_external_cataloging_candidates",
            "status": "skipped",
            "detail": detail,
            "data": {
                "job_id": job_id,
                "project_id": effective_project_id,
                "chapter_id": chapter_id,
                "validation_errors": validation_errors[:12],
                "validation_error_count": len(validation_errors),
                "validation_errors_has_more": len(validation_errors) > 12,
                "next_tool": "save_external_cataloging_candidates",
            },
        }

    saved = 0
    duplicates = 0
    warnings: list[str] = []
    existing_count = (
        db.query(CatalogingCandidate)
        .filter(CatalogingCandidate.chapter_run_id == chapter_run.id)
        .count()
    )
    for cand_data in candidates:
        for record in expand_candidate_records(cand_data):
            created = create_candidate_from_raw(
                db,
                job,
                chapter_run,
                record,
                existing_count + saved,
                source_task="external_agent",
            )
            if created.get("bad_line"):
                warnings.append(str(created.get("error") or "Unsupported candidate"))
                continue
            if created.get("skipped"):
                warnings.append(str(created.get("reason") or "Candidate skipped because it lacks usable content"))
                continue
            if created.get("duplicate"):
                duplicates += 1
                continue
            if created.get("candidate"):
                saved += 1

    db.flush()
    stored_candidates = (
        db.query(CatalogingCandidate)
        .filter(CatalogingCandidate.chapter_run_id == chapter_run.id)
        .all()
    )
    coverage = inspect_candidate_coverage(
        stored_candidates,
        db=db,
        project_id=project_id,
    )
    missing_required_items = list(coverage.cli_parity_missing)
    if managed_binding:
        # Managed automatic cataloging owns the complete facts-to-archive
        # transaction.  Source/manifest disagreements cannot be downgraded to
        # advisory warnings here, or a tiny placeholder summary and an empty
        # manifest can falsely mark a richly extracted chapter as complete.
        missing_required_items.extend(coverage.review_warnings)
    missing_required_items = list(dict.fromkeys(missing_required_items))
    candidate_set_complete = not missing_required_items
    managed_auto_job = (
        managed_binding
        and job.execution_mode == "auto"
        and job.execution_backend == "local_cli_agent"
    )
    auto_apply_inline = candidate_set_complete and managed_auto_job
    if candidate_set_complete:
        # Saved candidates still need to be applied to project data. Keep the
        # run blocking here so external agents cannot report completion before
        # writes are actually applied.
        chapter_run.status = "awaiting_confirmation"
        # A managed automatic job applies immediately below. Do not commit a
        # user-facing waiting state in the small transaction gap before that
        # call: HTTP clients and the CLI poller can otherwise mistake normal
        # automatic progress for a manual confirmation barrier.
        job.status = "running" if auto_apply_inline else "waiting_confirmation"
        job.blocked_chapter_id = None if auto_apply_inline else chapter_id
        next_tool = "apply_pending_cataloging"
        note = (
            "Candidates are only staged until apply_pending_cataloging writes them into "
            "characters, outline, worldbuilding, and summaries."
        )
    else:
        # A partial or empty call is not a completed candidate stage. Preserve
        # any valid rows so a fresh CLI turn can add the missing required data.
        chapter_run.status = "facts_saved"
        job.status = "running"
        # The managed automatic worker is actively accumulating another batch;
        # it is neither waiting for the author nor blocked.  Keep the public
        # blocking field reserved for real failures and manual confirmation.
        job.blocked_chapter_id = None if managed_auto_job else chapter_id
        missing_text = ", ".join(missing_required_items)
        warnings.append(
            f"Candidate set is incomplete; missing required items: {missing_text}"
        )
        next_tool = "save_external_cataloging_candidates"
        note = (
            "The candidate set is incomplete. Add the missing required items for this same "
            "chapter before calling apply_pending_cataloging."
        )
    job.updated_at = datetime.utcnow()
    commit_session(db)

    auto_applied = False
    apply_status = ""
    if auto_apply_inline:
        # A Siming-managed CLI turn is already running under a user-started
        # automatic cataloging job.  Apply at the transactional MCP boundary
        # instead of waiting for the model to remember a second tool call.  In
        # manual mode candidates remain staged for explicit user confirmation.
        from app.services.workspace.tools.cataloging import apply_pending_cataloging

        apply_result = await apply_pending_cataloging(
            db,
            effective_project_id,
            {"job_id": job_id},
        )
        apply_status = str(apply_result.get("status") or "")
        if apply_status == "ok":
            auto_applied = True
            next_tool = "verify_external_cataloging_progress"
            note = (
                "Automatic mode applied the complete candidate set transactionally. "
                "Verify once, then end this CLI turn without saving or applying again."
            )
        commit_session(db)

    no_effect_incomplete = not candidate_set_complete and saved == 0
    missing_preview = "；".join(missing_required_items[:3])
    missing_remainder = max(0, len(missing_required_items) - 3)
    incomplete_detail = (
        f"Saved {saved} candidates; candidate set is incomplete; missing: {missing_preview}"
        + (f"; and {missing_remainder} more" if missing_remainder else "")
    )
    result = {
        "tool": "save_external_cataloging_candidates",
        "status": "skipped" if no_effect_incomplete else "ok",
        "detail": (
            "No candidates were stored because the submitted batch was empty, invalid, "
            "or redundant; submit canonical candidate objects directly"
            if no_effect_incomplete
            else
            f"Saved {saved} candidates and applied them automatically"
            if auto_applied
            else f"Saved {saved} candidates"
            if candidate_set_complete
            else incomplete_detail
        ),
        "data": {
            "job_id": job_id,
            "project_id": effective_project_id,
            "chapter_id": chapter_id,
            "candidates_saved": saved,
            "duplicates_skipped": duplicates,
            "no_effect_reason": (
                "empty_or_unusable_candidate_batch" if no_effect_incomplete else None
            ),
            "candidates_total": coverage.total,
            "candidate_set_complete": candidate_set_complete,
            "missing_required_items": missing_required_items,
            "chapter_run_status": chapter_run.status,
            "auto_applied": auto_applied,
            "apply_status": apply_status or None,
            "coverage": coverage.to_dict(),
            "next_tool": next_tool,
            "workflow_reminder": _workflow_reminder(
                next_tool,
                note=note,
            ),
            "warnings": warnings,
        },
    }
    if warnings:
        result["warnings"] = warnings
    return result


async def verify_external_cataloging_progress(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Verify cataloging progress with counts and samples.

    API-free: reads from database.
    """
    job_id = str(args.get("job_id") or "").strip()
    if not job_id:
        return {
            "tool": "verify_external_cataloging_progress",
            "status": "skipped",
            "detail": "job_id is required",
            "data": None,
        }

    job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
    if not job:
        return {
            "tool": "verify_external_cataloging_progress",
            "status": "skipped",
            "detail": "Job not found",
            "data": None,
        }
    effective_project_id, mismatch = _job_project_id(job, project_id)
    if mismatch:
        return {
            "tool": "verify_external_cataloging_progress",
            "status": "skipped",
            "detail": mismatch,
            "data": None,
        }

    total_runs = db.query(CatalogingChapterRun).filter(
        CatalogingChapterRun.job_id == job_id,
    ).count()
    completed_runs = db.query(CatalogingChapterRun).filter(
        CatalogingChapterRun.job_id == job_id,
        CatalogingChapterRun.status.in_(["completed", "completed_with_warnings"]),
    ).count()
    failed_runs = db.query(CatalogingChapterRun).filter(
        CatalogingChapterRun.job_id == job_id,
        CatalogingChapterRun.status == "failed",
    ).count()
    pending_runs = db.query(CatalogingChapterRun).filter(
        CatalogingChapterRun.job_id == job_id,
        CatalogingChapterRun.status == "pending",
    ).count()
    in_progress_runs = db.query(CatalogingChapterRun).filter(
        CatalogingChapterRun.job_id == job_id,
        CatalogingChapterRun.status == "in_progress",
    ).count()
    facts_saved_runs = db.query(CatalogingChapterRun).filter(
        CatalogingChapterRun.job_id == job_id,
        CatalogingChapterRun.status == "facts_saved",
    ).count()
    awaiting_runs = db.query(CatalogingChapterRun).filter(
        CatalogingChapterRun.job_id == job_id,
        CatalogingChapterRun.status == "awaiting_confirmation",
    ).count()

    # Count project data
    chapters_count = db.query(Chapter).filter(Chapter.project_id == effective_project_id).count()
    characters_count = db.query(Character).filter(Character.project_id == effective_project_id).count()
    wb_count = db.query(WorldbuildingEntry).filter(
        WorldbuildingEntry.project_id == effective_project_id,
        current_worldbuilding_clause(WorldbuildingEntry.status),
    ).count()
    outline_count = db.query(OutlineNode).filter(OutlineNode.project_id == effective_project_id).count()
    chapter_outline_count = db.query(OutlineNode).filter(
        OutlineNode.project_id == effective_project_id,
        OutlineNode.node_type == "chapter",
    ).count()
    section_outline_count = db.query(OutlineNode).filter(
        OutlineNode.project_id == effective_project_id,
        OutlineNode.node_type == "section",
    ).count()
    rel_count = db.query(CharacterRelationship).filter(CharacterRelationship.project_id == effective_project_id).count()

    # Count pending candidates
    pending_candidates = db.query(CatalogingCandidate).filter(
        CatalogingCandidate.job_id == job_id,
        CatalogingCandidate.status == "pending",
    ).count()

    warnings = []
    if failed_runs > 0:
        warnings.append(f"{failed_runs} chapter runs failed")
    if characters_count == 0 and chapters_count > 0:
        warnings.append("No characters found despite having chapters")
    if outline_count == 0 and chapters_count > 0:
        warnings.append("No outline nodes found despite having chapters")
    if outline_count > 0 and chapters_count > 1 and section_outline_count == 0:
        warnings.append(
            "No section-level outline nodes found; external cataloging may be too coarse. "
            "Follow outline_granularity_policy and create section outline nodes for multi-scene chapters."
        )

    if pending_candidates > 0 or awaiting_runs > 0:
        next_tool = "apply_pending_cataloging"
        note = "There are staged candidates or awaiting chapters. Apply them before continuing."
        next_arguments = {"job_id": job_id}
    elif failed_runs > 0:
        next_tool = "retry_current_cataloging_chapter"
        note = "Retry or inspect failed chapters before moving on."
        next_arguments = {"job_id": job_id}
    elif facts_saved_runs > 0:
        next_tool = "get_next_external_cataloging_chapter"
        note = "Generate candidates for the earliest facts_saved chapter by calling phase='candidates'."
        next_arguments = {"job_id": job_id, "phase": "candidates"}
    elif pending_runs > 0:
        next_tool = "get_next_external_cataloging_chapter"
        note = "Extract facts for the earliest pending chapter."
        next_arguments = {"job_id": job_id, "phase": "facts"}
    elif in_progress_runs > 0:
        next_tool = "verify_external_cataloging_progress"
        note = "Wait for the in-progress chapter turn to save candidates or apply them, then verify again."
        next_arguments = {"job_id": job_id}
    else:
        next_tool = "get_project_archive_status"
        note = "All chapter runs are processed. Verify archive counts before reporting completion."
        next_arguments = {"project_id": effective_project_id}

    next_candidate_run = _next_candidate_run(db, job_id)

    return {
        "tool": "verify_external_cataloging_progress",
        "status": "ok",
        "detail": f"Progress: {completed_runs}/{total_runs} chapters processed",
        "data": {
            "job_id": job_id,
            "project_id": effective_project_id,
            "chapters_processed": completed_runs,
            "chapters_total": total_runs,
            "chapters_pending": pending_runs,
            "chapters_in_progress": in_progress_runs,
            "chapters_facts_saved": facts_saved_runs,
            "chapters_awaiting_confirmation": awaiting_runs,
            "chapters_failed": failed_runs,
            "next_candidate_run": _run_summary(next_candidate_run),
            "chapters_count": chapters_count,
            "characters_count": characters_count,
            "worldbuilding_count": wb_count,
            "outline_nodes_count": outline_count,
            "chapter_outline_nodes_count": chapter_outline_count,
            "section_outline_nodes_count": section_outline_count,
            "relationships_count": rel_count,
            "pending_candidates": pending_candidates,
            "next_tool": next_tool,
            "next_arguments": next_arguments,
            "workflow_reminder": _workflow_reminder(next_tool, note=note),
            "warnings": warnings,
        },
    }
