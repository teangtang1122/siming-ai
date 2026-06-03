"""Plan generators for predefined agent execution paths."""
from __future__ import annotations

import re
from typing import Any

from .plan_graph import PlanGraph, StepDef


# ---------------------------------------------------------------------------
# Intent types
# ---------------------------------------------------------------------------

_INTENT_CHAPTER = "chapter"
_INTENT_CHARACTER = "character"
_INTENT_WORLDBUILDING = "worldbuilding"
_INTENT_PROJECT_INIT = "project_init"


def plan_fast_chapter(
    *,
    outline_node_id: str,
    requirements: str = "",
    involved_characters: list[str] | None = None,
) -> PlanGraph:
    """Fast chapter write path: search -> write -> save -> detect changes."""
    chars = involved_characters or []
    char_args = {"involved_characters": chars} if chars else {}

    steps: dict[str, StepDef] = {
        "search_outline": StepDef(
            tool="search_outline",
            args={"node_id": outline_node_id},
            depends_on=[],
            label="查找大纲节点",
        ),
        "chapter_writer": StepDef(
            tool="chapter_writer",
            args={
                "outline_node_id": outline_node_id,
                "requirements": requirements,
                **char_args,
            },
            depends_on=["search_outline"],
            retry_policy="auto",
            label="生成章节内容",
        ),
        "create_chapter": StepDef(
            tool="create_chapter",
            args={
                "outline_node_id": outline_node_id,
                "draft_id": "{chapter_writer.data.draft_id}",
                "content_ref": "{chapter_writer.data.content_ref}",
                "title": "{search_outline.data.0.title}",
            },
            depends_on=["chapter_writer"],
            label="保存章节",
        ),
        "detect_character_changes": StepDef(
            tool="detect_character_changes",
            args={
                "draft_id": "{chapter_writer.data.draft_id}",
                "content_ref": "{chapter_writer.data.content_ref}",
                "chapter_id": "{create_chapter.data.chapter_id}",
                "outline_node_id": outline_node_id,
            },
            depends_on=["create_chapter"],
            label="检测角色变化",
        ),
        "detect_new_worldbuilding": StepDef(
            tool="detect_new_worldbuilding",
            args={
                "draft_id": "{chapter_writer.data.draft_id}",
                "content_ref": "{chapter_writer.data.content_ref}",
                "outline_node_id": outline_node_id,
            },
            depends_on=["create_chapter"],
            label="检测新世界观元素",
        ),
    }

    return PlanGraph(name="fast_chapter", steps=steps)


def plan_quality_chapter(
    *,
    outline_node_id: str,
    requirements: str = "",
    involved_characters: list[str] | None = None,
) -> PlanGraph:
    """Quality chapter write path: preview -> plot -> roleplay -> write -> evaluate -> save -> detect."""
    chars = involved_characters or []
    char_args = {"involved_characters": chars} if chars else {}

    # Roleplay tool selection: 2+ characters -> dialogue_battle, else roleplay_character
    if len(chars) >= 2:
        roleplay_key = "dialogue_battle"
        roleplay_step = StepDef(
            tool="dialogue_battle",
            args={
                "character_names": chars,
                "scene": "{design_plot.data.scenes.0}",
                "outline_node_id": outline_node_id,
            },
            depends_on=["design_plot"],
            label="多角色对话演练",
        )
    else:
        roleplay_key = "roleplay"
        roleplay_step = StepDef(
            tool="roleplay_character",
            args={
                "character_name": chars[0] if chars else "",
                "situation": "{design_plot.data.scenes.0}",
                "outline_node_id": outline_node_id,
            },
            depends_on=["design_plot"],
            label="角色反应演练",
        )

    steps: dict[str, StepDef] = {
        "preview_context": StepDef(
            tool="preview_writing_context",
            args={
                "outline_node_id": outline_node_id,
                "requirements": requirements,
                **char_args,
            },
            depends_on=[],
            label="预览写作上下文",
        ),
        "design_plot": StepDef(
            tool="design_plot",
            args={
                "outline_node_id": outline_node_id,
                "requirements": requirements,
                "context_summary": "{preview_context.data}",
            },
            depends_on=["preview_context"],
            retry_policy="auto",
            label="设计剧情",
        ),
        roleplay_key: roleplay_step,
        "chapter_writer": StepDef(
            tool="chapter_writer",
            args={
                "outline_node_id": outline_node_id,
                "requirements": requirements,
                "previous_plot": "{design_plot.data}",
                "previous_roleplay": "{" + roleplay_key + ".data}",
                **char_args,
            },
            depends_on=[roleplay_key],
            retry_policy="auto",
            label="生成章节内容",
        ),
        "evaluate_chapter": StepDef(
            tool="evaluate_chapter",
            args={
                "draft_id": "{chapter_writer.data.draft_id}",
                "content_ref": "{chapter_writer.data.content_ref}",
                "outline_node_id": outline_node_id,
            },
            depends_on=["chapter_writer"],
            label="评估章节质量",
        ),
        "create_chapter": StepDef(
            tool="create_chapter",
            args={
                "outline_node_id": outline_node_id,
                "draft_id": "{chapter_writer.data.draft_id}",
                "content_ref": "{chapter_writer.data.content_ref}",
                "title": "{preview_context.data.outline_context.title}",
            },
            depends_on=["evaluate_chapter"],
            label="保存章节",
        ),
        "detect_character_changes": StepDef(
            tool="detect_character_changes",
            args={
                "draft_id": "{chapter_writer.data.draft_id}",
                "content_ref": "{chapter_writer.data.content_ref}",
                "chapter_id": "{create_chapter.data.chapter_id}",
                "outline_node_id": outline_node_id,
            },
            depends_on=["create_chapter"],
            label="检测角色变化",
        ),
        "detect_new_worldbuilding": StepDef(
            tool="detect_new_worldbuilding",
            args={
                "draft_id": "{chapter_writer.data.draft_id}",
                "content_ref": "{chapter_writer.data.content_ref}",
                "outline_node_id": outline_node_id,
            },
            depends_on=["create_chapter"],
            label="检测新世界观元素",
        ),
    }

    return PlanGraph(name="quality_chapter", steps=steps)


