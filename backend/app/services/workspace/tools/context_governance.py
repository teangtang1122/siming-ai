"""Workspace/MCP wrappers around the shared context orchestrator."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....database.models import AgentRun
from ....services.context_orchestrator import ContextOrchestrator
from ....services.task_context_delivery import (
    begin_context_delivery,
    build_context_page,
    compact_context_manifest,
    context_delivery_ready,
    context_delivery_state,
    context_delivery_status,
    context_page_arguments,
    context_selection_diagnostics,
    deliver_next_context_page,
)
from ....services.task_context_selection import (
    MODEL_SELECTED_TASK_TYPES,
    TASK_CONTEXT_SEARCH_MAX_CURSOR,
    TASK_CONTEXT_SEARCH_PAGE_LIMIT,
    TASK_CONTEXT_SEARCH_SOURCE_TYPES,
    render_generation_context,
)


def _manifest_id_from_args(db: Session, project_id: str, args: dict[str, Any]) -> str:
    manifest_id = str(args.get("context_manifest_id") or args.get("manifest_id") or "").strip()
    run_id = str(args.get("run_id") or "").strip()
    run = (
        db.query(AgentRun).filter(AgentRun.id == run_id, AgentRun.project_id == project_id).first()
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
    from ....services.chapter_writing_constraints import normalize_writing_arguments

    orchestrator = ContextOrchestrator(db)
    task_type = str(args.get("task_type") or "writing").strip()
    run_id = str(args.get("run_id") or "").strip()
    run = (
        db.query(AgentRun).filter(AgentRun.id == run_id, AgentRun.project_id == project_id).first()
        if run_id
        else None
    )
    # The public tool contract has one authoritative flat argument shape.  A
    # nested free-form `arguments` object hid required targets from native
    # models and created unusable manifests that were rolled back while their
    # IDs were still returned.
    try:
        task_arguments = (
            normalize_writing_arguments(args)
            if task_type == "writing"
            else dict(args)
        )
    except ValueError as error:
        return {
            "tool": "prepare_task_context",
            "status": "skipped",
            "detail": str(error),
            "data": {"task_type": task_type},
        }
    requested_manifest_id = str(
        args.get("context_manifest_id") or args.get("manifest_id") or ""
    ).strip()
    manifest = (
        orchestrator.get_manifest(requested_manifest_id, project_id)
        if requested_manifest_id
        else None
    )
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

    if manifest is None and task_type == "writing" and not str(
        task_arguments.get("outline_node_id") or ""
    ).strip():
        return {
            "tool": "prepare_task_context",
            "status": "needs_confirmation",
            "detail": "writing requires outline_node_id on the prepare_task_context call; no manifest was created.",
            "data": {
                "reason": "missing_task_anchor",
                "task_type": task_type,
                "required_arguments": ["outline_node_id"],
                "next_tool": "prepare_task_context",
            },
        }

    # A long-running cataloging Agent needs a distinct, auditable baseline for
    # each claimed chapter. Reusing its previous run-level manifest would make
    # the evidence chain point at the wrong chapter after the first iteration.
    scoped_target_keys = {
        "chapter_id",
        "target_chapter_id",
        "outline_node_id",
        "target_outline_id",
        "source_draft_id",
        "target_text",
        "chapter_text",
        "content",
        "text",
    }
    has_scoped_target = any(
        key in task_arguments and task_arguments.get(key) not in (None, "", [], {})
        for key in scoped_target_keys
    )
    if (
        manifest is None
        and run_manifest
        and not has_scoped_target
        and run_manifest.task_type == task_type
    ):
        manifest = run_manifest
    if manifest is None:
        manifest = orchestrator.prepare(
            project_id=project_id,
            task_type=task_type,
            model=str(args.get("model") or "") or None,
            execution_route=str(args.get("execution_route") or "external_mcp")[:50],
            arguments=task_arguments,
            session_id=str(args.get("session_id") or "") or None,
            pinned_chunk_ids=args.get("pinned_chunk_ids")
            if isinstance(args.get("pinned_chunk_ids"), list)
            else (),
            pinned_source_ids=args.get("pinned_source_ids")
            if isinstance(args.get("pinned_source_ids"), list)
            else (),
        )
    payload = orchestrator.manifest_payload(manifest, include_content=False)
    if manifest.status != "ready":
        missing_coverage = [
            name for name, value in (payload.get("coverage") or {}).items()
            if isinstance(value, dict) and value.get("status") == "missing"
        ]
        return {
            "tool": "prepare_task_context",
            "status": "needs_confirmation",
            "detail": "Task context anchors are invalid or incomplete; no reusable manifest ID was issued.",
            "data": {
                "reason": "invalid_task_anchor",
                "task_type": task_type,
                "missing_coverage": missing_coverage,
                "coverage": payload.get("coverage") or {},
                "warnings": payload.get("warnings") or [],
                "next_tool": "prepare_task_context",
            },
        }
    if run:
        run.context_manifest_id = manifest.id
    selection = payload.get("selection") or {}
    selected = selection.get("status") == "ready" and bool(selection.get("token"))
    needs_selection = manifest.task_type in MODEL_SELECTED_TASK_TYPES and not selected
    document = render_generation_context(manifest) if manifest.task_type in MODEL_SELECTED_TASK_TYPES else manifest.rendered_context
    selection_token = str(selection.get("token") or "")
    delivery_state = context_delivery_state(manifest)
    try:
        if selected:
            page, delivery_state = deliver_next_context_page(
                manifest,
                document,
                args,
                selection_token,
            )
        else:
            page = build_context_page(document, args)
    except ValueError as error:
        return {"tool": "prepare_task_context", "status": "skipped", "detail": str(error),
                "data": {"context_manifest_id": manifest.id}}
    db.flush()
    delivery_ready = selected and context_delivery_ready(manifest, selection_token)
    return {
        "tool": "prepare_task_context",
        "status": manifest.status,
        "detail": (
            "Compact task anchors prepared; search as needed and finalize "
            "exact evidence before generation."
            if manifest.status == "ready" and manifest.task_type in MODEL_SELECTED_TASK_TYPES
            else "Task context prepared."
            if manifest.status == "ready"
            else "Task context requires confirmation or rebuild completion."
        ),
        "data": {
            "manifest_id": manifest.id,
            "context_manifest_id": manifest.id,
            "context_manifest": compact_context_manifest(payload),
            "context_page": page,
            "context_selection_token": selection_token if delivery_ready else None,
            "context_delivery_ready": delivery_ready,
            "context_delivery": context_delivery_status(delivery_state),
            "selection_required": needs_selection,
            "next_tools": (
                ["prepare_task_context"] if page["has_more"]
                else ["search_task_context", "submit_context_evidence"]
                if needs_selection
                else []
            ),
            "next_arguments": context_page_arguments(manifest.id, manifest.task_type, page) if page["has_more"] else None,
        },
    }


async def search_task_context(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    """Search a prepared task context and issue verifiable result evidence."""
    manifest_id = _manifest_id_from_args(db, project_id, args)
    if not manifest_id:
        return {
            "tool": "search_task_context",
            "status": "skipped",
            "detail": "context_manifest_id or run_id is required",
            "data": {"items": []},
        }
    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.get_manifest(manifest_id, project_id)
    if not manifest:
        return {
            "tool": "search_task_context",
            "status": "skipped",
            "detail": "Context manifest not found",
            "data": {"items": []},
        }
    query = str(args.get("query") or "").strip()
    if not query:
        return {
            "tool": "search_task_context",
            "status": "skipped",
            "detail": "query is required",
            "data": {"items": []},
        }
    if len(query) > 500:
        return {
            "tool": "search_task_context",
            "status": "skipped",
            "detail": "query exceeds 500 characters; narrow the retrieval question",
            "data": {"items": []},
        }
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
    unsupported_source_types = sorted(
        set(source_types) - TASK_CONTEXT_SEARCH_SOURCE_TYPES
    )
    if unsupported_source_types:
        return {
            "tool": "search_task_context",
            "status": "skipped",
            "detail": (
                "Unsupported source_types: "
                + ", ".join(unsupported_source_types)
                + ". Use the exact singular values from the tool contract."
            ),
            "data": {
                "manifest_id": manifest.id,
                "items": [],
                "supported_source_types": sorted(TASK_CONTEXT_SEARCH_SOURCE_TYPES),
            },
        }
    requested_limit = int(args.get("limit") or TASK_CONTEXT_SEARCH_PAGE_LIMIT)
    page_limit = max(1, min(requested_limit, TASK_CONTEXT_SEARCH_PAGE_LIMIT))
    page_cursor = max(
        0,
        min(int(args.get("cursor") or 0), TASK_CONTEXT_SEARCH_MAX_CURSOR),
    )
    probed_rows = orchestrator.search_task_context(
        manifest,
        query=query,
        limit=page_limit,
        offset=page_cursor,
        source_types=source_types,
        include_next_probe=True,
    )
    rows = probed_rows[:page_limit]
    has_more = len(probed_rows) > page_limit
    return {
        "tool": "search_task_context",
        "status": "ok",
        "detail": f"Verified task-context search returned {len(rows)} sources.",
        "data": {
            "manifest_id": manifest.id,
            "items": rows,
            "page": {
                "cursor": page_cursor,
                "limit": page_limit,
                "next_cursor": page_cursor + len(rows) if has_more else None,
                "has_more": has_more,
            },
        },
    }


async def submit_context_evidence(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    """Validate Agent-selected sources against its baseline manifest."""
    manifest_id = _manifest_id_from_args(db, project_id, args)
    if not manifest_id:
        return {
            "tool": "submit_context_evidence",
            "status": "skipped",
            "detail": "context_manifest_id or run_id is required",
            "data": {},
        }
    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.get_manifest(manifest_id, project_id)
    if not manifest:
        return {
            "tool": "submit_context_evidence",
            "status": "skipped",
            "detail": "Context manifest not found",
            "data": {},
        }
    usable, detail = orchestrator.validate(manifest)
    if not usable:
        return {
            "tool": "submit_context_evidence",
            "status": manifest.status,
            "detail": detail,
            "data": {"manifest_id": manifest.id},
        }
    sources = args.get("sources")
    if not isinstance(sources, list) or any(not isinstance(item, dict) for item in sources):
        return {"tool": "submit_context_evidence", "status": "skipped",
                "detail": "sources must be a JSON array of objects, not an encoded string",
                "data": {"manifest_id": manifest.id}}
    result = orchestrator.submit_evidence(manifest, sources)
    if result.get("rejected"):
        result.update(context_selection_diagnostics(result["rejected"]))
    # Raw selector results serve internal generation too; only this tool
    # envelope replaces the full document with a lossless bounded page.
    result.pop("task_context", None)
    if result.get("selection_ready"):
        page = build_context_page(render_generation_context(manifest), {})
        selection_token = str(result.get("context_selection_token") or "")
        delivery_state = begin_context_delivery(manifest, page, selection_token)
        result["context_page"] = page
        result["context_delivery_ready"] = context_delivery_ready(manifest, selection_token)
        result["context_delivery"] = context_delivery_status(delivery_state)
        if page["has_more"]:
            # The token remains persisted with the selected evidence, but it is
            # deliberately absent from model-visible receipts until the final
            # contiguous page has been delivered.
            result.pop("context_selection_token", None)
            result["next_tool"] = "prepare_task_context"
            result["next_arguments"] = context_page_arguments(manifest.id, manifest.task_type, page)
        db.flush()
    if manifest.task_type in MODEL_SELECTED_TASK_TYPES:
        status = "ok" if result.get("selection_ready") else "needs_confirmation"
        detail = (
            f"Finalized {result['accepted_count']} exact task source(s). "
            + (
                "Read every context_page in order; context_selection_token is withheld until the final page."
                if not result.get("context_delivery_ready")
                else "The complete context document was delivered and generation may use the returned token."
            )
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
