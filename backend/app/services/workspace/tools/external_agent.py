"""External Agent reporting tools — MCP tools for external Agents to report progress.

These tools write run telemetry only, not project content. They are allowed
in MCP readonly mode because they don't modify project data.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


async def start_agent_run(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Start a new external Agent run."""
    from app.services.external_agent.run_service import create_run

    client_name = str(args.get("client_name") or "unknown").strip()
    title = str(args.get("title") or "").strip() or None
    context_manifest_id = str(args.get("context_manifest_id") or "").strip()

    run = create_run(db, project_id, source="mcp", client_name=client_name, title=title)
    if context_manifest_id:
        from app.services.context_orchestrator import ContextOrchestrator

        manifest = ContextOrchestrator(db).get_manifest(context_manifest_id, project_id)
        if manifest:
            run.context_manifest_id = manifest.id
    return {
        "tool": "start_agent_run",
        "status": "ok",
        "detail": f"Agent run started: {run.id}",
        "data": {"run_id": run.id, "status": run.status, "context_manifest_id": run.context_manifest_id},
    }


async def report_agent_plan(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Report the execution plan for an Agent run."""
    from app.services.external_agent.run_service import add_event, get_run

    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        return {"tool": "report_agent_plan", "status": "skipped", "detail": "run_id is required"}

    run = get_run(db, run_id)
    if not run or run.project_id != project_id:
        return {"tool": "report_agent_plan", "status": "skipped", "detail": "Run not found"}

    plan = args.get("plan", [])
    if isinstance(plan, list):
        plan = plan[:10]  # Max 10 steps

    event = add_event(
        db, run_id, "plan",
        status="ok",
        message=f"Plan: {len(plan)} steps",
        payload_json=json.dumps({"plan": plan}, ensure_ascii=False),
    )
    if not event:
        return {"tool": "report_agent_plan", "status": "skipped", "detail": "Run is terminal"}

    return {
        "tool": "report_agent_plan",
        "status": "ok",
        "detail": f"Plan reported: {len(plan)} steps",
        "data": {"run_id": run_id, "sequence": event.sequence},
    }


async def report_agent_progress(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Report a progress update."""
    from app.services.external_agent.run_service import add_event, get_run

    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        return {"tool": "report_agent_progress", "status": "skipped", "detail": "run_id is required"}

    run = get_run(db, run_id)
    if not run or run.project_id != project_id:
        return {"tool": "report_agent_progress", "status": "skipped", "detail": "Run not found"}

    message = str(args.get("message") or "").strip()
    step = args.get("step")

    payload = {}
    if step is not None:
        payload["step"] = step

    event = add_event(
        db, run_id, "progress",
        status="ok",
        message=message or None,
        payload_json=json.dumps(payload, ensure_ascii=False) if payload else None,
    )
    if not event:
        return {"tool": "report_agent_progress", "status": "skipped", "detail": "Run is terminal"}

    return {
        "tool": "report_agent_progress",
        "status": "ok",
        "detail": f"Progress: {message[:100]}",
        "data": {"run_id": run_id, "sequence": event.sequence},
    }


async def report_context_selected(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Report which context was selected for reasoning."""
    from app.services.external_agent.run_service import add_event, get_run

    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        return {"tool": "report_context_selected", "status": "skipped", "detail": "run_id is required"}

    run = get_run(db, run_id)
    if not run or run.project_id != project_id:
        return {"tool": "report_context_selected", "status": "skipped", "detail": "Run not found"}

    sources = args.get("sources", [])
    if isinstance(sources, list):
        sources = sources[:20]  # Max 20 sources

    evidence_result = None
    if run.context_manifest_id:
        from app.services.context_orchestrator import ContextOrchestrator

        manifest = ContextOrchestrator(db).get_manifest(run.context_manifest_id, project_id)
        if not manifest:
            return {"tool": "report_context_selected", "status": "needs_confirmation", "detail": "Baseline context manifest is unavailable"}
        evidence_result = ContextOrchestrator(db).submit_evidence(manifest, sources)
        if not evidence_result["accepted_count"]:
            return {
                "tool": "report_context_selected",
                "status": "needs_confirmation",
                "detail": "No submitted context source could be verified against the baseline manifest.",
                "data": {"run_id": run_id, "manifest_id": manifest.id, **evidence_result},
            }

    event = add_event(
        db, run_id, "context_selected",
        status="ok",
        message=f"{len(sources)} sources selected",
        payload_json=json.dumps({"sources": sources, "verified": evidence_result}, ensure_ascii=False),
    )
    if not event:
        return {"tool": "report_context_selected", "status": "skipped", "detail": "Run is terminal"}

    return {
        "tool": "report_context_selected",
        "status": "ok",
        "detail": f"Context: {len(sources)} sources",
        "data": {"run_id": run_id, "sequence": event.sequence, "verified": evidence_result},
    }


async def append_draft_chunk(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Stream a draft content chunk."""
    from app.services.external_agent.run_service import add_event, get_run

    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        return {"tool": "append_draft_chunk", "status": "skipped", "detail": "run_id is required"}

    run = get_run(db, run_id)
    if not run or run.project_id != project_id:
        return {"tool": "append_draft_chunk", "status": "skipped", "detail": "Run not found"}

    content = str(args.get("content") or "").strip()
    chunk_index = int(args.get("chunk_index", 0))

    event = add_event(
        db, run_id, "draft_chunk",
        status="ok",
        message=f"Chunk {chunk_index} ({len(content)} chars)",
        payload_json=json.dumps({
            "content": content[:5000],  # Truncate to spec limit
            "chunk_index": chunk_index,
        }, ensure_ascii=False),
    )
    if not event:
        return {"tool": "append_draft_chunk", "status": "skipped", "detail": "Run is terminal"}

    return {
        "tool": "append_draft_chunk",
        "status": "ok",
        "detail": f"Draft chunk {chunk_index}: {len(content)} chars",
        "data": {"run_id": run_id, "sequence": event.sequence},
    }


async def mark_draft_ready(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Signal that a draft is complete."""
    from app.services.external_agent.run_service import add_event, get_run

    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        return {"tool": "mark_draft_ready", "status": "skipped", "detail": "run_id is required"}

    run = get_run(db, run_id)
    if not run or run.project_id != project_id:
        return {"tool": "mark_draft_ready", "status": "skipped", "detail": "Run not found"}

    content_type = str(args.get("content_type") or "unknown").strip()
    summary = str(args.get("summary") or "").strip()

    event = add_event(
        db, run_id, "draft_ready",
        status="ok",
        message=f"Draft ready: {content_type}",
        payload_json=json.dumps({
            "content_type": content_type,
            "summary": summary[:1000],
        }, ensure_ascii=False),
    )
    if not event:
        return {"tool": "mark_draft_ready", "status": "skipped", "detail": "Run is terminal"}

    return {
        "tool": "mark_draft_ready",
        "status": "ok",
        "detail": f"Draft ready: {content_type}",
        "data": {"run_id": run_id, "sequence": event.sequence},
    }


async def finish_agent_run(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Signal run completion."""
    from app.services.external_agent.run_service import add_event, get_run, update_run_status

    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        return {"tool": "finish_agent_run", "status": "skipped", "detail": "run_id is required"}

    run = get_run(db, run_id)
    if not run or run.project_id != project_id:
        return {"tool": "finish_agent_run", "status": "skipped", "detail": "Run not found"}

    summary = str(args.get("summary") or "").strip()

    event = add_event(
        db, run_id, "run_finished",
        status="ok",
        message=summary[:500] or "Run completed",
        payload_json=json.dumps({"summary": summary[:1000]}, ensure_ascii=False) if summary else None,
    )

    return {
        "tool": "finish_agent_run",
        "status": "ok",
        "detail": "Run finished",
        "data": {"run_id": run_id, "status": "completed"},
    }
