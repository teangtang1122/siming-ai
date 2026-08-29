"""Deterministic turn boundaries for workspace assistant tools."""
from __future__ import annotations

from enum import StrEnum
from typing import Any


class AssistantTurnDirective(StrEnum):
    """Server-owned control flow that the model cannot override."""

    CONTINUE = "continue"
    END_AFTER_DRAFT = "end_after_draft"
    END_AFTER_OUTLINE_DRAFT = "end_after_outline_draft"
    BLOCKED_ON_OUTLINE_DRAFT = "blocked_on_outline_draft"
    BLOCKED_ON_CATALOGING = "blocked_on_cataloging"


def apply_turn_directive(
    result: dict[str, Any],
    directive: AssistantTurnDirective,
) -> dict[str, Any]:
    result["turn_directive"] = directive.value
    result["turn_terminal"] = directive is not AssistantTurnDirective.CONTINUE
    return result


def _directive(result: dict[str, Any] | None) -> str:
    if not isinstance(result, dict):
        return AssistantTurnDirective.CONTINUE
    return str(result.get("turn_directive") or AssistantTurnDirective.CONTINUE)


def is_terminal_tool_result(result: dict[str, Any] | None) -> bool:
    return _directive(result) in {
        AssistantTurnDirective.END_AFTER_DRAFT,
        AssistantTurnDirective.END_AFTER_OUTLINE_DRAFT,
        AssistantTurnDirective.BLOCKED_ON_OUTLINE_DRAFT,
        AssistantTurnDirective.BLOCKED_ON_CATALOGING,
    }


def terminal_reply(result: dict[str, Any]) -> str:
    if _directive(result) == AssistantTurnDirective.BLOCKED_ON_CATALOGING:
        return (
            str(result.get("detail") or "上一章尚未完成建档，本轮未生成下一章。")
            + " 请先保存当前草稿并完成建档；聊天模型不会在后台等待或轮询。"
        )

    if _directive(result) == AssistantTurnDirective.END_AFTER_DRAFT:
        return (
            "章节草稿已生成并载入正文编辑器，尚未保存。"
            "你可以先使用“去除 AI 味”或“质量评分”；确认后请选择“保存并建档”或“仅保存”。"
            "建档完成前，AI 不会继续生成下一章。"
        )

    if _directive(result) == AssistantTurnDirective.END_AFTER_OUTLINE_DRAFT:
        return (
            "大纲草稿已生成并显示在大纲页，尚未写入正式大纲。"
            "请先编辑或确认；只有作者确认后才能据此发起新的写章请求。"
        )

    if _directive(result) == AssistantTurnDirective.BLOCKED_ON_OUTLINE_DRAFT:
        return "已有一份大纲草稿等待处理；请先确认、重新规划或丢弃。"

    return str(result.get("detail") or "本轮已结束。")


__all__ = [
    "AssistantTurnDirective",
    "apply_turn_directive",
    "is_terminal_tool_result",
    "terminal_reply",
]
