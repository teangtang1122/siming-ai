"""External cataloging tools — API-free tools for external agents to catalog imported chapters.

These tools work without any Moshu model API configured. They allow
Claude Code / Codex to extract characters, worldbuilding, outline,
and chapter summaries from imported text.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.prompts.cataloging_source import get_outline_granularity_rules

from ....services.content_store import refresh_project_from_files

logger = logging.getLogger(__name__)


CANONICAL_CANDIDATE_TYPES = {
    "chapter_summary",
    "character_create",
    "character_update",
    "character_state_update",
    "character_timeline",
    "character_relationship",
    "character_merge_candidate",
    "worldbuilding_timeline",
    "chapter_link",
    "outline_create",
    "outline_update",
    "worldbuilding_create",
    "worldbuilding_update",
}

COMPLETED_RUN_STATUSES = {"completed", "completed_with_warnings"}


def _normalize_candidate_input(candidate: dict[str, Any]) -> tuple[dict[str, Any], str, str, str | None]:
    """Normalize external-agent candidate shorthand to the internal apply contract."""
    payload = candidate.get("data") if isinstance(candidate.get("data"), dict) else None
    normalized_payload = dict(payload or candidate)
    # Map character_name to name if name is not present
    if "character_name" in candidate and "name" not in candidate:
        candidate["name"] = candidate["character_name"]

    for key in [
        "name", "title", "summary", "content", "dimension", "aliases",
        "source_name", "target_name", "character_a", "character_b",
        "relationship_type", "chapter_id", "outline_node_id", "id",
    ]:
        if key in candidate and key not in normalized_payload:
            normalized_payload[key] = candidate[key]

    raw_type = str(
        candidate.get("item_type")
        or candidate.get("type")
        or normalized_payload.get("item_type")
        or normalized_payload.get("type")
        or ""
    ).strip()
    action = str(
        candidate.get("operation")
        or candidate.get("action")
        or normalized_payload.get("operation")
        or normalized_payload.get("action")
        or "create"
    ).strip().lower()

    item_type = _canonical_candidate_type(raw_type, action)
    operation = _operation_for(item_type, action)
    normalized_payload["item_type"] = item_type
    normalized_payload["operation"] = operation
    normalized_payload["type"] = item_type
    normalized_payload["action"] = operation

    warning = None
    if item_type == "unknown":
        warning = f"Unsupported candidate type: type={raw_type or '<empty>'} action={action or '<empty>'}"
    return normalized_payload, item_type, operation, warning


def _canonical_candidate_type(raw_type: str, action: str) -> str:
    text = raw_type.lower().replace("-", "_").replace(" ", "_")
    op = action.lower().replace("-", "_").replace(" ", "_")
    if text in CANONICAL_CANDIDATE_TYPES:
        return text
    if text in {"character", "角色"}:
        if op in {"update", "upsert", "merge"}:
            return "character_update"
        return "character_create"
    if text in {"character_state", "state", "角色状态"}:
        return "character_state_update"
    if text in {"relationship", "character_relation", "角色关系"}:
        return "character_relationship"
    if text in {"timeline", "character_event", "角色时间线"}:
        return "character_timeline"
    if text in {"character_merge", "duplicate_character", "角色合并"}:
        return "character_merge_candidate"
    if text in {"outline", "outline_node", "大纲"}:
        return "outline_update" if op == "update" else "outline_create"
    if text in {"worldbuilding", "world", "setting", "设定", "世界观"}:
        return "worldbuilding_update" if op == "update" else "worldbuilding_create"
    if text in {"worldbuilding_event", "world_timeline", "setting_timeline", "世界观时间线"}:
        return "worldbuilding_timeline"
    if text in {"summary", "chapter", "chapter_summary", "章节摘要"}:
        return "chapter_summary"
    if text in {"chapter_link", "link", "章节关联"}:
        return "chapter_link"
    return "unknown"


def _operation_for(item_type: str, action: str) -> str:
    if item_type.endswith("_create"):
        return "create"
    if item_type.endswith("_update") or item_type in {"character_state_update", "worldbuilding_timeline"}:
        return "update"
    if action in {"create", "update", "delete", "merge", "link", "upsert"}:
        return action
    return "upsert"


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


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
            "facts": "Fact extraction may run in parallel across chapters. Save facts for each chapter as soon as they are extracted.",
            "candidates": "Candidate generation and apply must be sequential by chapter_order. Never generate candidates for a later chapter before every earlier chapter is applied.",
            "why": "Candidates merge into cumulative character, outline, and worldbuilding cards. Later chapters must see earlier applied cards to avoid scrambled backgrounds and duplicate entities.",
        },
        "language_rule": (
            "Use the novel/source language for archive data. For Chinese novels, "
            "save Chinese names, titles, summaries, facts, candidates, aliases, "
            "outline nodes, and worldbuilding. Do not translate to English unless the user explicitly asks."
        ),
        "outline_granularity_policy": get_outline_granularity_rules(),
        "no_api_rule": (
            "When the user says Moshu API is unavailable, do not call internal LLM tools such as "
            "start_cataloging_job, chapter_writer, character_writer, outline_writer, worldbuilding_writer, "
            "design_plot, or evaluate_chapter."
        ),
        "standard_flow": [
            "get_moshu_usage_guide(scenario='cataloging_no_api', no_api=true)",
            "get_prompt_pack(pack_id='cataloging_external_no_api')",
            "start_external_cataloging_job",
            "Parallel fact stage: get_next_external_cataloging_chapter(phase='facts') -> save_external_cataloging_facts for many chapters",
            "Sequential candidate stage: get_next_external_cataloging_chapter(phase='candidates') -> save_external_cataloging_candidates -> apply_pending_cataloging -> verify_external_cataloging_progress, one chapter at a time in chapter_order",
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
    from app.database.models import CatalogingChapterRun

    return (
        db.query(CatalogingChapterRun)
        .filter(CatalogingChapterRun.job_id == job_id)
        .filter(CatalogingChapterRun.status.notin_(list(COMPLETED_RUN_STATUSES)))
        .order_by(CatalogingChapterRun.chapter_order.asc())
        .first()
    )


def _previous_unfinished_run(db: Session, run: Any) -> Any | None:
    from app.database.models import CatalogingChapterRun

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
    from app.database.models import CatalogingJob, CatalogingChapterRun, Chapter

    if not str(project_id or "").strip():
        return {
            "tool": "start_external_cataloging_job",
            "status": "skipped",
            "detail": "project_id is required to start an external cataloging job",
            "data": None,
        }

    # Get chapters for this project
    refresh_project_from_files(db, project_id)
    db.flush()
    chapter_ids = args.get("chapter_ids", [])
    if chapter_ids:
        chapters = db.query(Chapter).filter(
            Chapter.project_id == project_id,
            Chapter.id.in_(chapter_ids),
        ).order_by(Chapter.created_at).all()
    else:
        chapters = db.query(Chapter).filter(
            Chapter.project_id == project_id,
        ).order_by(Chapter.created_at).all()

    if not chapters:
        return {
            "tool": "start_external_cataloging_job",
            "status": "skipped",
            "detail": "No chapters found for this project",
            "data": None,
        }

    # Create job
    job = CatalogingJob(
        project_id=project_id,
        execution_mode="external_agent",
        status="running",
        total_chapters=len(chapters),
    )
    db.add(job)
    db.flush()

    # Create chapter runs
    for i, chapter in enumerate(chapters):
        run = CatalogingChapterRun(
            job_id=job.id,
            project_id=project_id,
            chapter_id=chapter.id,
            chapter_order=i,
            status="pending",
        )
        db.add(run)

    db.commit()
    db.refresh(job)

    return {
        "tool": "start_external_cataloging_job",
        "status": "ok",
        "detail": f"Job created with {len(chapters)} chapters",
        "data": {
            "job_id": job.id,
            "chapter_count": len(chapters),
            "status": job.status,
            "next_tool": "get_prompt_pack",
            "workflow_reminder": _workflow_reminder(
                "get_prompt_pack",
                note=(
                    "Read the cataloging_external_no_api prompt pack before extracting facts. "
                    "Facts may be extracted in parallel, but candidates must later be generated and applied in chapter_order."
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
    from app.database.models import (
        CatalogingJob, CatalogingChapterRun, Chapter,
        Character, WorldbuildingEntry, OutlineNode,
        PublicPromptPack,
    )
    from app.services.prompt_packs.seed import ensure_builtin_packs

    ensure_builtin_packs(db)

    job_id = str(args.get("job_id") or "").strip()
    phase = str(args.get("phase") or "facts").strip().lower()
    if phase in {"candidate", "resolution", "resolve", "apply"}:
        phase = "candidates"
    if phase not in {"facts", "candidates"}:
        phase = "facts"
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

    refresh_project_from_files(db, effective_project_id)
    db.flush()

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
        # Get next pending chapter run for fact extraction.
        chapter_run = db.query(CatalogingChapterRun).filter(
            CatalogingChapterRun.job_id == job_id,
            CatalogingChapterRun.status == "pending",
        ).order_by(CatalogingChapterRun.chapter_order).first()

        if not chapter_run:
            awaiting_run = db.query(CatalogingChapterRun).filter(
                CatalogingChapterRun.job_id == job_id,
                CatalogingChapterRun.status == "awaiting_confirmation",
            ).order_by(CatalogingChapterRun.chapter_order).first()
            if awaiting_run:
                return {
                    "tool": "get_next_external_cataloging_chapter",
                    "status": "ok",
                    "detail": "A chapter is awaiting candidate application before continuing candidate generation",
                    "data": {
                        "job_id": job_id,
                        "project_id": effective_project_id,
                        "phase": "facts",
                        "chapter_id": awaiting_run.chapter_id,
                        "chapter_index": awaiting_run.chapter_order,
                        "all_done": False,
                        "waiting_for_apply": True,
                        "next_tool": "apply_pending_cataloging",
                        "workflow_reminder": _workflow_reminder(
                            "apply_pending_cataloging",
                            note="Apply the current chapter's candidates before generating candidates for later chapters. You may only fetch more fact chapters if any are still pending.",
                        ),
                    },
                }

            first_unfinished = _earliest_unfinished_run(db, job_id)
            if first_unfinished and first_unfinished.status == "facts_saved":
                return {
                    "tool": "get_next_external_cataloging_chapter",
                    "status": "ok",
                    "detail": "All pending fact chapters are assigned; switch to sequential candidate generation",
                    "data": {
                        "job_id": job_id,
                        "project_id": effective_project_id,
                        "phase": "facts",
                        "all_done": False,
                        "facts_stage_done": True,
                        "next_candidate_run": _run_summary(first_unfinished),
                        "next_tool": "get_next_external_cataloging_chapter",
                        "next_arguments": {"job_id": job_id, "phase": "candidates"},
                        "workflow_reminder": _workflow_reminder(
                            "get_next_external_cataloging_chapter",
                            note="Call get_next_external_cataloging_chapter with phase='candidates' and process candidates strictly in chapter_order.",
                        ),
                    },
                }
            if first_unfinished:
                return {
                    "tool": "get_next_external_cataloging_chapter",
                    "status": "ok",
                    "detail": "No pending fact chapters remain, but some chapters are still being processed",
                    "data": {
                        "job_id": job_id,
                        "project_id": effective_project_id,
                        "phase": "facts",
                        "all_done": False,
                        "waiting_for_facts": True,
                        "blocking_run": _run_summary(first_unfinished),
                        "next_tool": "verify_external_cataloging_progress",
                        "workflow_reminder": _workflow_reminder(
                            "verify_external_cataloging_progress",
                            note="Wait for parallel fact extraction to save facts, then switch to phase='candidates'.",
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
                    "phase": "facts",
                    "all_done": True,
                    "next_tool": "get_project_archive_status",
                    "workflow_reminder": _workflow_reminder(
                        "get_project_archive_status",
                        note="Verify archive counts before reporting the cataloging job complete.",
                    ),
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

    # Build context indexes
    characters = db.query(Character).filter(
        Character.project_id == effective_project_id,
    ).all()
    char_index = {c.name: c.id for c in characters}
    # Also include aliases
    for c in characters:
        if hasattr(c, 'aliases') and c.aliases:
            for alias in c.aliases:
                alias_name = getattr(alias, "alias", None) or getattr(alias, "alias_name", None)
                if alias_name:
                    char_index[alias_name] = c.id

    wb_entries = db.query(WorldbuildingEntry).filter(
        WorldbuildingEntry.project_id == effective_project_id,
    ).all()
    wb_index = {e.title: e.id for e in wb_entries}

    # Get outline neighborhood
    outline_nodes = db.query(OutlineNode).filter(
        OutlineNode.project_id == effective_project_id,
    ).order_by(OutlineNode.sort_order).limit(20).all()
    outline_neighborhood = [
        {"id": n.id, "title": n.title, "node_type": n.node_type, "parent_id": n.parent_id}
        for n in outline_nodes
    ]

    # Get prompt pack
    pack = db.query(PublicPromptPack).filter(
        PublicPromptPack.pack_id == "cataloging_external_no_api",
        PublicPromptPack.enabled == True,
    ).first()

    prompt_pack_data = None
    if pack:
        prompt_pack_data = {
            "pack_id": pack.pack_id,
            "version": pack.version,
            "system_prompt": pack.system_prompt,
            "workflow": pack.workflow_json,
        }

    # Mark chapter run as in_progress only when assigning fact extraction.
    if phase == "facts":
        chapter_run.status = "in_progress"
        db.commit()

    return {
        "tool": "get_next_external_cataloging_chapter",
        "status": "ok",
        "detail": f"Chapter: {chapter.title}",
        "data": {
            "job_id": job_id,
            "project_id": effective_project_id,
            "phase": phase,
            "chapter_id": chapter.id,
            "chapter_index": chapter_run.chapter_order,
            "title": chapter.title,
            "content": chapter.content,
            "character_alias_index": char_index,
            "worldbuilding_title_index": wb_index,
            "outline_neighborhood": outline_neighborhood,
            "outline_granularity_policy": get_outline_granularity_rules(),
            "prompt_pack": prompt_pack_data,
            "next_tool": "save_external_cataloging_facts" if phase == "facts" else "save_external_cataloging_candidates",
            "workflow_reminder": _workflow_reminder(
                "save_external_cataloging_facts" if phase == "facts" else "save_external_cataloging_candidates",
                note=(
                    "Read this chapter with the prompt pack, then save extracted facts in the source language."
                    if phase == "facts"
                    else "Generate candidates for this chapter now. This is the current sequential candidate turn; do not skip ahead."
                ),
            ),
        },
    }


async def save_external_cataloging_facts(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Save facts extracted by the external model.

    API-free: stores facts in CatalogingFact table.
    """
    from app.database.models import CatalogingJob, CatalogingChapterRun, CatalogingFact

    job_id = str(args.get("job_id") or "").strip()
    chapter_id = str(args.get("chapter_id") or "").strip()
    facts = args.get("facts", [])

    if not job_id or not chapter_id:
        return {
            "tool": "save_external_cataloging_facts",
            "status": "skipped",
            "detail": "job_id and chapter_id are required",
            "data": None,
        }

    job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
    if not job:
        return {
            "tool": "save_external_cataloging_facts",
            "status": "skipped",
            "detail": "Job not found",
            "data": None,
        }
    effective_project_id, mismatch = _job_project_id(job, project_id)
    if mismatch:
        return {
            "tool": "save_external_cataloging_facts",
            "status": "skipped",
            "detail": mismatch,
            "data": None,
        }

    # Find the chapter run
    chapter_run = db.query(CatalogingChapterRun).filter(
        CatalogingChapterRun.job_id == job_id,
        CatalogingChapterRun.chapter_id == chapter_id,
    ).first()

    if not chapter_run:
        return {
            "tool": "save_external_cataloging_facts",
            "status": "skipped",
            "detail": "Chapter run not found",
            "data": None,
        }

    # Save facts
    saved = 0
    for fact_data in facts:
        if not isinstance(fact_data, dict):
            continue
        fact = CatalogingFact(
            job_id=job_id,
            chapter_run_id=chapter_run.id,
            project_id=job.project_id,
            chapter_id=chapter_id,
            fact_type=str(fact_data.get("type", "unknown")),
            raw_payload=json.dumps(fact_data.get("data", fact_data), ensure_ascii=False),
        )
        db.add(fact)
        saved += 1

    chapter_run.status = "facts_saved"
    if job.status == "waiting_confirmation":
        job.status = "running"
    db.commit()

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
    from app.database.models import CatalogingJob, CatalogingChapterRun, CatalogingCandidate

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

    saved = 0
    warnings: list[str] = []
    for cand_data in candidates:
        if not isinstance(cand_data, dict):
            continue
        payload, item_type, operation, warning = _normalize_candidate_input(cand_data)
        if warning:
            warnings.append(warning)
            continue
        candidate = CatalogingCandidate(
            job_id=job_id,
            chapter_run_id=chapter_run.id,
            project_id=job.project_id,
            chapter_id=chapter_id,
            item_type=item_type,
            operation=operation,
            target_id=str(payload.get("target_id") or payload.get("id") or "") or None,
            target_name=str(payload.get("target_name") or payload.get("name") or payload.get("title") or "")[:200] or None,
            raw_payload=json.dumps(payload, ensure_ascii=False),
            status="pending",
            confidence=_float_or_none(payload.get("confidence")),
            evidence=str(payload.get("evidence") or "")[:2000] or None,
            sort_order=int(payload.get("sort_order") or saved),
            source_task="external_agent",
        )
        db.add(candidate)
        saved += 1

    # Saved candidates still need to be applied to project data. Keep the run
    # blocking here so external agents cannot report completion before writes
    # are actually applied.
    chapter_run.status = "awaiting_confirmation"
    job.status = "waiting_confirmation"
    job.blocked_chapter_id = chapter_id
    db.commit()

    result = {
        "tool": "save_external_cataloging_candidates",
        "status": "ok",
        "detail": f"Saved {saved} candidates",
        "data": {
            "job_id": job_id,
            "project_id": effective_project_id,
            "chapter_id": chapter_id,
            "candidates_saved": saved,
            "chapter_run_status": chapter_run.status,
            "next_tool": "apply_pending_cataloging",
            "workflow_reminder": _workflow_reminder(
                "apply_pending_cataloging",
                note="Candidates are only staged until apply_pending_cataloging writes them into characters, outline, worldbuilding, and summaries.",
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
    from app.database.models import (
        CatalogingJob, CatalogingChapterRun, CatalogingCandidate,
        Chapter, Character, WorldbuildingEntry, OutlineNode,
        CharacterRelationship,
    )

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

    # Count chapter runs
    total_runs = db.query(CatalogingChapterRun).filter(
        CatalogingChapterRun.job_id == job_id,
    ).count()
    completed_runs = db.query(CatalogingChapterRun).filter(
        CatalogingChapterRun.job_id == job_id,
        CatalogingChapterRun.status == "completed",
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
    wb_count = db.query(WorldbuildingEntry).filter(WorldbuildingEntry.project_id == effective_project_id).count()
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
        note = "Continue parallel fact extraction with phase='facts'."
        next_arguments = {"job_id": job_id, "phase": "facts"}
    elif in_progress_runs > 0:
        next_tool = "verify_external_cataloging_progress"
        note = "Wait for in-progress fact extraction to save facts, then verify again."
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
