"""Natural idempotency keys and duplicate-write detection."""
from __future__ import annotations

import hashlib
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
    """Generate a stable key for idempotent workspace writes."""
    if tool == "create_chapter":
        key = str(args.get("outline_node_id") or args.get("title") or "").strip()
        return f"create_chapter:{project_id}:{key}" if key else None

    if tool == "create_character":
        key = str(args.get("name") or "").strip()
        return f"create_character:{project_id}:{key}" if key else None

    if tool == "create_outline_node":
        parent = str(args.get("parent_id") or "").strip()
        title = str(args.get("title") or "").strip()
        key = f"{parent}:{title}" if parent else title
        return f"create_outline_node:{project_id}:{key}" if key else None

    if tool == "create_outline_nodes":
        nodes = args.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            return None
        natural_keys: list[str] = []
        default_parent = str(args.get("parent_id") or "").strip()
        for item in nodes[:8]:
            if not isinstance(item, dict):
                continue
            parent = str(item.get("parent_id") or default_parent).strip()
            title = str(item.get("title") or "").strip()
            if title:
                natural_keys.append(f"{parent}:{title}" if parent else title)
        if not natural_keys:
            return None
        digest = hashlib.sha256("|".join(natural_keys).encode("utf-8")).hexdigest()[:16]
        return f"create_outline_nodes:{project_id}:{digest}"

    if tool == "create_worldbuilding_entry":
        dimension = str(args.get("dimension") or "").strip()
        title = str(args.get("title") or "").strip()
        key = f"{dimension}:{title}"
        return f"create_worldbuilding_entry:{project_id}:{key}" if title else None

    if tool == "create_relationship":
        source = str(args.get("source") or args.get("from") or "").strip()
        target = str(args.get("target") or args.get("to") or "").strip()
        if not source or not target:
            return None
        from .utils import find_character_by_name_or_id

        source_character = find_character_by_name_or_id(db, project_id, source)
        target_character = find_character_by_name_or_id(db, project_id, target)
        if source_character and target_character:
            first, second = sorted([source_character.id, target_character.id])
            return f"create_relationship:{project_id}:{first}:{second}"
        first, second = sorted([source.lower(), target.lower()])
        return f"create_relationship:{project_id}:{first}:{second}"

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

    result = {}
    if existing.result_json:
        with suppress(Exception):
            result = json.loads(existing.result_json)
    return {
        "tool": existing.tool or "",
        "status": "ok",
        "detail": "已存在，跳过重复创建",
        "data": result.get("data"),
    }


__all__ = ["check_idempotency", "generate_idempotency_key"]