def plan_cataloging_init(
    *,
    chapter_ids: list[str] | None = None,
) -> PlanGraph:
    """Cataloging initialization path — all tools marked not_implemented.

    This plan can only be constructed directly, never by detect_intent.
    The orchestrator will skip all steps with status="skipped".
    """
    steps: dict[str, StepDef] = {
        "extract_facts": StepDef(
            tool="extract_facts",
            args={"chapter_ids": chapter_ids or []},
            depends_on=[],
            label="提取事实（未实现）",
        ),
        "resolve_targets": StepDef(
            tool="resolve_targets",
            args={},
            depends_on=["extract_facts"],
            label="解析目标（未实现）",
        ),
        "apply_candidates": StepDef(
            tool="apply_candidates",
            args={},
            depends_on=["resolve_targets"],
            label="应用候选（未实现）",
        ),
    }

    return PlanGraph(name="cataloging_init", steps=steps)


# ---------------------------------------------------------------------------
# Plan generators: character and worldbuilding
# ---------------------------------------------------------------------------

def plan_create_character(
    *,
    character_name: str = "",
    requirements: str = "",
) -> PlanGraph:
    """Character creation path: generate -> save."""
    steps: dict[str, StepDef] = {
        "character_writer": StepDef(
            tool="character_writer",
            args={
                "name": character_name,
                "requirements": requirements,
            },
            depends_on=[],
            retry_policy="auto",
            label="生成角色档案",
        ),
        "create_character": StepDef(
            tool="create_character",
            args={
                "name": "{character_writer.data.character.name}",
                "appearance": "{character_writer.data.character.appearance}",
                "personality": "{character_writer.data.character.personality}",
                "background": "{character_writer.data.character.background}",
                "abilities": "{character_writer.data.character.abilities}",
                "role_type": "{character_writer.data.character.role_type}",
                "speech_style": "{character_writer.data.character.speech_style}",
                "motivation": "{character_writer.data.character.motivation}",
                "conflict": "{character_writer.data.character.conflict}",
                "custom_system_prompt": "{character_writer.data.character.custom_system_prompt}",
            },
            depends_on=["character_writer"],
            label="保存角色",
        ),
    }
    return PlanGraph(name="create_character", steps=steps)


def plan_create_worldbuilding(
    *,
    topic: str = "",
    requirements: str = "",
) -> PlanGraph:
    """Worldbuilding creation path: generate -> save."""
    steps: dict[str, StepDef] = {
        "worldbuilding_writer": StepDef(
            tool="worldbuilding_writer",
            args={
                "title": topic,
                "requirements": requirements,
            },
            depends_on=[],
            retry_policy="auto",
            label="生成世界观设定",
        ),
        "create_worldbuilding_entry": StepDef(
            tool="create_worldbuilding_entry",
            args={
                "title": "{worldbuilding_writer.data.entry.title}",
                "content": "{worldbuilding_writer.data.entry.content}",
                "dimension": "{worldbuilding_writer.data.entry.dimension}",
                "plot_usage": "{worldbuilding_writer.data.entry.plot_usage}",
                "constraints": "{worldbuilding_writer.data.entry.constraints}",
            },
            depends_on=["worldbuilding_writer"],
            label="保存世界观条目",
        ),
    }
    return PlanGraph(name="create_worldbuilding", steps=steps)


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

_CHAPTER_RE = re.compile(r"第\s*(\d+)\s*章")
_QUALITY_KEYWORDS = {"精写", "高质量", "仔细写", "认真写", "质量", "quality"}

