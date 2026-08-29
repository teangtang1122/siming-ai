"""Detect durable author-review drafts committed by direct-MCP CLI turns."""

from __future__ import annotations

from typing import Any

from .generated_drafts import (
    chapter_draft_result_data,
    find_new_pending_chapter_draft,
)
from .outline_drafts import (
    find_new_pending_outline_draft,
    outline_draft_result_data,
)
from .turn_control import AssistantTurnDirective, apply_turn_directive


def local_cli_terminal_draft(
    db: Any,
    project_id: str,
    chapter_excluded_ids: set[str],
    outline_excluded_ids: set[str],
) -> tuple[dict[str, Any], str] | None:
    """Return authoritative terminal evidence committed by a direct-MCP CLI."""
    chapter = find_new_pending_chapter_draft(
        db,
        project_id,
        chapter_excluded_ids,
    )
    if chapter is not None:
        return (
            apply_turn_directive(
                {
                    "tool": "save_external_chapter_draft",
                    "status": "ok",
                    "detail": "本机 CLI 已生成章节草稿，尚未保存",
                    "data": chapter_draft_result_data(chapter),
                },
                AssistantTurnDirective.END_AFTER_DRAFT,
            ),
            "章节草稿已生成，已到达服务端回合终止边界",
        )
    outline = find_new_pending_outline_draft(
        db,
        project_id,
        outline_excluded_ids,
    )
    if outline is None:
        return None
    return (
        apply_turn_directive(
            {
                "tool": "save_external_outline_draft",
                "status": "ok",
                "detail": "本机 CLI 已生成大纲草稿，尚未确认",
                "data": outline_draft_result_data(outline),
            },
            AssistantTurnDirective.END_AFTER_OUTLINE_DRAFT,
        ),
        "大纲草稿已生成，已到达服务端回合终止边界",
    )


__all__ = ["local_cli_terminal_draft"]
