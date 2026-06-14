"""Workspace tool to launch a local CLI agent worker."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....services.local_cli_agent_worker import start_local_cli_agent_worker


async def start_local_cli_agent_run(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Start Claude/Codex/opencode as a Moshu-managed CLI Agent worker."""
    task_type = str(args.get("task_type") or args.get("mode") or "general").strip().lower()
    if task_type not in {"general", "cataloging", "writing"}:
        task_type = "general"
    user_request = str(args.get("user_request") or args.get("request") or "").strip()
    provider = str(args.get("provider") or "").strip() or None
    result = start_local_cli_agent_worker(
        db,
        project_id,
        user_request=user_request,
        task_type=task_type,
        provider=provider,
    )
    return {
        "tool": "start_local_cli_agent_run",
        "status": result.get("status", "ok"),
        "detail": result.get("detail", ""),
        "data": result.get("data"),
    }
