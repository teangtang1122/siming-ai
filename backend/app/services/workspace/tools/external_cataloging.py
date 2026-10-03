"""External cataloging tools — API-free tools for external agents to catalog imported chapters.

These tools work without any Siming model API configured. They allow
Claude Code / Codex to extract characters, worldbuilding, outline,
and chapter summaries from imported text.
"""
from __future__ import annotations

from app.services.cataloging.archive_reader import read_cataloging_archive as read_cataloging_archive


import logging
import os
from datetime import datetime
from typing import Any

from ..external_results import external_tool_failure

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session
from app.core.legacy_env import compatible_env_prefixes
from app.database.models import (
    CatalogingCandidate,
    CatalogingChapterRun,
    CatalogingJob,
    Chapter,
    Character,
    CharacterRelationship,
    OutlineNode,
    Project,
    WorldbuildingEntry,
)
from app.database.query_filters import current_worldbuilding_clause
from app.modules.story.application.content_sync import ensure_chapter_mirror
from app.services.cataloging.launcher import create_and_queue_cataloging_job

logger = logging.getLogger(__name__)


COMPLETED_RUN_STATUSES = {"completed", "completed_with_warnings"}


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


def _job_project_id(job: Any, provided_project_id: str) -> tuple[str, str | None]:
    effective_project_id = str(getattr(job, "project_id", "") or "").strip()
    provided = str(provided_project_id or "").strip()
    if provided and effective_project_id and provided != effective_project_id:
        return effective_project_id, (
            f"project_id mismatch: provided {provided}, but job {getattr(job, 'id', '')} belongs to {effective_project_id}"
        )
    return effective_project_id or provided, None


def _workflow_reminder(next_tool: str, *, note: str = "") -> dict[str, Any]:
    return {"mode": "cataloging_plan", "next_tool": next_tool, "note": note,
            "standard_flow": ["get_next_external_cataloging_chapter",
                              "read_cataloging_archive as needed",
                              "save_external_cataloging_candidates(finalize=true)",
                              "apply_pending_cataloging when authorized", "verify_external_cataloging_progress"],
            "language_rule": "Use the source language. Process one chapter completely before the next."}


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
        .filter(CatalogingChapterRun.status.notin_([*COMPLETED_RUN_STATUSES, "skipped_by_user"]))
        .order_by(CatalogingChapterRun.chapter_order.asc())
        .first()
    )


def _previous_unfinished_run(db: Session, run: Any) -> Any | None:
    return (
        db.query(CatalogingChapterRun)
        .filter(CatalogingChapterRun.job_id == run.job_id)
        .filter(CatalogingChapterRun.chapter_order < run.chapter_order)
        .filter(CatalogingChapterRun.status.notin_([*COMPLETED_RUN_STATUSES, "skipped_by_user"]))
        .order_by(CatalogingChapterRun.chapter_order.asc())
        .first()
    )


def _candidate_gate(db: Session, run: Any) -> tuple[bool, dict[str, Any] | None, str]:
    previous = _previous_unfinished_run(db, run)
    if previous:
        return False, _run_summary(previous), "A previous chapter has not been applied."
    if run.status not in {"pending", "in_progress", "extracting"}:
        return False, _run_summary(run), "The chapter must be resumed or resolved before changing its plan."
    return True, None, "This is the current chapter plan."


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
        return external_tool_failure("start_external_cataloging_job", "project_id is required to start an external cataloging job")

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
        return external_tool_failure("start_external_cataloging_job", "No chapters found for this project")

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
                    "Process each chapter through one plan, application, and verification in chapter_order."
                ),
            ),
        },
    }