# Character creation keywords
_CHAR_KEYWORDS = ("创建角色", "新建角色", "新角色", "添加角色", "写角色", "生成角色", "设计角色")
# Worldbuilding keywords
_WB_KEYWORDS = ("创建世界观", "新建世界观", "世界观设定", "创建设定", "添加设定", "写世界观", "生成世界观", "世界观条目")
# Project init keywords
_INIT_KEYWORDS = ("初始化项目", "项目初始化", "自动规划", "帮我规划", "全面规划")


def _extract_character_name(text: str) -> str:
    """Try to extract a character name from the user message."""
    # Patterns like "创建角色小明", "新角色：张三", "创建一个叫小明的角色"
    for pattern in [
        r"(?:创建|新建|添加|写|生成|设计)角色[：:]?\s*(.+?)(?:[，,。\s]|$)",
        r"(?:叫|名为|名叫)\s*(.+?)(?:[，,。\s的]|$)",
    ]:
        m = re.search(pattern, text)
        if m:
            name = m.group(1).strip()
            if 1 <= len(name) <= 20:
                return name
    return ""


def _extract_worldbuilding_topic(text: str) -> str:
    """Try to extract a worldbuilding topic from the user message."""
    for pattern in [
        r"(?:创建|新建|添加|写|生成)世界观[：:]?\s*(.+?)(?:[，,。\s]|$)",
        r"(?:创建|新建|添加)设定[：:]?\s*(.+?)(?:[，,。\s]|$)",
        r"(?:关于)\s*(.+?)(?:的设定|的世界观|[，,。\s]|$)",
    ]:
        m = re.search(pattern, text)
        if m:
            topic = m.group(1).strip()
            if 1 <= len(topic) <= 30:
                return topic
    return ""


def detect_intent(user_message: str) -> dict[str, Any] | None:
    """Parse user intent from a natural language message.

    Returns dict with keys:
      - intent_type: "chapter" | "character" | "worldbuilding" | "project_init"
      - For chapter: mode, outline_query, chapter_number
      - For character: character_name, requirements
      - For worldbuilding: topic, requirements
      - For project_init: requirements

    Returns None if no recognizable intent detected.
    """
    text = user_message.strip()
    if not text:
        return None

    # 1. Project init
    if any(kw in text for kw in _INIT_KEYWORDS):
        return {"intent_type": _INTENT_PROJECT_INIT, "requirements": text}

    # 2. Character creation
    if any(kw in text for kw in _CHAR_KEYWORDS):
        return {
            "intent_type": _INTENT_CHARACTER,
            "character_name": _extract_character_name(text),
            "requirements": text,
        }

    # 3. Worldbuilding creation
    if any(kw in text for kw in _WB_KEYWORDS):
        return {
            "intent_type": _INTENT_WORLDBUILDING,
            "topic": _extract_worldbuilding_topic(text),
            "requirements": text,
        }

    # 4. Chapter writing
    chapter_match = _CHAPTER_RE.search(text)
    if chapter_match:
        chapter_number = int(chapter_match.group(1))
        mode = "quality" if any(kw in text for kw in _QUALITY_KEYWORDS) else "fast"
        return {
            "intent_type": _INTENT_CHAPTER,
            "mode": mode,
            "outline_query": text,
            "requirements": text,
            "chapter_number": chapter_number,
        }

    if any(kw in text for kw in ("写章", "写一章", "续写", "继续写")):
        mode = "quality" if any(kw in text for kw in _QUALITY_KEYWORDS) else "fast"
        return {
            "intent_type": _INTENT_CHAPTER,
            "mode": mode,
            "outline_query": text,
            "requirements": text,
            "chapter_number": None,
        }

    return None


def build_plan_from_intent(intent: dict[str, Any], *, outline_node_id: str = "") -> PlanGraph | None:
    """Build a PlanGraph from a detected intent dict.

    Returns None if the intent type is not supported for plan execution.
    """
    intent_type = intent.get("intent_type")

    if intent_type == _INTENT_CHAPTER:
        mode = intent.get("mode", "fast")
        if mode == "quality":
            return plan_quality_chapter(
                outline_node_id=outline_node_id,
                requirements=intent.get("requirements", ""),
            )
        return plan_fast_chapter(
            outline_node_id=outline_node_id,
            requirements=intent.get("requirements", ""),
        )

    if intent_type == _INTENT_CHARACTER:
        return plan_create_character(
            character_name=intent.get("character_name", ""),
            requirements=intent.get("requirements", ""),
        )

    if intent_type == _INTENT_WORLDBUILDING:
        return plan_create_worldbuilding(
            topic=intent.get("topic", ""),
            requirements=intent.get("requirements", ""),
        )

    # project_init: not yet implemented as a plan — fall back to old assistant
    if intent_type == _INTENT_PROJECT_INIT:
        return None

    return None
