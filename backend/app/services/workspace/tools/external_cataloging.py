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


def _workflow_reminder(next_tool: str, *, note: str = "") -> dict[str, Any]:
    """Return a compact workflow reminder for long-context external agents."""
    return {
        "mode": "external_cataloging_no_api",
        "language_rule": (
            "Use the novel/source language for archive data. For Chinese novels, "
            "save Chinese names, titles, summaries, facts, candidates, aliases, "
            "outline nodes, and worldbuilding. Do not translate to English unless the user explicitly asks."
        ),
        "no_api_rule": (
            "When the user says Moshu API is unavailable, do not call internal LLM tools such as "
            "start_cataloging_job, chapter_writer, character_writer, outline_writer, worldbuilding_writer, "
            "design_plot, or evaluate_chapter."
        ),
        "standard_flow": [
            "get_moshu_usage_guide(scenario='cataloging_no_api', no_api=true)",
            "get_prompt_pack(pack_id='cataloging_external_no_api')",
            "start_external_cataloging_job",
            "Repeat per chapter: get_next_external_cataloging_chapter -> save_external_cataloging_facts -> save_external_cataloging_candidates -> apply_pending_cataloging -> verify_external_cataloging_progress",
            "Finish with get_project_archive_status and verify counts before reporting completion",
        ],
        "next_tool": next_tool,
        "note": note,
    }


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

    # Get chapters for this project
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
                note="Read the cataloging_external_no_api prompt pack before extracting facts.",
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

    # Get next pending chapter run
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
                "detail": "A chapter is awaiting candidate application before continuing",
                "data": {
                    "job_id": job_id,
                    "chapter_id": awaiting_run.chapter_id,
                    "chapter_index": awaiting_run.chapter_order,
                    "all_done": False,
                    "waiting_for_apply": True,
                    "next_tool": "apply_pending_cataloging",
                    "workflow_reminder": _workflow_reminder(
                        "apply_pending_cataloging",
                        note="Apply the current chapter's candidates before reading the next chapter.",
                    ),
                },
            }
        return {
            "tool": "get_next_external_cataloging_chapter",
            "status": "ok",
            "detail": "No more chapters to process",
            "data": {
                "job_id": job_id,
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
        Character.project_id == project_id,
    ).all()
    char_index = {c.name: c.id for c in characters}
    # Also include aliases
    for c in characters:
        if hasattr(c, 'aliases') and c.aliases:
            for alias in c.aliases:
                if alias.alias_name:
                    char_index[alias.alias_name] = c.id

    wb_entries = db.query(WorldbuildingEntry).filter(
        WorldbuildingEntry.project_id == project_id,
    ).all()
    wb_index = {e.title: e.id for e in wb_entries}

    # Get outline neighborhood
    outline_nodes = db.query(OutlineNode).filter(
        OutlineNode.project_id == project_id,
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

    # Mark chapter run as in_progress
    chapter_run.status = "in_progress"
    db.commit()

    return {
        "tool": "get_next_external_cataloging_chapter",
        "status": "ok",
        "detail": f"Chapter: {chapter.title}",
        "data": {
            "job_id": job_id,
            "chapter_id": chapter.id,
            "chapter_index": chapter_run.chapter_order,
            "title": chapter.title,
            "content": chapter.content,
            "character_alias_index": char_index,
            "worldbuilding_title_index": wb_index,
            "outline_neighborhood": outline_neighborhood,
            "prompt_pack": prompt_pack_data,
            "next_tool": "save_external_cataloging_facts",
            "workflow_reminder": _workflow_reminder(
                "save_external_cataloging_facts",
                note="Read this chapter with the prompt pack, then save extracted facts in the source language.",
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

    db.commit()

    return {
        "tool": "save_external_cataloging_facts",
        "status": "ok",
        "detail": f"Saved {saved} facts",
        "data": {
            "job_id": job_id,
            "chapter_id": chapter_id,
            "facts_saved": saved,
            "next_tool": "save_external_cataloging_candidates",
            "workflow_reminder": _workflow_reminder(
                "save_external_cataloging_candidates",
                note="Convert the saved facts into concrete write candidates in the source language.",
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
    awaiting_runs = db.query(CatalogingChapterRun).filter(
        CatalogingChapterRun.job_id == job_id,
        CatalogingChapterRun.status == "awaiting_confirmation",
    ).count()

    # Count project data
    chapters_count = db.query(Chapter).filter(Chapter.project_id == project_id).count()
    characters_count = db.query(Character).filter(Character.project_id == project_id).count()
    wb_count = db.query(WorldbuildingEntry).filter(WorldbuildingEntry.project_id == project_id).count()
    outline_count = db.query(OutlineNode).filter(OutlineNode.project_id == project_id).count()
    rel_count = db.query(CharacterRelationship).filter(CharacterRelationship.project_id == project_id).count()

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

    if pending_candidates > 0 or awaiting_runs > 0:
        next_tool = "apply_pending_cataloging"
        note = "There are staged candidates or awaiting chapters. Apply them before continuing."
    elif failed_runs > 0:
        next_tool = "retry_current_cataloging_chapter"
        note = "Retry or inspect failed chapters before moving on."
    elif pending_runs > 0:
        next_tool = "get_next_external_cataloging_chapter"
        note = "Continue with the next pending chapter."
    else:
        next_tool = "get_project_archive_status"
        note = "All chapter runs are processed. Verify archive counts before reporting completion."

    return {
        "tool": "verify_external_cataloging_progress",
        "status": "ok",
        "detail": f"Progress: {completed_runs}/{total_runs} chapters processed",
        "data": {
            "job_id": job_id,
            "chapters_processed": completed_runs,
            "chapters_total": total_runs,
            "chapters_pending": pending_runs,
            "chapters_awaiting_confirmation": awaiting_runs,
            "chapters_failed": failed_runs,
            "chapters_count": chapters_count,
            "characters_count": characters_count,
            "worldbuilding_count": wb_count,
            "outline_nodes_count": outline_count,
            "relationships_count": rel_count,
            "pending_candidates": pending_candidates,
            "next_tool": next_tool,
            "workflow_reminder": _workflow_reminder(next_tool, note=note),
            "warnings": warnings,
        },
    }
