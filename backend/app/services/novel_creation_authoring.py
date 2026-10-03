"""Author-owned constraints and stage-output validation for V3 creation."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.modules.creation.domain.generation_contract import validate_stage_text_fields
from app.modules.creation.domain.opening_outline_contract import (
    OPENING_OUTLINE_INSTRUCTION,
    validate_opening_outline,
)
from app.services.novel_creation_contract import (
    LEGACY_OPENING_OUTLINE_CHAPTER_COUNT,
    OPENING_OUTLINE_CHAPTER_COUNT,
)

_WORLD_STYLE_TEXT_FIELDS = ("writing_style", "world_tone", "story_structure", "pacing")
_AUTHOR_FIELD_LABELS = {
    "writing_style": "正文风格",
    "world_tone": "世界基调",
    "story_structure": "剧情结构",
    "pacing": "叙事节奏",
    "core_tone": "核心基调",
    "atmosphere": "氛围",
    "emotional_color": "情绪色彩",
    "reader_experience": "读者感受",
    "narrative_perspective": "叙事视角",
    "perspective": "叙事视角",
    "sentence_rhythm": "句式节奏",
    "language_style": "语言风格",
    "main_line": "主线结构",
    "stages": "阶段安排",
    "opening": "开篇节奏",
    "middle": "中段节奏",
    "climax": "高潮节奏",
    "summary": "摘要",
    "description": "说明",
    "content": "内容",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _author_field_label(key: Any) -> str:
    text = _text(key)
    return _AUTHOR_FIELD_LABELS.get(text, text.replace("_", " "))


def _author_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "；".join(item for item in (_author_text(entry) for entry in value) if item)
    if isinstance(value, dict):
        parts = []
        for key, child in value.items():
            child_text = _author_text(child)
            if child_text:
                parts.append(f"{_author_field_label(key)}：{child_text}")
        return "；".join(parts)
    return _text(value)


def _dict_rows(value: Any, *, name_field: str = "name") -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [deepcopy(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        rows: list[dict[str, Any]] = []
        for key, child in value.items():
            if not isinstance(child, dict):
                continue
            item = deepcopy(child)
            item.setdefault(name_field, _text(key))
            rows.append(item)
        return rows
    return []


def _dedupe_dicts(rows: list[dict[str, Any]], key_builder: Any) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: dict[Any, int] = {}
    for row in rows:
        key = key_builder(row)
        if not key:
            unique.append(deepcopy(row))
            continue
        if key in seen:
            existing = unique[seen[key]]
            for field, value in row.items():
                if existing.get(field) in (None, "", [], {}):
                    existing[field] = deepcopy(value)
            continue
        seen[key] = len(unique)
        unique.append(deepcopy(row))
    return unique


def _looks_like_cli_metadata(data: dict[str, Any]) -> bool:
    event_type = _text(data.get("type")).lower().replace("-", "_")
    part = data.get("part") if isinstance(data.get("part"), dict) else {}
    part_type = _text(part.get("type")).lower().replace("-", "_")
    metadata_types = {
        "step_start", "step_finish", "message_start", "message_finish", "tool_start", "tool_finish",
    }
    return event_type in metadata_types or part_type in metadata_types


def _opening_outline_chapter_count(data: dict[str, Any]) -> int:
    """Read the persisted opening window, defaulting new data to three chapters."""
    try:
        chapter_count = int(data.get("opening_chapter_count") or 0)
    except (TypeError, ValueError):
        chapter_count = 0
    if chapter_count == LEGACY_OPENING_OUTLINE_CHAPTER_COUNT:
        return LEGACY_OPENING_OUTLINE_CHAPTER_COUNT
    return OPENING_OUTLINE_CHAPTER_COUNT


def _stage_contract(
    stage: str,
    *,
    opening_chapter_count: int = OPENING_OUTLINE_CHAPTER_COUNT,
    entity_target: dict[str, Any] | None = None,
) -> str:
    from app.modules.creation.domain.generation_contract import entity_generation_instruction

    opening_chapter_count = (
        LEGACY_OPENING_OUTLINE_CHAPTER_COUNT
        if opening_chapter_count == LEGACY_OPENING_OUTLINE_CHAPTER_COUNT
        else OPENING_OUTLINE_CHAPTER_COUNT
    )
    contracts = {
        "world_style": "保留 writing_style/world_tone/story_structure/pacing/style_rules/forbidden_patterns/worldbuilding/display_groups 字段；writing_style、world_tone、story_structure、pacing 必须各自是非空字符串，不得返回对象或数组；worldbuilding 使用司命六维分类。",
        "characters": "返回 characters 数组和 relationships 数组。每个角色必须含 name、role_type（主角固定为 protagonist，其余为 supporting）和 goal；并保留年龄、外貌、位置、状态，以及 profile 的 core_motivation、inner_lack、core_belief、public_persona、hidden_persona、reveal_chapter、moral_taboo、voice、action_habit、trauma_trigger。不得把 characters 改成以人名为键的对象。",
        "locations": (
            "返回 entries 数组和 relations 数组，不得重复实体或关系。"
            "entries 中每条对象必须含 title、dimension、content；地点 dimension 固定为 geography，"
            "势力 dimension 固定为 factions，不能省略或改用 type/entity_type。"
            "关系必须含 source_title、target_title、relation_type、description、metadata。"
        ),
        "macro_outline": "返回 story_overview、core_conflict、ending_direction、target_chapters、volumes、stage_plan；每卷必须含 title、start_chapter、end_chapter、summary；只做全书宏观结构，不展开全部章节。",
        "opening_outline": OPENING_OUTLINE_INSTRUCTION.format(count=opening_chapter_count),
        "final_review": "返回 ready（布尔值）、blocking（阻塞问题字符串数组）、warnings（提醒字符串数组）、counts；其中 characters/worldbuilding/chapters/sections 分别是实际角色、世界设定、开篇章纲、场景条目数，计划总章数放 target_chapters。只根据证据审阅，不擅自删改上游内容。",
    }
    return (
        contracts.get(stage, "保持输入结构，只提高具体性、一致性和可执行性。")
        + entity_generation_instruction(entity_target)
    )


def _validate_stage(stage: str, data: dict[str, Any]) -> None:
    if not isinstance(data, dict) or not data:
        raise ValueError("模型没有返回可用的阶段对象")
    if _looks_like_cli_metadata(data):
        raise ValueError("模型只返回了运行状态，没有返回可用的阶段正文")
    if stage == "final_review" and not isinstance(data.get("ready"), bool):
        raise ValueError("最终审阅缺少布尔字段 ready；请按契约返回 ready、blocking、warnings、counts")
    if stage == "concepts":
        options = data.get("options")
        _validate_compact_concepts(options)
    if stage == "world_style":
        invalid = [
            _AUTHOR_FIELD_LABELS[field]
            for field in _WORLD_STYLE_TEXT_FIELDS
            if not isinstance(data.get(field), str) or not data[field].strip()
        ]
        if invalid:
            raise ValueError("文风与世界观缺少可读文本：" + "、".join(invalid))
        if not isinstance(data.get("worldbuilding"), list) or not data["worldbuilding"]:
            raise ValueError("文风与世界观缺少可用的世界设定条目")
    if stage == "characters":
        characters = data.get("characters") if isinstance(data.get("characters"), list) else []
        if not characters:
            raise ValueError("角色与关系阶段没有返回角色数组")
        invalid = [
            _text(item.get("name")) or f"第{index + 1}个角色"
            for index, item in enumerate(characters)
            if not isinstance(item, dict)
            or not _text(item.get("role_type"))
            or not _text(item.get("goal") or item.get("current_goal"))
        ]
        if invalid:
            raise ValueError("以下角色缺少角色类型或当前目标：" + "、".join(invalid[:5]))
    if stage == "locations":
        entries = data.get("entries") if isinstance(data.get("entries"), list) else []
        relations = data.get("relations") if isinstance(data.get("relations"), list) else []
        if not entries:
            raise ValueError("地点与势力阶段没有返回实体数组")
        titles = {
            _text(item.get("title")).casefold()
            for item in entries
            if isinstance(item, dict) and _text(item.get("title"))
        }
        invalid_relations = [
            f"{_text(item.get('source_title')) or '未知起点'} → {_text(item.get('target_title')) or '未知终点'}"
            for item in relations
            if not isinstance(item, dict)
            or not _text(item.get("source_title"))
            or not _text(item.get("target_title"))
            or not _text(item.get("relation_type"))
            or _text(item.get("source_title")).casefold() not in titles
            or _text(item.get("target_title")).casefold() not in titles
        ]
        if invalid_relations:
            raise ValueError("以下地点关系缺少端点、类型或引用了不存在的实体：" + "、".join(invalid_relations[:5]))
    if stage == "macro_outline":
        validate_stage_text_fields(stage, data)
        volumes = data.get("volumes") if isinstance(data.get("volumes"), list) else []
        if not volumes:
            raise ValueError("全书主线与卷纲没有返回分卷规划")
        invalid_volumes = [
            _text(item.get("title")) or f"第{index + 1}卷"
            for index, item in enumerate(volumes)
            if not isinstance(item, dict)
            or not _text(item.get("summary"))
            or int(item.get("start_chapter") or 0) <= 0
            or int(item.get("end_chapter") or 0) < int(item.get("start_chapter") or 0)
        ]
        if invalid_volumes:
            raise ValueError("以下分卷缺少有效章节范围或摘要：" + "、".join(invalid_volumes[:5]))
    if stage == "opening_outline":
        _validate_opening_outline(data)


def _validate_opening_outline(data: dict[str, Any]) -> None:
    validate_opening_outline(data)


def _validate_compact_concepts(
    concepts: Any,
) -> list[dict[str, Any]]:
    if not isinstance(concepts, list) or not concepts:
        raise ValueError("创意方向没有可用的方案卡")
    required = ("title", "logline", "world_hook", "core_conflict", "opening_hook")
    cards: list[dict[str, Any]] = []
    titles: set[str] = set()
    for index, raw in enumerate(concepts):
        if not isinstance(raw, dict):
            raise ValueError(f"第{index + 1}张创意卡不是对象")
        missing = [field for field in required if not _text(raw.get(field))]
        protagonist = raw.get("protagonist_seed")
        if not isinstance(protagonist, dict):
            missing.append("protagonist_seed")
        else:
            for field in ("identity", "goal", "lack"):
                if not _text(protagonist.get(field)):
                    missing.append(f"protagonist_seed.{field}")
        if missing:
            raise ValueError(f"第{index + 1}张创意卡缺少：{'、'.join(missing)}")
        title = _text(raw.get("title"))
        if title in titles:
            raise ValueError("创意方向标题不得重复")
        titles.add(title)
        cards.append(raw)
    return cards


def _author_context(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "creation_mode": _text(draft.get("creation_mode")) or "explore",
        "author_brief": _text(draft.get("author_brief")),
        "author_outline": _text(draft.get("author_outline")),
        "locked_requirements": [
            _text(item) for item in (draft.get("locked_requirements") or []) if _text(item)
        ],
    }
