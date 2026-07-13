"""Workspace/MCP wrappers around the shared context orchestrator."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....database.models import AgentRun
from ....services.context_orchestrator import ContextOrchestrator


def _manifest_id_from_args(db: Session, project_id: str, args: dict[str, Any]) -> str:
    manifest_id = str(args.get("context_manifest_id") or args.get("manifest_id") or "").strip()
    if manifest_id:
        return manifest_id
    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        return ""
    run = db.query(AgentRun).filter(AgentRun.id == run_id, AgentRun.project_id == project_id).first()
    return str(run.context_manifest_id or "") if run else ""


async def prepare_task_context(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    """Prepare a baseline manifest for a local CLI or MCP Agent task."""
    orchestrator = ContextOrchestrator(db)
    task_type = str(args.get("task_type") or "writing").strip()
    run_id = str(args.get("run_id") or "").strip()
    run = db.query(AgentRun).filter(AgentRun.id == run_id, AgentRun.project_id == project_id).first() if run_id else None
    task_arguments = args.get("arguments") if isinstance(args.get("arguments"), dict) else args
    requested_manifest_id = str(args.get("context_manifest_id") or args.get("manifest_id") or "").strip()
    manifest = orchestrator.get_manifest(requested_manifest_id, project_id) if requested_manifest_id else None
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
    if manifest is None and run and run.context_manifest_id and not has_scoped_target:
        candidate = orchestrator.get_manifest(str(run.context_manifest_id), project_id)
        if candidate and candidate.task_type == task_type:
            manifest = candidate
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
    return {
        "tool": "prepare_task_context",
        "status": manifest.status,
        "detail": "Task context prepared." if manifest.status == "ready" else "Task context requires confirmation or rebuild completion.",
        "data": {
            "manifest_id": manifest.id,
            "context_manifest": orchestrator.manifest_payload(manifest, include_content=True),
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
    rows = orchestrator.search_task_context(manifest, query=query, limit=max(1, min(int(args.get("limit") or 12), 40)))
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
    sources = args.get("sources") if isinstance(args.get("sources"), list) else []
    result = orchestrator.submit_evidence(manifest, sources)
    status = "ok" if result["accepted_count"] else "needs_confirmation"
    return {
        "tool": "submit_context_evidence",
        "status": status,
        "detail": f"Verified {result['accepted_count']} context evidence source(s).",
        "data": {"manifest_id": manifest.id, **result},
    }
