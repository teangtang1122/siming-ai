"""Natural idempotency keys and duplicate-write detection."""
from __future__ import annotations

import json
from contextlib import suppress

from sqlalchemy.orm import Session

from ...database.models import AssistantRunStep


def generate_idempotency_key(
    db: Session,
    tool: str,
    project_id: str,
    args: dict,
) -> str | None:
    """Generate a stable key for idempotent non-prose workspace writes."""
    if tool == "create_character":
        key = str(args.get("name") or "").strip()
        return f"create_character:{project_id}:{key}" if key else None

    if tool == "create_worldbuilding_entry":
        dimension = str(args.get("dimension") or "").strip()
        title = str(args.get("title") or "").strip()
        key = f"{dimension}:{title}"
        return f"create_worldbuilding_entry:{project_id}:{key}" if title else None

    return None


def check_idempotency(
    db: Session,
    project_id: str,
    idempotency_key: str,
) -> dict | None:
    """Return a prior successful result for an identical write."""
    existing = (
        db.query(AssistantRunStep)
        .filter(
            AssistantRunStep.project_id == project_id,
            AssistantRunStep.idempotency_key == idempotency_key,
            AssistantRunStep.status == "ok",
        )
        .order_by(AssistantRunStep.completed_at.desc())
        .first()
    )
    if not existing:
        return None

    result: dict = {}
    if existing.result_json:
        with suppress(Exception):
            result = json.loads(existing.result_json)
    if not isinstance(result, dict):
        return None
    return {
        "tool": existing.tool or "",
        "status": "ok",
        "detail": "已存在，跳过重复创建",
        "data": result.get("data"),
    }


__all__ = ["check_idempotency", "generate_idempotency_key"]
