"""API router for external Agent runs."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.response import ApiResponse
from ..core.db_helpers import get_project_or_404
from ..database.session import get_db
from ..database.models import AgentRun, AgentRunEvent
from ..schemas.agent_run import (
    AgentRunCreate,
    AgentRunRead,
    AgentRunEventCreate,
    AgentRunEventRead,
    AgentRunListResponse,
    AgentRunEventListResponse,
)
from ..schemas.external_agent_settings import ExternalAgentSettingsUpdate
from ..services.external_agent.run_service import (
    create_run,
    get_run,
    list_runs,
    add_event,
    get_events,
    cancel_run,
    update_run_status,
)

router = APIRouter(prefix="/projects/{project_id}/agent-runs", tags=["external-agent"])


def _run_to_read(run: AgentRun) -> AgentRunRead:
    return AgentRunRead(
        id=run.id,
        project_id=run.project_id,
        source=run.source,
        client_name=run.client_name,
        title=run.title,
        status=run.status,
        current_step=run.current_step,
        summary=run.summary,
        created_at=run.created_at,
        updated_at=run.updated_at,
        completed_at=run.completed_at,
    )


def _event_to_read(event: AgentRunEvent) -> AgentRunEventRead:
    return AgentRunEventRead(
        id=event.id,
        run_id=event.run_id,
        sequence=event.sequence,
        event_type=event.event_type,
        status=event.status,
        message=event.message,
        payload_json=event.payload_json,
        created_at=event.created_at,
    )


@router.post("")
def create_agent_run(
    project_id: str,
    body: AgentRunCreate,
    db: Session = Depends(get_db),
):
    """Create a new Agent run."""
    get_project_or_404(db, project_id)
    run = create_run(
        db, project_id,
        source=body.source,
        client_name=body.client_name,
        title=body.title,
    )
    return ApiResponse.success(data=_run_to_read(run).model_dump(), message="Run created")


@router.get("")
def list_agent_runs(
    project_id: str,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """List Agent runs for a project."""
    get_project_or_404(db, project_id)
    runs = list_runs(db, project_id, status=status)
    return ApiResponse.success(data={
        "items": [_run_to_read(r).model_dump() for r in runs],
        "total": len(runs),
    })


@router.get("/{run_id}")
def get_agent_run(
    project_id: str,
    run_id: str,
    db: Session = Depends(get_db),
):
    """Get a single Agent run."""
    get_project_or_404(db, project_id)
    run = get_run(db, run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    return ApiResponse.success(data=_run_to_read(run).model_dump())


@router.get("/{run_id}/events")
def get_agent_run_events(
    project_id: str,
    run_id: str,
    after_sequence: int = 0,
    db: Session = Depends(get_db),
):
    """Get events for an Agent run."""
    get_project_or_404(db, project_id)
    run = get_run(db, run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    events = get_events(db, run_id, after_sequence=after_sequence)
    return ApiResponse.success(data={
        "items": [_event_to_read(e).model_dump() for e in events],
        "total": len(events),
    })


@router.post("/{run_id}/events")
def add_agent_run_event(
    project_id: str,
    run_id: str,
    body: AgentRunEventCreate,
    db: Session = Depends(get_db),
):
    """Add an event to an Agent run."""
    get_project_or_404(db, project_id)
    run = get_run(db, run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    event = add_event(
        db, run_id, body.event_type,
        status=body.status,
        message=body.message,
        payload_json=body.payload_json,
    )
    if not event:
        raise HTTPException(status_code=400, detail="Cannot add event to terminal run")
    return ApiResponse.success(data=_event_to_read(event).model_dump(), message="Event added")


@router.get("/{run_id}/stream")
async def stream_agent_run_events(
    project_id: str,
    run_id: str,
    db: Session = Depends(get_db),
):
    """SSE stream for Agent run events."""
    get_project_or_404(db, project_id)
    run = get_run(db, run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")

    async def event_generator():
        # Send existing events first
        last_seq = 0
        events = get_events(db, run_id)
        for event in events:
            payload = _event_to_read(event).model_dump()
            # Convert datetime to string for JSON
            for k, v in payload.items():
                if hasattr(v, 'isoformat'):
                    payload[k] = v.isoformat()
            yield f"event: agent_run_event\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            last_seq = event.sequence

        # Poll for new events
        while True:
            await asyncio.sleep(1)
            # Re-query run status
            db.refresh(run)
            new_events = get_events(db, run_id, after_sequence=last_seq)
            for event in new_events:
                payload = _event_to_read(event).model_dump()
                for k, v in payload.items():
                    if hasattr(v, 'isoformat'):
                        payload[k] = v.isoformat()
                yield f"event: agent_run_event\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                last_seq = event.sequence

            # Stop if run is terminal
            if run.status in ("completed", "failed", "cancelled"):
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{run_id}/cancel")
def cancel_agent_run(
    project_id: str,
    run_id: str,
    db: Session = Depends(get_db),
):
    """Cancel an Agent run."""
    get_project_or_404(db, project_id)
    run = get_run(db, run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    cancelled = cancel_run(db, run_id)
    return ApiResponse.success(data=_run_to_read(cancelled).model_dump(), message="Run cancelled")


# ── Write request endpoints ──────────────────────────────────────────────

class WriteRequestCreate(BaseModel):
    write_type: str
    payload_summary: str
    payload_json: str | None = None


class WriteConfirmResponse(BaseModel):
    confirmation_token: str
    tool: str


@router.post("/{run_id}/write-requests")
def request_agent_write(
    project_id: str,
    run_id: str,
    body: WriteRequestCreate,
    db: Session = Depends(get_db),
):
    """Request a write that requires user confirmation."""
    get_project_or_404(db, project_id)
    run = get_run(db, run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")

    from app.services.external_agent.write_requests import request_write
    result = request_write(
        db, run_id, body.write_type,
        body.payload_summary,
        payload_json=body.payload_json,
    )
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["detail"])
    return ApiResponse.success(data=result, message="Write request created")


@router.post("/{run_id}/write-requests/{request_id}/confirm")
def confirm_agent_write(
    project_id: str,
    run_id: str,
    request_id: int,
    db: Session = Depends(get_db),
):
    """Confirm a pending write request."""
    get_project_or_404(db, project_id)
    run = get_run(db, run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")

    from app.services.external_agent.write_requests import confirm_write
    result = confirm_write(db, run_id, request_id)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["detail"])
    return ApiResponse.success(data=result, message="Write confirmed")


@router.post("/{run_id}/write-requests/{request_id}/reject")
def reject_agent_write(
    project_id: str,
    run_id: str,
    request_id: int,
    reason: str = "",
    db: Session = Depends(get_db),
):
    """Reject a pending write request."""
    get_project_or_404(db, project_id)
    run = get_run(db, run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")

    from app.services.external_agent.write_requests import reject_write
    result = reject_write(db, run_id, request_id, reason=reason)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["detail"])
    return ApiResponse.success(data=result, message="Write rejected")


# ── Settings endpoints ───────────────────────────────────────────────────

@router.get("/settings")
def get_external_agent_settings(
    project_id: str,
    db: Session = Depends(get_db),
):
    """Get external Agent permission settings for a project."""
    get_project_or_404(db, project_id)

    from app.database.models import ExternalAgentSettings
    from app.schemas.external_agent_settings import (
        DEFAULT_ENABLED_PACKS, DEFAULT_TRUSTED_LOCAL_ENABLED,
        DEFAULT_TRUSTED_LOCAL_CLIENTS, DEFAULT_REQUIRE_CONFIRMATION_FOR_WRITES,
        DEFAULT_REQUIRE_CONFIRMATION_FOR_DESTRUCTIVE,
    )

    settings = db.query(ExternalAgentSettings).filter(
        ExternalAgentSettings.project_id == project_id
    ).first()

    if not settings:
        # Return defaults
        return ApiResponse.success(data={
            "project_id": project_id,
            "enabled_packs": DEFAULT_ENABLED_PACKS,
            "trusted_local_enabled": DEFAULT_TRUSTED_LOCAL_ENABLED,
            "trusted_local_clients": DEFAULT_TRUSTED_LOCAL_CLIENTS,
            "require_confirmation_for_writes": DEFAULT_REQUIRE_CONFIRMATION_FOR_WRITES,
            "require_confirmation_for_destructive": DEFAULT_REQUIRE_CONFIRMATION_FOR_DESTRUCTIVE,
        })

    return ApiResponse.success(data={
        "id": settings.id,
        "project_id": settings.project_id,
        "enabled_packs": settings.enabled_packs or DEFAULT_ENABLED_PACKS,
        "trusted_local_enabled": settings.trusted_local_enabled or False,
        "trusted_local_clients": settings.trusted_local_clients or [],
        "require_confirmation_for_writes": settings.require_confirmation_for_writes if settings.require_confirmation_for_writes is not None else True,
        "require_confirmation_for_destructive": settings.require_confirmation_for_destructive if settings.require_confirmation_for_destructive is not None else True,
    })


@router.put("/settings")
def update_external_agent_settings(
    project_id: str,
    body: ExternalAgentSettingsUpdate,
    db: Session = Depends(get_db),
):
    """Update external Agent permission settings for a project."""
    get_project_or_404(db, project_id)

    from app.database.models import ExternalAgentSettings
    from app.schemas.external_agent_settings import (
        DEFAULT_ENABLED_PACKS, DEFAULT_TRUSTED_LOCAL_CLIENTS,
    )

    settings = db.query(ExternalAgentSettings).filter(
        ExternalAgentSettings.project_id == project_id
    ).first()

    if not settings:
        settings = ExternalAgentSettings(project_id=project_id)
        db.add(settings)

    if body.enabled_packs is not None:
        settings.enabled_packs = body.enabled_packs
    if body.trusted_local_enabled is not None:
        settings.trusted_local_enabled = body.trusted_local_enabled
    if body.trusted_local_clients is not None:
        settings.trusted_local_clients = body.trusted_local_clients
    if body.require_confirmation_for_writes is not None:
        settings.require_confirmation_for_writes = body.require_confirmation_for_writes
    if body.require_confirmation_for_destructive is not None:
        settings.require_confirmation_for_destructive = body.require_confirmation_for_destructive

    from datetime import datetime
    settings.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(settings)

    return ApiResponse.success(data={
        "id": settings.id,
        "project_id": settings.project_id,
        "enabled_packs": settings.enabled_packs or DEFAULT_ENABLED_PACKS,
        "trusted_local_enabled": settings.trusted_local_enabled or False,
        "trusted_local_clients": settings.trusted_local_clients or [],
        "require_confirmation_for_writes": settings.require_confirmation_for_writes if settings.require_confirmation_for_writes is not None else True,
        "require_confirmation_for_destructive": settings.require_confirmation_for_destructive if settings.require_confirmation_for_destructive is not None else True,
    }, message="Settings updated")


# ── Global settings endpoints ────────────────────────────────────────────

@router.get("/global-settings")
def get_global_settings(db: Session = Depends(get_db)):
    """Get global external Agent permission settings."""
    from app.database.models import ExternalAgentGlobalSettings

    settings = db.query(ExternalAgentGlobalSettings).first()
    if not settings:
        return ApiResponse.success(data={
            "enabled_packs": DEFAULT_ENABLED_PACKS,
            "trusted_local_enabled": False,
            "trusted_local_clients": [],
            "require_confirmation_for_writes": True,
            "require_confirmation_for_destructive": True,
            "mcp_permission_source": "global_settings",
        })

    return ApiResponse.success(data={
        "id": settings.id,
        "enabled_packs": settings.enabled_packs or DEFAULT_ENABLED_PACKS,
        "trusted_local_enabled": settings.trusted_local_enabled or False,
        "trusted_local_clients": settings.trusted_local_clients or [],
        "require_confirmation_for_writes": settings.require_confirmation_for_writes if settings.require_confirmation_for_writes is not None else True,
        "require_confirmation_for_destructive": settings.require_confirmation_for_destructive if settings.require_confirmation_for_destructive is not None else True,
        "mcp_permission_source": settings.mcp_permission_source or "global_settings",
    })


@router.put("/global-settings")
def update_global_settings(
    body: ExternalAgentSettingsUpdate,
    db: Session = Depends(get_db),
):
    """Update global external Agent permission settings."""
    from app.database.models import ExternalAgentGlobalSettings

    settings = db.query(ExternalAgentGlobalSettings).first()
    if not settings:
        settings = ExternalAgentGlobalSettings()
        db.add(settings)

    if body.enabled_packs is not None:
        settings.enabled_packs = body.enabled_packs
    if body.trusted_local_enabled is not None:
        settings.trusted_local_enabled = body.trusted_local_enabled
    if body.trusted_local_clients is not None:
        settings.trusted_local_clients = body.trusted_local_clients
    if body.require_confirmation_for_writes is not None:
        settings.require_confirmation_for_writes = body.require_confirmation_for_writes
    if body.require_confirmation_for_destructive is not None:
        settings.require_confirmation_for_destructive = body.require_confirmation_for_destructive

    from datetime import datetime
    settings.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(settings)

    return ApiResponse.success(data={
        "id": settings.id,
        "enabled_packs": settings.enabled_packs or DEFAULT_ENABLED_PACKS,
        "trusted_local_enabled": settings.trusted_local_enabled or False,
        "trusted_local_clients": settings.trusted_local_clients or [],
        "require_confirmation_for_writes": settings.require_confirmation_for_writes if settings.require_confirmation_for_writes is not None else True,
        "require_confirmation_for_destructive": settings.require_confirmation_for_destructive if settings.require_confirmation_for_destructive is not None else True,
        "mcp_permission_source": settings.mcp_permission_source or "global_settings",
    }, message="Global settings updated")


@router.get("/effective-permissions")
def get_effective_permissions(
    project_id: str | None = None,
    db: Session = Depends(get_db),
):
    """Get effective permissions for a project (global + project override)."""
    from app.database.models import ExternalAgentGlobalSettings, ExternalAgentSettings

    global_settings = db.query(ExternalAgentGlobalSettings).first()
    global_packs = (global_settings.enabled_packs if global_settings and global_settings.enabled_packs else DEFAULT_ENABLED_PACKS)

    project_packs = None
    if project_id:
        project_settings = db.query(ExternalAgentSettings).filter(
            ExternalAgentSettings.project_id == project_id,
        ).first()
        if project_settings and project_settings.enabled_packs:
            project_packs = project_settings.enabled_packs

    # Effective pack is the highest enabled pack
    pack_order = [
        "readonly_collaboration",
        "draft_generation",
        "project_writing",
        "project_management",
        "trusted_local_maintenance",
    ]
    effective_packs = project_packs or global_packs
    max_level = 0
    for pack in effective_packs:
        try:
            level = pack_order.index(pack)
            max_level = max(max_level, level)
        except ValueError:
            continue
    effective_pack = pack_order[max_level]

    warnings = []
    if global_settings and global_settings.mcp_permission_source == "cli_override":
        warnings.append("MCP permission source is CLI override. UI settings may not apply.")

    return ApiResponse.success(data={
        "global_enabled_packs": global_packs,
        "project_enabled_packs": project_packs,
        "effective_pack": effective_pack,
        "source": "project_override" if project_packs else "global_settings",
        "cli_override": (global_settings.mcp_permission_source == "cli_override") if global_settings else False,
        "warnings": warnings,
    })