async def get_next_external_cataloging_chapter(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    from app.services.cataloging.job_control import validate_cataloging_run_source
    from app.services.cataloging.candidate_retry import candidate_recovery_context
    from app.prompts.cataloging_source import get_internal_cataloging_system_prompt

    tool = "get_next_external_cataloging_chapter"
    job_id = args.get("job_id")
    job = db.get(CatalogingJob, job_id) if job_id else None
    if job is None:
        return external_tool_failure(tool, "Job not found")
    effective_project_id, mismatch = _job_project_id(job, project_id)
    if mismatch:
        return external_tool_failure(tool, mismatch)
    binding_error = _managed_binding_error(project_id=effective_project_id, job_id=job.id)
    if binding_error:
        return _managed_stop_result(job.id, effective_project_id, binding_error)
    run = _earliest_unfinished_run(db, job.id)
    if run is None:
        return {"tool": tool, "status": "ok", "data": {"all_done": True, "job_id": job.id}}
    binding_error = _managed_binding_error(project_id=effective_project_id, job_id=job.id, chapter_id=run.chapter_id)
    if binding_error:
        return _managed_stop_result(job.id, effective_project_id, binding_error)
    if job.status in {"paused", "paused_on_failure", "cancelled"}:
        return external_tool_failure(tool, "任务已暂停或取消；先恢复任务")
    if run.status == "awaiting_confirmation":
        return {"tool": tool, "status": "ok", "data": {"job_id": job.id,
                "chapter_id": run.chapter_id, "waiting_for_apply": True,
                "next_tool": "apply_pending_cataloging"}}
    chapter = validate_cataloging_run_source(db, job, run)
    run.status = "extracting"
    run.started_at = run.started_at or datetime.utcnow()
    job.status = "running"
    job.current_chapter_id = chapter.id
    commit_session(db)
    project = db.get(Project, effective_project_id)
    folder, file_path = ensure_chapter_mirror(db, project, chapter, index=run.chapter_order + 1,
                                             source="cataloging")
    include_content = args.get("include_content", True)
    return {"tool": tool, "status": "ok", "detail": chapter.title, "data": {
        "job_id": job.id, "project_id": effective_project_id, "chapter_run_id": run.id,
        "chapter_id": chapter.id, "chapter_version": run.chapter_version,
        "chapter_index": run.chapter_order, "title": chapter.title,
        "outline_node_id": chapter.outline_node_id,
        "content": chapter.content if include_content else None, "content_included": include_content,
        "content_file_path": str(file_path), "project_folder": str(folder),
        "recovery_context": candidate_recovery_context(db, run, include_payloads=False),
        "prompt_pack": {"system_prompt": get_internal_cataloging_system_prompt()}
            if args.get("include_prompt_pack", False) else None,
        "next_tool": "read_cataloging_archive",
        "workflow_reminder": _workflow_reminder("save_external_cataloging_candidates",
            note="Read chapter and real archives in the same Agent turn; declare explicit ID bindings in the summary plan.")}}


async def save_external_cataloging_candidates(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    """Stage one plan increment; finalize only when the Agent explicitly requests it."""
    from app.services.cataloging.candidate_store import create_candidate_from_raw
    from app.services.cataloging.candidate_retry import candidate_issue, candidate_recovery_context
    from app.services.cataloging.plan_validation import inspect_complete_plan
    from app.services.cataloging.job_control import validate_cataloging_run_source

    tool = "save_external_cataloging_candidates"
    job = db.get(CatalogingJob, args.get("job_id")) if args.get("job_id") else None
    if job is None:
        return external_tool_failure(tool, "Job not found")
    effective_project_id, mismatch = _job_project_id(job, project_id)
    if mismatch:
        return external_tool_failure(tool, mismatch)
    chapter_id = args.get("chapter_id")
    error = _managed_binding_error(project_id=effective_project_id, job_id=job.id, chapter_id=chapter_id or "")
    if error:
        return external_tool_failure(tool, error)
    run = db.query(CatalogingChapterRun).filter_by(job_id=job.id, chapter_id=chapter_id).first()
    if run is None:
        return external_tool_failure(tool, "Chapter run not found")
    # Refresh durable controls after an unbounded provider/MCP round trip.
    db.refresh(job)
    db.refresh(run)
    if job.status in {"paused", "paused_on_failure", "cancelled"}:
        return external_tool_failure(tool, "任务已暂停或取消；本次没有写入")
    allowed, blocking, note = _candidate_gate(db, run)
    if not allowed:
        return _candidate_gate_failure(job.id, effective_project_id, chapter_id, run, blocking, note)
    try:
        validate_cataloging_run_source(db, job, run)
    except ValueError as exc:
        return external_tool_failure(tool, str(exc))
    records = args.get("candidates")
    finalize = args.get("finalize", False)
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        return external_tool_failure(tool, "candidates 必须是原生 JSON 对象数组")
    if not isinstance(finalize, bool):
        return external_tool_failure(tool, "finalize 必须是 boolean")
    from app.services.cataloging.scene_contract import plan_scene_count, validate_scene_submission
    summary_in_batch = next((row for row in records if row.get("type") == "chapter_summary"), None)
    declared_scenes = summary_in_batch.get("scenes") if summary_in_batch else None
    scene_count = len(declared_scenes) if isinstance(declared_scenes, list) else plan_scene_count(db, run)
    try:
        validate_scene_submission(records, scene_count)
    except ValueError as exc:
        return external_tool_failure(tool, str(exc))
    rejected_ids = args.get("reject_candidate_ids", [])
    if not isinstance(rejected_ids, list) or any(not isinstance(value, str) for value in rejected_ids):
        return external_tool_failure(tool, "reject_candidate_ids 必须为当前章节候选 ID 数组")
    rejected = [db.get(CatalogingCandidate, identity) for identity in rejected_ids]
    if any(row is None or row.chapter_run_id != run.id or row.project_id != effective_project_id
           or row.status not in {"pending", "apply_failed", "rejected"} or row.edited_payload for row in rejected):
        return external_tool_failure(tool, "只能撤回本章未应用、未经作者编辑或确认的候选")
    for row in rejected:
        row.status = "rejected"
        row.error = "当前建档 Agent 在修正计划时明确撤回"
    saved, duplicates, errors = 0, 0, []
    count = db.query(CatalogingCandidate).filter_by(chapter_run_id=run.id).count()
    for index, record in enumerate(records):
        # Summary precedes dependent candidates in the same call. Invalid
        # records do not discard valid records or trigger another model phase.
        with db.begin_nested() as checkpoint:
            result = create_candidate_from_raw(db, job, run, record, count + index, source_task="cataloging_plan")
            if result.get("bad_line") or result.get("skipped"):
                checkpoint.rollback()
        if result.get("bad_line"):
            errors.append({"index": index, **candidate_issue(result)})
        elif result.get("skipped"):
            errors.append({"index": index, "message": result.get("reason"), "rejected_candidate": record})
        elif result.get("duplicate"):
            duplicates += 1
        elif result.get("scene_plan_replaced"):
            saved += len(result["candidates"])
        elif result.get("candidate"):
            saved += 1
        db.flush()
    candidates = db.query(CatalogingCandidate).filter_by(chapter_run_id=run.id).all()
    report = inspect_complete_plan(db, run, rows=candidates)
    submission_blockers = [f"本次候选 {issue['index']}：{issue['message']}" for issue in errors]
    errors.extend(report["candidate_errors"])
    missing = list(dict.fromkeys([*report["missing_required_items"], *submission_blockers]))
    report = {"candidate_errors": errors, "missing_required_items": missing}
    complete = finalize and not errors and not missing
    run.status = "awaiting_confirmation" if complete else "extracting"
    run.error = None
    binding = _managed_cataloging_binding(project_id=effective_project_id, job_id=job.id, chapter_id=chapter_id)
    inline_apply = bool(complete and binding and job.execution_mode == "auto" and job.execution_backend == "local_cli_agent")
    job.status = "running" if not complete or inline_apply else "waiting_confirmation"
    job.blocked_chapter_id = chapter_id if complete and not inline_apply else None
    commit_session(db)
    auto_applied = False
    if inline_apply:
        from app.services.workspace.tools.cataloging import apply_pending_cataloging
        result = await apply_pending_cataloging(db, effective_project_id, {"job_id": job.id})
        auto_applied = result.get("status") == "ok"
        commit_session(db)
    status = "error" if finalize and not complete else "skipped" if errors and not saved else "ok"
    return {"tool": tool, "status": status,
            "detail": "计划已完整提交" if complete else "计划尚未通过校验；候选已暂存，错误和缺项均须处理后再 finalize",
            "data": {"job_id": job.id, "chapter_id": chapter_id, "candidates_saved": saved,
                     "duplicates_skipped": duplicates, "candidate_errors": errors,
                     "candidate_set_complete": complete, "missing_required_items": missing,
                     "chapter_run_status": run.status, "auto_applied": auto_applied,
                     "recovery_context": candidate_recovery_context(db, run, include_payloads=False, plan_report=report)
                         if not complete else None}}


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
        return external_tool_failure("verify_external_cataloging_progress", "job_id is required")

    job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
    if not job:
        return external_tool_failure("verify_external_cataloging_progress", "Job not found")
    effective_project_id, mismatch = _job_project_id(job, project_id)
    if mismatch:
        return external_tool_failure("verify_external_cataloging_progress", mismatch)

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
    extracting_runs = db.query(CatalogingChapterRun).filter(
        CatalogingChapterRun.job_id == job_id,
        CatalogingChapterRun.status == "extracting",
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

    if awaiting_runs > 0:
        next_tool = "apply_pending_cataloging"
        note = "A complete plan is awaiting application or author confirmation."
        next_arguments = {"job_id": job_id}
    elif failed_runs > 0:
        next_tool = "repair_cataloging_plan_current"
        note = "Retry or inspect failed chapters before moving on."
        next_arguments = {"job_id": job_id}
    elif extracting_runs > 0:
        next_tool = "get_next_external_cataloging_chapter"
        note = "Continue the earliest chapter plan using its retained candidates."
        next_arguments = {"job_id": job_id}
    elif pending_runs > 0:
        next_tool = "get_next_external_cataloging_chapter"
        note = "Read the earliest pending chapter and plan against real archives."
        next_arguments = {"job_id": job_id}
    elif in_progress_runs > 0:
        next_tool = "verify_external_cataloging_progress"
        note = "Wait for the in-progress chapter turn to save candidates or apply them, then verify again."
        next_arguments = {"job_id": job_id}
    else:
        next_tool = "get_project_archive_status"
        note = "All chapter runs are processed. Verify archive counts before reporting completion."
        next_arguments = {"project_id": effective_project_id}

    next_candidate_run = _earliest_unfinished_run(db, job_id)

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
            "chapters_extracting": extracting_runs,
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


def _candidate_gate_failure(
    job_id: str, effective_project_id: str, chapter_id: str,
    chapter_run: CatalogingChapterRun, blocking_run: dict[str, Any] | None, gate_note: str,
) -> dict[str, Any]:
    if chapter_run.status == "awaiting_confirmation":
        next_tool = "apply_pending_cataloging"
        next_arguments = {"job_id": job_id}
    elif blocking_run and blocking_run.get("status") == "extracting":
        next_tool = "get_next_external_cataloging_chapter"
        next_arguments = {"job_id": job_id}
    elif blocking_run and blocking_run.get("status") == "awaiting_confirmation":
        next_tool = "apply_pending_cataloging"
        next_arguments = {"job_id": job_id}
    else:
        next_tool = "get_next_external_cataloging_chapter"
        next_arguments = {"job_id": job_id}
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
                    "then request the next chapter."
                ),
            ),
        },
    }
