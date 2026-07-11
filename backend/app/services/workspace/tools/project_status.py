"""Project archive status tool — canonical way to verify whether project data exists.

Gives internal and external agents a single tool to check project
completeness before reporting 'done'.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


async def get_project_archive_status(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Return counts and health indicators for a project's archived data.

    API-free: reads from database only.
    """
    from app.database.models import (
        CatalogingJob,
        CatalogingChapterRun,
        Chapter,
        ChapterSummary,
        Character,
        CharacterAlias,
        CharacterRelationship,
        OutlineNode,
        WorldbuildingEntry,
    )
    from app.services.story_granularity import inspect_chapter_granularity
    from app.services.narrative_ledger import list_narrative_ledger
    if not str(project_id or "").strip():
        return {
            "tool": "get_project_archive_status",
            "status": "skipped",
            "detail": "project_id is required to verify project archive status",
            "data": None,
        }

    chapters_count = db.query(Chapter).filter(
        Chapter.project_id == project_id,
    ).count()

    summaries_count = db.query(ChapterSummary).join(Chapter).filter(
        Chapter.project_id == project_id,
    ).count()

    outline_count = db.query(OutlineNode).filter(
        OutlineNode.project_id == project_id,
    ).count()

    characters_count = db.query(Character).filter(
        Character.project_id == project_id,
    ).count()

    aliases_count = db.query(CharacterAlias).filter(
        CharacterAlias.project_id == project_id,
    ).count()

    relationships_count = db.query(CharacterRelationship).filter(
        CharacterRelationship.project_id == project_id,
    ).count()

    worldbuilding_count = db.query(WorldbuildingEntry).filter(
        WorldbuildingEntry.project_id == project_id,
    ).count()

    # Last cataloging job
    last_job = db.query(CatalogingJob).filter(
        CatalogingJob.project_id == project_id,
    ).order_by(CatalogingJob.created_at.desc()).first()

    last_job_data = None
    if last_job:
        failed_runs = db.query(CatalogingChapterRun).filter(
            CatalogingChapterRun.job_id == last_job.id,
            CatalogingChapterRun.status == "failed",
        ).count()
        pending_candidates = db.query(CatalogingChapterRun).filter(
            CatalogingChapterRun.job_id == last_job.id,
            CatalogingChapterRun.status == "pending",
        ).count()
        last_job_data = {
            "job_id": last_job.id,
            "status": last_job.status,
            "execution_mode": last_job.execution_mode,
            "total_chapters": last_job.total_chapters,
            "completed_chapters": last_job.completed_chapters,
            "failed_runs": failed_runs,
            "pending_candidates": pending_candidates,
            "created_at": last_job.created_at.isoformat() if last_job.created_at else None,
        }

    # Warnings
    warnings: list[str] = []
    if chapters_count > 0 and characters_count == 0:
        warnings.append("chapters_imported_but_no_characters")
    if chapters_count > 0 and outline_count == 0:
        warnings.append("chapters_imported_but_no_outline")
    if chapters_count > 0 and summaries_count == 0:
        warnings.append("chapters_imported_but_no_summaries")
    if last_job and last_job.status == "failed":
        warnings.append("last_cataloging_job_failed")
    if last_job_data and last_job_data["failed_runs"] > 0:
        warnings.append(f"{last_job_data['failed_runs']}_chapter_runs_failed")

    audited_chapters = db.query(Chapter).filter(
        Chapter.project_id == project_id,
    ).order_by(Chapter.created_at.asc()).limit(200).all()
    granularity_items = [
        inspect_chapter_granularity(db, project_id, chapter)
        for chapter in audited_chapters
    ]
    granularity_missing: dict[str, int] = {}
    granularity_warnings: dict[str, int] = {}
    narrative_totals: dict[str, int] = {
        "chapter_narrative_state_count": 0,
        "section_scene_state_count": 0,
        "chapter_element_link_count": 0,
        "event_count": 0,
        "foreshadowing_planted_count": 0,
        "foreshadowing_resolved_count": 0,
        "storyline_progress_count": 0,
        "unresolved_action_count": 0,
        "ledger_entry_count": 0,
        "completed_beat_count": 0,
        "revealed_clue_count": 0,
        "narrative_promise_count": 0,
        "storyline_state_count": 0,
    }
    for item in granularity_items:
        for key in item["missing"]:
            granularity_missing[key] = granularity_missing.get(key, 0) + 1
        for key in item["warnings"]:
            granularity_warnings[key] = granularity_warnings.get(key, 0) + 1
        narrative = item.get("narrative_health") if isinstance(item, dict) else None
        if isinstance(narrative, dict):
            for key in narrative_totals:
                value = narrative.get(key)
                if isinstance(value, int):
                    narrative_totals[key] += value
    granularity_health = {
        "chapters_checked": len(granularity_items),
        "ok_chapters": sum(1 for item in granularity_items if item["ok"]),
        "missing_counts": granularity_missing,
        "warning_counts": granularity_warnings,
        "narrative_health": {
            **narrative_totals,
            "needs_attention": any(
                key in granularity_warnings
                for key in ("chapter_narrative_state_missing", "section_scene_state_missing", "narrative_progress_missing")
            ),
        },
        "needs_repair": bool(granularity_missing or granularity_warnings),
        "sample_issues": [
            item for item in granularity_items
            if item["missing"] or item["warnings"]
        ][:10],
    }
    ledger_items = list_narrative_ledger(db, project_id)
    ledger_type_counts: dict[str, int] = {}
    for item in ledger_items:
        ledger_type = str(item.get("ledger_type") or "unknown")
        ledger_type_counts[ledger_type] = ledger_type_counts.get(ledger_type, 0) + 1
    granularity_health["narrative_ledger"] = {
        "active_count": len(ledger_items),
        "type_counts": ledger_type_counts,
        "open_promise_count": sum(1 for item in ledger_items if item.get("ledger_type") == "narrative_promise" and item.get("status") == "open"),
        "needs_attention": "narrative_ledger_missing" in granularity_warnings or "unanchored_narrative_promises" in granularity_warnings,
    }
    if granularity_health["needs_repair"]:
        warnings.append("story_granularity_needs_attention")

    # Recommended next steps
    recommended_next_steps: list[str] = []
    if chapters_count == 0:
        recommended_next_steps.append("import_chapters")
    elif characters_count == 0 and outline_count == 0:
        if last_job and last_job.execution_mode == "external_agent":
            recommended_next_steps.append("run_external_cataloging")
        else:
            recommended_next_steps.append("run_cataloging")
    elif last_job_data and last_job_data["pending_candidates"] > 0:
        recommended_next_steps.append("apply_pending_cataloging")
    elif last_job_data and last_job_data["failed_runs"] > 0:
        recommended_next_steps.append("retry_failed_chapters")
    if granularity_health["needs_repair"]:
        recommended_next_steps.append("inspect_story_granularity")

    return {
        "tool": "get_project_archive_status",
        "status": "ok",
        "detail": f"{chapters_count} chapters, {characters_count} characters, {outline_count} outline nodes, {worldbuilding_count} worldbuilding entries",
        "data": {
            "chapters_count": chapters_count,
            "chapter_summaries_count": summaries_count,
            "outline_nodes_count": outline_count,
            "characters_count": characters_count,
            "character_aliases_count": aliases_count,
            "relationships_count": relationships_count,
            "worldbuilding_count": worldbuilding_count,
            "last_cataloging_job": last_job_data,
            "granularity_health": granularity_health,
            "warnings": warnings,
            "recommended_next_steps": recommended_next_steps,
        },
    }
