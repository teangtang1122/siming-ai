"""Write request management for external Agent drafts.

Allows external Agents to request writes that require user confirmation
before being applied to project data.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import AgentRun, AgentRunEvent
from app.services.external_agent.run_service import add_event, get_run, update_run_status
from app.mcp.permissions import issue_confirmation_token

logger = logging.getLogger(__name__)

# Supported write types
WRITE_TYPES = {
    "create_chapter",
    "update_chapter",
    "create_outline",
    "update_outline",
    "create_character",
    "update_character",
    "create_worldbuilding",
    "update_worldbuilding",
}


def request_write(
    db: Session,
    run_id: str,
    write_type: str,
    payload_summary: str,
    *,
    payload_json: str | None = None,
) -> dict[str, Any]:
    """Request a write that requires user confirmation.

    Creates a pending write request event. The frontend shows the request
    to the user for confirmation.

    Returns:
        dict with status, request_id (event sequence), and detail.
    """
    run = get_run(db, run_id)
    if not run:
        return {"status": "error", "detail": "Run not found"}

    if run.status in ("completed", "failed", "cancelled"):
        return {"status": "error", "detail": "Run is terminal"}

    if write_type not in WRITE_TYPES:
        return {"status": "error", "detail": f"Unsupported write type: {write_type}"}

    # Record the write_requested event
    event = add_event(
        db, run_id, "write_requested",
        status="ok",
        message=f"Write requested: {write_type}",
        payload_json=json.dumps({
            "write_type": write_type,
            "payload_summary": payload_summary[:500],
            "payload_json": payload_json[:5000] if payload_json else None,
        }, ensure_ascii=False),
    )

    if not event:
        return {"status": "error", "detail": "Cannot add event to terminal run"}

    update_run_status(
        db,
        run_id,
        "waiting_confirmation",
        current_step=f"Waiting for confirmation: {write_type}",
        summary=payload_summary[:500],
    )

    return {
        "status": "ok",
        "request_id": event.sequence,
        "detail": f"Write request created. Waiting for user confirmation.",
    }


def confirm_write(
    db: Session,
    run_id: str,
    request_id: int,
) -> dict[str, Any]:
    """Confirm a pending write request.

    Issues a confirmation token that the external Agent can use to
    execute the write through the MCP tool.

    Returns:
        dict with status, confirmation_token, and detail.
    """
    run = get_run(db, run_id)
    if not run:
        return {"status": "error", "detail": "Run not found"}

    # Find the write_requested event
    event = (
        db.query(AgentRunEvent)
        .filter(
            AgentRunEvent.run_id == run_id,
            AgentRunEvent.sequence == request_id,
            AgentRunEvent.event_type == "write_requested",
        )
        .first()
    )
    if not event:
        return {"status": "error", "detail": "Write request not found"}

    # Parse the payload to get the write type
    try:
        payload = json.loads(event.payload_json) if event.payload_json else {}
    except json.JSONDecodeError:
        payload = {}

    write_type = payload.get("write_type", "unknown")

    # Issue a confirmation token for the corresponding MCP tool
    # Map write_type to MCP tool name
    tool_map = {
        "create_chapter": "create_chapter",
        "update_chapter": "update_chapter",
        "create_outline": "create_outline_node",
        "update_outline": "update_outline_node",
        "create_character": "create_character",
        "update_character": "update_character",
        "create_worldbuilding": "create_worldbuilding_entry",
        "update_worldbuilding": "update_worldbuilding_entry",
    }
    tool_name = tool_map.get(write_type, write_type)
    token = issue_confirmation_token(tool_name)

    # Record the write_committed event
    add_event(
        db, run_id, "write_committed",
        status="ok",
        message=f"Write confirmed: {write_type}",
        payload_json=json.dumps({
            "write_type": write_type,
            "tool": tool_name,
        }, ensure_ascii=False),
    )

    update_run_status(
        db,
        run_id,
        "running",
        current_step=f"Executing confirmed write: {write_type}",
        summary=f"Write confirmed: {write_type}",
    )

    return {
        "status": "ok",
        "confirmation_token": token,
        "tool": tool_name,
        "detail": f"Confirmation token issued for {tool_name}",
    }


def reject_write(
    db: Session,
    run_id: str,
    request_id: int,
    *,
    reason: str = "",
) -> dict[str, Any]:
    """Reject a pending write request.

    Records a rejection event and does not modify project data.

    Returns:
        dict with status and detail.
    """
    run = get_run(db, run_id)
    if not run:
        return {"status": "error", "detail": "Run not found"}

    # Find the write_requested event
    event = (
        db.query(AgentRunEvent)
        .filter(
            AgentRunEvent.run_id == run_id,
            AgentRunEvent.sequence == request_id,
            AgentRunEvent.event_type == "write_requested",
        )
        .first()
    )
    if not event:
        return {"status": "error", "detail": "Write request not found"}

    # Record the rejection event
    add_event(
        db, run_id, "write_committed",
        status="skipped",
        message=f"Write rejected: {reason}" if reason else "Write rejected by user",
        payload_json=json.dumps({
            "rejected": True,
            "reason": reason[:200],
        }, ensure_ascii=False),
    )

    update_run_status(
        db,
        run_id,
        "running",
        current_step="Write rejected, continuing...",
        summary=reason[:200] if reason else "Write rejected by user",
    )

    return {
        "status": "ok",
        "detail": "Write rejected",
    }
