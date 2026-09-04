"""Detect durable author-review drafts committed by direct-MCP CLI turns."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .generated_drafts import (
    chapter_draft_result_data,
    find_chapter_draft,
)
from .outline_drafts import (
    find_outline_draft,
    outline_draft_result_data,
)
from .turn_control import AssistantTurnDirective, apply_turn_directive


def local_cli_terminal_draft(
    db: Any,
    project_id: str,
    run_id: str,
    iteration: int,
) -> tuple[dict[str, Any], str] | None:
    """Return a draft proven by this run iteration's declared output refs."""
    from app.database.models import AssistantRunStep

    steps = (
        db.query(AssistantRunStep)
        .filter(
            AssistantRunStep.run_id == run_id,
            AssistantRunStep.project_id == project_id,
            AssistantRunStep.iteration == iteration,
            AssistantRunStep.status.in_({"ok", "completed", "success", "succeeded"}),
            AssistantRunStep.tool.in_({
                "save_external_chapter_draft",
                "save_external_outline_draft",
            }),
        )
        .order_by(AssistantRunStep.completed_at.desc(), AssistantRunStep.id.desc())
        .all()
    )
    for step in steps:
        try:
            refs = json.loads(step.output_refs or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if step.tool == "save_external_chapter_draft":
            value = refs.get("chapter_draft") if isinstance(refs, dict) else None
            draft_id = str(value.get("id") or "") if isinstance(value, dict) else ""
            draft = find_chapter_draft(db, project_id, draft_id) if draft_id else None
            if draft is not None and str(draft.status or "") == "pending":
                return _chapter_terminal_result(draft)
        if step.tool == "save_external_outline_draft":
            value = refs.get("outline_draft") if isinstance(refs, dict) else None
            draft_id = str(value.get("id") or "") if isinstance(value, dict) else ""
            draft = find_outline_draft(db, project_id, draft_id) if draft_id else None
            if draft is not None and str(draft.status or "") == "pending":
                return _outline_terminal_result(draft)
    return None


def _chapter_terminal_result(draft: Any) -> tuple[dict[str, Any], str]:
    return (
        apply_turn_directive(
            {
                "tool": "save_external_chapter_draft",
                "status": "ok",
                "detail": "本机 CLI 已生成章节草稿，尚未保存",
                "data": chapter_draft_result_data(draft),
            },
            AssistantTurnDirective.END_AFTER_DRAFT,
        ),
        "章节草稿已生成，已到达服务端回合终止边界",
    )


def _outline_terminal_result(draft: Any) -> tuple[dict[str, Any], str]:
    return (
        apply_turn_directive(
            {
                "tool": "save_external_outline_draft",
                "status": "ok",
                "detail": "本机 CLI 已生成大纲草稿，尚未确认",
                "data": outline_draft_result_data(draft),
            },
            AssistantTurnDirective.END_AFTER_OUTLINE_DRAFT,
        ),
        "大纲草稿已生成，已到达服务端回合终止边界",
    )


def local_cli_terminal_draft_probe(
    runtime_body: dict[str, Any],
) -> Callable[[], str | None] | None:
    """Build the process monitor's durable terminal-draft activity probe."""
    project_id = str(runtime_body.get("local_cli_terminal_draft_project_id") or "").strip()
    run_id = str(runtime_body.get("local_cli_terminal_draft_run_id") or "").strip()
    try:
        iteration = int(runtime_body.get("local_cli_terminal_draft_iteration"))
    except (TypeError, ValueError):
        return None
    if not project_id or not run_id or iteration < 0:
        return None

    def _probe() -> str | None:
        from app.database.session import SessionLocal
        from app.services.workspace.registry import registry

        session = SessionLocal()
        try:
            detected = local_cli_terminal_draft(
                session,
                project_id,
                run_id,
                iteration,
            )
            if detected is None:
                return None
            result, _detail = detected
            tool_name = str(result.get("tool") or "")
            definition = registry.get(tool_name)
            if definition is None or not definition.ends_agent_turn:
                return None
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            draft_id = str(data.get("draft_id") or "committed")
            return f"{tool_name}:{draft_id}"
        finally:
            session.close()

    return _probe


__all__ = ["local_cli_terminal_draft", "local_cli_terminal_draft_probe"]
