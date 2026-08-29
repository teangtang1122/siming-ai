"""Workspace/MCP wrappers around the shared context orchestrator."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....database.models import AgentRun
from ....services.context_orchestrator import ContextOrchestrator
from ....services.task_context_selection import (
    MODEL_SELECTED_TASK_TYPES,
    render_generation_context,
)


def _manifest_id_from_args(db: Session, project_id: str, args: dict[str, Any]) -> str:
    manifest_id = str(args.get("context_manifest_id") or args.get("manifest_id") or "").strip()
    run_id = str(args.get("run_id") or "").strip()
    run = (
        db.query(AgentRun)
        .filter(AgentRun.id == run_id, AgentRun.project_id == project_id)
        .first()
        if run_id
        else None
    )
    run_manifest_id = str(run.context_manifest_id or "").strip() if run else ""

    # Prefer a valid explicit ID.  If a CLI model copied or fabricated an
    # invalid UUID, recover through the authoritative manifest bound to its
    # run instead of trapping the task in a retry loop.
    if manifest_id:
        if ContextOrchestrator(db).get_manifest(manifest_id, project_id):
            return manifest_id
        if run_manifest_id:
            return run_manifest_id
        return manifest_id
    return run_manifest_id


async def prepare_task_context(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    """Prepare a compact baseline manifest for a local CLI or MCP Agent task."""
    orchestrator = ContextOrchestrator(db)
    task_type = str(args.get("task_type") or "writing").strip()
    run_id = str(args.get("run_id") or "").strip()
    run = db.query(AgentRun).filter(AgentRun.id == run_id, AgentRun.project_id == project_id).first() if run_id else None
    task_arguments = args.get("arguments") if isinstance(args.get("arguments"), dict) else args
    requested_manifest_id = str(args.get("context_manifest_id") or args.get("manifest_id") or "").strip()
    manifest = orchestrator.get_manifest(requested_manifest_id, project_id) if requested_manifest_id else None
    run_manifest = (
        orchestrator.get_manifest(str(run.context_manifest_id), project_id)
        if run and run.context_manifest_id
        else None
    )
    if requested_manifest_id and manifest is None and run_manifest is not None:
        manifest = run_manifest
    if requested_manifest_id and manifest is None:
        return {
            "tool": "prepare_task_context",
            "status": "needs_confirmation",
            "detail": "The requested context manifest was not found for this project.",
            "data": {"manifest_id": requested_manifest_id},
        }

    # A long-running cataloging Agent needs a distinct, auditable baseline for
    # each claimed chapter. Reusing its previous run-level manifest would make
    # the evidence chain point at the wrong chapter after the first iteration.
    scoped_target_keys = {
        "chapter_id", "target_chapter_id", "outline_node_id", "target_outline_id",
        "target_text", "chapter_text", "content", "text",
    }
    has_scoped_target = any(
        key in task_arguments and task_arguments.get(key) not in (None, "", [], {})
        for key in scoped_target_keys
    )
    if manifest is None and run_manifest and not has_scoped_target:
        if run_manifest.task_type == task_type:
            manifest = run_manifest
    if manifest is None:
        manifest = orchestrator.prepare(
            project_id=project_id,
            task_type=task_type,
            model=str(args.get("model") or "") or None,
            execution_route=str(args.get("execution_route") or "external_mcp")[:50],
            arguments=task_arguments,
            session_id=str(args.get("session_id") or "") or None,
            pinned_chunk_ids=args.get("pinned_chunk_ids") if isinstance(args.get("pinned_chunk_ids"), list) else (),
            pinned_source_ids=args.get("pinned_source_ids") if isinstance(args.get("pinned_source_ids"), list) else (),
        )
    if run:
        run.context_manifest_id = manifest.id
    payload = orchestrator.manifest_payload(manifest, include_content=False)
    return {
        "tool": "prepare_task_context",
        "status": manifest.status,
        "detail": (
            "Compact task anchors prepared; search as needed and finalize exact evidence before generation."
            if manifest.status == "ready" and manifest.task_type in MODEL_SELECTED_TASK_TYPES
            else "Task context prepared."
            if manifest.status == "ready"
            else "Task context requires confirmation or rebuild completion."
        ),
        "data": {
            "manifest_id": manifest.id,
            "context_manifest_id": manifest.id,
            "context_manifest": payload,
            "baseline_context": (
                render_generation_context(manifest)
                if manifest.task_type in MODEL_SELECTED_TASK_TYPES
                else manifest.rendered_context
            ),
            "selection_required": manifest.task_type in MODEL_SELECTED_TASK_TYPES,
            "next_tools": (
                ["search_task_context", "submit_context_evidence"]
                if manifest.task_type in MODEL_SELECTED_TASK_TYPES
                else []
            ),
        },
    }


async def search_task_context(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    """Search a prepared task context and issue verifiable result evidence."""
    manifest_id = _manifest_id_from_args(db, project_id, args)
    if not manifest_id:
        return {"tool": "search_task_context", "status": "skipped", "detail": "context_manifest_id or run_id is required", "data": {"items": []}}
    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.get_manifest(manifest_id, project_id)
    if not manifest:
        return {"tool": "search_task_context", "status": "skipped", "detail": "Context manifest not found", "data": {"items": []}}
    query = str(args.get("query") or "").strip()
    if not query:
        return {"tool": "search_task_context", "status": "skipped", "detail": "query is required", "data": {"items": []}}
    usable, detail = orchestrator.validate(manifest)
    if not usable:
        return {
            "tool": "search_task_context",
            "status": manifest.status,
            "detail": detail,
            "data": {"manifest_id": manifest.id, "items": []},
        }
    source_types = (
        [str(value).strip() for value in args.get("source_types", []) if str(value).strip()]
        if isinstance(args.get("source_types"), list)
        else []
    )
    rows = orchestrator.search_task_context(
        manifest,
        query=query,
        limit=max(
            1,
            min(
                int(args.get("limit") or 12),
                20 if manifest.task_type in MODEL_SELECTED_TASK_TYPES else 40,
            ),
        ),
        source_types=source_types,
    )
    return {
        "tool": "search_task_context",
        "status": "ok",
        "detail": f"Verified task-context search returned {len(rows)} sources.",
        "data": {"manifest_id": manifest.id, "items": rows},
    }


async def submit_context_evidence(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    """Validate Agent-selected sources against its baseline manifest."""
    manifest_id = _manifest_id_from_args(db, project_id, args)
    if not manifest_id:
        return {"tool": "submit_context_evidence", "status": "skipped", "detail": "context_manifest_id or run_id is required", "data": {}}
    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.get_manifest(manifest_id, project_id)
    if not manifest:
        return {"tool": "submit_context_evidence", "status": "skipped", "detail": "Context manifest not found", "data": {}}
    usable, detail = orchestrator.validate(manifest)
    if not usable:
        return {
            "tool": "submit_context_evidence",
            "status": manifest.status,
            "detail": detail,
            "data": {"manifest_id": manifest.id},
        }
    sources = args.get("sources") if isinstance(args.get("sources"), list) else []
    result = orchestrator.submit_evidence(manifest, sources)
    if manifest.task_type in MODEL_SELECTED_TASK_TYPES:
        status = "ok" if result.get("selection_ready") else "needs_confirmation"
        detail = (
            f"Finalized {result['accepted_count']} exact task source(s). "
            "Use the returned context_selection_token in the next model step."
            if status == "ok"
            else "The proposed task evidence was not finalized; narrow or refresh the selection."
        )
    else:
        status = "ok" if result["accepted_count"] else "needs_confirmation"
        detail = f"Verified {result['accepted_count']} context evidence source(s)."
    return {
        "tool": "submit_context_evidence",
        "status": status,
        "detail": detail,
        "data": {"manifest_id": manifest.id, **result},
    }
