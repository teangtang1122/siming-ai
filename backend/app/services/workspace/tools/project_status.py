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
            "warnings": warnings,
            "recommended_next_steps": recommended_next_steps,
        },
    }
