"""Assistant intent detection and confirmation heuristics."""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.orm import Session

from .workspace import _find_outline_by_title_or_id


def _assistant_heuristic_plan(message: str) -> dict:
    text = message.lower()
    tools = {"read_recent_summaries", "read_outline", "read_worldbuilding", "read_characters", "read_relationships"}
    if any(key in text for key in ["矛盾", "冲突", "合理", "检查", "详细", "正文", "bug", "不一致"]):
        tools.add("read_chapter_detail")
    if any(key in text for key in ["写", "生成", "新章节", "创建章节", "对话", "扮演", "行动", "出场"]):
        tools.add("roleplay_characters")
    should_create = bool(
        any(key in text for key in ["创建章节", "新章节", "直接生成章节", "写一章", "写第", "帮我写第"])
        or re.search(r"写\s*第?\s*\d+\s*章", text)
        or re.search(r"第\s*\d+\s*章", text) and any(key in text for key in ["写", "生成", "创建"])
    )
    return {
        "intent": "write" if should_create else "advise",
        "tools": sorted(tools),
        "character_names": [],
        "needs_worldbuilding": any(key in text for key in ["设定", "世界观", "规则", "势力", "地图"]),
        "should_create_chapter": should_create,
        "chapter_title": _chapter_title_from_request(message) if should_create else "",
        "reason": "启发式计划",
    }


def _chapter_title_from_request(message: str) -> str:
    text = (message or "").strip()
    match = re.search(r"第\s*([0-9一二两三四五六七八九十百千万零〇]+)\s*章", text)
    if match:
        return f"第{match.group(1)}章"
    return "AI生成章节"


def _normalize_assistant_plan(raw_plan: Optional[dict], message: str) -> dict:
    fallback = _assistant_heuristic_plan(message)
    if not raw_plan:
        return fallback
    allowed_tools = {
        "read_recent_summaries",
        "read_outline",
        "read_worldbuilding",
        "read_characters",
        "read_relationships",
        "read_chapter_detail",
        "roleplay_characters",
    }
    tools = [tool for tool in raw_plan.get("tools") or [] if tool in allowed_tools]
    for tool in fallback["tools"]:
        if tool not in tools:
            tools.append(tool)
    names = [
        str(name).strip()
        for name in raw_plan.get("character_names") or []
        if str(name).strip()
    ][:6]
    return {
        "intent": str(raw_plan.get("intent") or fallback["intent"])[:50],
        "tools": tools,
        "character_names": names,
        "needs_worldbuilding": bool(raw_plan.get("needs_worldbuilding", fallback["needs_worldbuilding"])),
        "should_create_chapter": bool(raw_plan.get("should_create_chapter")) or bool(fallback["should_create_chapter"]),
        "chapter_title": str(raw_plan.get("chapter_title") or fallback.get("chapter_title") or _chapter_title_from_request(message) or "")[:200],
        "reason": str(raw_plan.get("reason") or fallback["reason"])[:500],
    }


def _is_affirmative_confirmation(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    if any(phrase in normalized for phrase in ["不是", "不行", "不要", "不按", "不可以", "否", "换个方向", "改一下"]):
        return False
    return any(
        phrase in normalized
        for phrase in [
            "是",
            "可以",
            "确认",
            "同意",
            "按这个",
            "就这样",
            "继续",
            "没问题",
            "照这个",
            "就按",
            "yes",
            "ok",
        ]
    ) or normalized in {"好", "好的", "行"}


def _user_requests_chapter_creation(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return any(
        phrase in normalized
        for phrase in ["写第", "写一章", "写新章", "新章节", "创建章节", "生成章节", "帮我写", "开始写", "续写第"]
    )


def _chapter_action_needs_outline_confirmation(
    db: Session,
    project_id: str,
    actions: list[dict],
    user_message: str,
) -> bool:
    confirmed = _is_affirmative_confirmation(user_message)
    pending_outline_titles = set()
    for action in actions:
        if isinstance(action, dict) and action.get("tool") == "create_outline_node":
            args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
            title = str(args.get("title") or "").strip()
            if title:
                pending_outline_titles.add(title)
        elif isinstance(action, dict) and action.get("tool") == "create_outline_nodes":
            args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
            nodes = args.get("nodes")
            if isinstance(nodes, list):
                for node in nodes:
                    if not isinstance(node, dict):
                        continue
                    title = str(node.get("title") or "").strip()
                    if title:
                        pending_outline_titles.add(title)
    if pending_outline_titles and _user_requests_chapter_creation(user_message) and not confirmed:
        return True
    for action in actions:
        if not isinstance(action, dict) or action.get("tool") != "create_chapter":
            continue
        args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
        outline_ref = args.get("outline_node_id") or args.get("outline_node_title") or args.get("outline_title")
        if _find_outline_by_title_or_id(db, project_id, outline_ref):
            continue
        if confirmed and str(outline_ref or "").strip() in pending_outline_titles:
            continue
        if confirmed and len(pending_outline_titles) == 1 and not str(outline_ref or "").strip():
            args["outline_node_title"] = next(iter(pending_outline_titles))
            continue
        if not confirmed:
            return True
        return True
    return False
