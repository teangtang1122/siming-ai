"""Apply model-proposed prose edits without letting it replace a whole chapter."""

from __future__ import annotations

import json
from typing import Any

from ..core.exceptions import LLMError
from .de_ai_validation import count_de_ai_visible_characters


def apply_de_ai_edits(source: str, response: Any) -> tuple[str, int, int]:
    """Apply only unique, bounded, non-overlapping replacements from JSON."""

    raw = str(response or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise LLMError("去除 AI 味失败：模型没有返回合法的局部修订 JSON；原文未变") from exc
    edits = payload.get("edits") if isinstance(payload, dict) else None
    if not isinstance(edits, list) or len(edits) > 24:
        raise LLMError("去除 AI 味失败：模型返回的局部修订格式无效；原文未变")

    accepted: list[tuple[int, int, str]] = []
    rejected = 0
    maximum_span = max(120, min(250, len(source) // 5))
    for edit in edits:
        if not isinstance(edit, dict):
            rejected += 1
            continue
        old, new = edit.get("old"), edit.get("new")
        if not isinstance(old, str) or not isinstance(new, str):
            rejected += 1
            continue
        if (
            not old.strip()
            or not new.strip()
            or old == new
            or len(old) > maximum_span
            or source.count(old) != 1
            or new.count("\n") != old.count("\n")
            or count_de_ai_visible_characters(new)
            < count_de_ai_visible_characters(old) * 0.8
        ):
            rejected += 1
            continue
        start = source.index(old)
        end = start + len(old)
        if any(start < previous_end and previous_start < end for previous_start, previous_end, _ in accepted):
            rejected += 1
            continue
        accepted.append((start, end, new))

    if not accepted:
        raise LLMError("去除 AI 味失败：模型没有给出可定位且保留内容的修订；原文未变")
    revised = source
    for start, end, new in sorted(accepted, reverse=True):
        revised = revised[:start] + new + revised[end:]
    if count_de_ai_visible_characters(revised) < count_de_ai_visible_characters(source) * 0.9:
        raise LLMError("去除 AI 味失败：候选稿删减过多，已拒绝整批修订；原文未变")
    return revised, len(accepted), rejected
