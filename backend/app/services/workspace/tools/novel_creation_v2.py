"""Workspace tools for the resumable V2 novel creation workbench."""
from __future__ import annotations

from app.architecture.uow import commit_session

import json
import re
import time
from contextlib import nullcontext
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from ....modules.model_runtime.application.execution import model_executor as LLMGateway
from ...operation_runtime import current_operation_id, record_operation_signal
from ....core.json_repair import parse_json_object
from ....database.models import NovelCreationSession, NovelCreationStageRun
from ....services.context_orchestrator import ContextOrchestrator, activate_context_manifest
from ....services.novel_creation_stage_runtime import stage_data_with_fallback, stage_tool_result
from ....services.observability.run_events import classify_failure
from ...novel_creation_workspace import (
    STAGE_LABELS,
    STAGE_ORDER,
    add_run_event,
    complete_run,
    create_run,
    derive_stage,
    fail_run,
    patch_session,
    save_compact_concepts,
    save_stage,
    serialize_run,
    serialize_session,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


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
    metadata_types = {"step_start", "step_finish", "message_start", "message_finish", "tool_start", "tool_finish"}
    return event_type in metadata_types or part_type in metadata_types


def _normalize_worldbuilding(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [deepcopy(item) for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key, child in value.items():
        if isinstance(child, dict):
            item = deepcopy(child)
            item.setdefault("title", _text(key))
            item.setdefault("dimension", _text(key))
            if not _text(item.get("content")):
                item["content"] = _author_text(item.get("summary") or item.get("description") or child)
        else:
            item = {"title": _text(key), "dimension": _text(key), "content": _author_text(child)}
        rows.append(item)
    return rows


def _normalize_characters(data: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    source_rows = _dict_rows(data.get("characters"))
    base_rows = _dict_rows(baseline.get("characters"))
    if not source_rows:
        source_rows = deepcopy(base_rows)
    base_by_name = {_text(row.get("name")): row for row in base_rows if _text(row.get("name"))}
    characters: list[dict[str, Any]] = []
    for index, source in enumerate(source_rows):
        name = _text(source.get("name"))
        base = base_by_name.get(name) or (base_rows[index] if index < len(base_rows) else {})
        item = {**deepcopy(base), **deepcopy(source)}
        item["name"] = name or _text(base.get("name")) or f"角色{index + 1}"
        profile = {**deepcopy(base.get("profile") if isinstance(base.get("profile"), dict) else {}), **deepcopy(item.get("profile") if isinstance(item.get("profile"), dict) else {})}
        source_profile = source.get("profile") if isinstance(source.get("profile"), dict) else {}
        role_type = _text(source.get("role_type") or source.get("role") or base.get("role_type"))
        if not role_type:
            role_type = "protagonist" if index == 0 else "supporting"
        goal = _text(
            source.get("goal")
            or source.get("current_goal")
            or source_profile.get("core_motivation")
            or base.get("goal")
            or profile.get("core_motivation")
        )
        item["role_type"] = role_type
        item["goal"] = goal
        item["current_goal"] = goal
        item["background"] = _text(item.get("background") or item.get("position") or item.get("status"))
        if not _text(profile.get("core_motivation")):
            profile["core_motivation"] = goal
        item["profile"] = profile
        characters.append(item)
    characters = _dedupe_dicts(characters, lambda item: _text(item.get("name")).casefold())
    relationships = _dict_rows(data.get("relationships"), name_field="id") or _dict_rows(baseline.get("relationships"), name_field="id")
    relationships = _dedupe_dicts(
        relationships,
        lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
    )
    return {**deepcopy(baseline), **deepcopy(data), "characters": characters, "relationships": relationships}


def _normalize_locations(data: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    entries = (
        _dict_rows(data.get("entries"), name_field="title")
        + _dict_rows(baseline.get("entries"), name_field="title")
    )
    entries = _dedupe_dicts(entries, lambda item: _text(item.get("title")).casefold())
    relations = (
        _dict_rows(data.get("relations"), name_field="id")
        + _dict_rows(baseline.get("relations"), name_field="id")
    )
    relations = _dedupe_dicts(
        relations,
        lambda item: (
            _text(item.get("source_title")).casefold(),
            _text(item.get("target_title")).casefold(),
            _text(item.get("relation_type")).casefold(),
        ),
    )
    return {**deepcopy(baseline), **deepcopy(data), "entries": entries, "relations": relations}


def _chapter_range(value: Any) -> tuple[int | None, int | None]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None, None
    numbers = re.findall(r"\d+", _text(value))
    if len(numbers) >= 2:
        return int(numbers[0]), int(numbers[1])
    return None, None


def _normalize_macro_outline(data: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    normalized = {**deepcopy(baseline), **deepcopy(data)}
    source_volumes = _dict_rows(data.get("volumes"), name_field="title")
    base_volumes = _dict_rows(baseline.get("volumes"), name_field="title")
    if not source_volumes:
        source_volumes = deepcopy(base_volumes)
    volumes: list[dict[str, Any]] = []
    for index, source in enumerate(source_volumes):
        base = base_volumes[index] if index < len(base_volumes) else {}
        item = {**deepcopy(base), **deepcopy(source)}
        parsed_start, parsed_end = _chapter_range(item.get("chapters") or item.get("range"))
        start = item.get("start_chapter") or parsed_start or base.get("start_chapter")
        end = item.get("end_chapter") or parsed_end or base.get("end_chapter")
        try:
            item["start_chapter"] = int(start)
            item["end_chapter"] = int(end)
        except (TypeError, ValueError):
            item["start_chapter"] = 0
            item["end_chapter"] = 0
        item["summary"] = _text(item.get("summary") or item.get("core_function") or item.get("focus") or item.get("climax") or base.get("summary"))
        item["title"] = _text(item.get("title")) or f"第{index + 1}卷"
        volumes.append(item)
    normalized["volumes"] = volumes
    normalized["stage_plan"] = _dict_rows(normalized.get("stage_plan"), name_field="name") or [
        {
            "name": item["title"],
            "range": [item["start_chapter"], item["end_chapter"]],
            "promise": item["summary"],
        }
        for item in volumes
    ]
    return normalized


def _chapter_number(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        numbers = re.findall(r"\d+", _text(value))
        return int(numbers[0]) if numbers else fallback


def _normalize_section(
    section: dict[str, Any],
    base: dict[str, Any],
    *,
    chapter_id: str,
    chapter_number: int,
    scene_number: int,
) -> dict[str, Any]:
    item = {**deepcopy(base), **deepcopy(section)}
    item["client_id"] = _text(item.get("client_id")) or f"{chapter_id}-section-{scene_number}"
    item["parent_client_id"] = chapter_id
    item["node_type"] = "section"
    item["sort_order"] = _chapter_number(item.get("sort_order"), scene_number)
    item["title"] = _text(item.get("title")) or f"第{chapter_number}章 · 场景{scene_number}"
    item["summary"] = _text(item.get("summary") or item.get("planned_summary") or item.get("purpose"))
    item["planned_summary"] = _text(item.get("planned_summary") or item.get("summary"))
    base_metadata = base.get("metadata") if isinstance(base.get("metadata"), dict) else {}
    source_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    metadata = {**deepcopy(base_metadata), **deepcopy(source_metadata)}
    metadata["scene_number"] = _chapter_number(metadata.get("scene_number"), scene_number)
    metadata["purpose"] = _text(metadata.get("purpose") or item.get("purpose") or item.get("summary")) or "推进本章目标"
    metadata["location"] = _text(metadata.get("location")) or "地点待定"
    metadata["timeline"] = _text(metadata.get("timeline")) or f"第{chapter_number}章第{scene_number}场"
    metadata["pov_character"] = _text(metadata.get("pov_character")) or "主角"
    metadata["characters"] = metadata.get("characters") if isinstance(metadata.get("characters"), list) else [metadata["pov_character"]]
    metadata["entry_state"] = _text(metadata.get("entry_state")) or "承接上一场景"
    metadata["exit_state"] = _text(metadata.get("exit_state")) or "产生新的行动压力"
    metadata["emotional_residue"] = _text(metadata.get("emotional_residue")) or "情绪推动下一场景"
    metadata["unresolved_actions"] = metadata.get("unresolved_actions") if isinstance(metadata.get("unresolved_actions"), list) else ["追踪本场景产生的新问题"]
    item["metadata"] = metadata
    return item


def _normalize_opening_outline(data: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    source_chapters = _dict_rows(data.get("chapters"), name_field="title")
    base_chapters = _dict_rows(baseline.get("chapters"), name_field="title")
    if base_chapters:
        source_chapters = (source_chapters + [{} for _ in range(len(base_chapters))])[:len(base_chapters)]
    chapters: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    top_sections = _dict_rows(data.get("sections"), name_field="title")
    base_sections = _dict_rows(baseline.get("sections"), name_field="title")
    for index, source in enumerate(source_chapters):
        base = base_chapters[index] if index < len(base_chapters) else {}
        original_id = _text(source.get("client_id"))
        chapter_number = _chapter_number(source.get("chapter_number") or source.get("chapter") or source.get("number"), index + 1)
        chapter_id = original_id or _text(base.get("client_id")) or f"chapter-{chapter_number:02d}"
        chapter = {**deepcopy(base), **deepcopy(source)}
        nested_sections = _dict_rows(chapter.pop("sections", None), name_field="title")
        chapter["client_id"] = chapter_id
        chapter["chapter_number"] = chapter_number
        chapter["node_type"] = "chapter"
        chapter["sort_order"] = _chapter_number(chapter.get("sort_order"), chapter_number)
        chapter["title"] = _text(chapter.get("title")) or f"第{chapter_number}章 未命名事件"
        chapter["summary"] = _text(chapter.get("summary") or chapter.get("planned_summary") or chapter.get("beat"))
        chapter["planned_summary"] = _text(chapter.get("planned_summary") or chapter.get("summary"))
        chapters.append(chapter)

        chapter_aliases = {chapter_id, str(chapter_number), f"chapter-{chapter_number:02d}"}
        if original_id:
            chapter_aliases.add(original_id)
        matching = nested_sections or [
            item for item in top_sections
            if _text(item.get("parent_client_id")) in chapter_aliases
        ]
        base_chapter_id = _text(base.get("client_id")) or chapter_id
        fallback_sections = [item for item in base_sections if _text(item.get("parent_client_id")) == base_chapter_id]
        if len(matching) not in range(2, 7) and fallback_sections:
            matching = fallback_sections
        for scene_index, raw_section in enumerate(matching[:6], start=1):
            base_section = fallback_sections[scene_index - 1] if scene_index <= len(fallback_sections) else {}
            sections.append(_normalize_section(
                raw_section,
                base_section,
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                scene_number=scene_index,
            ))
    return {
        **deepcopy(baseline),
        **deepcopy(data),
        "opening_chapter_count": len(chapters),
        "chapters": chapters,
        "sections": sections,
        "section_rule": "每章2至6个场景事件",
    }


def _normalize_stage_data(stage: str, data: dict[str, Any], baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    base = deepcopy(baseline) if isinstance(baseline, dict) else {}
    source = {} if _looks_like_cli_metadata(data) else deepcopy(data)
    normalized = {**base, **source}
    if stage == "world_style":
        for field in _WORLD_STYLE_TEXT_FIELDS:
            normalized[field] = _author_text(normalized.get(field))
        normalized["worldbuilding"] = _normalize_worldbuilding(normalized.get("worldbuilding"))
    elif stage == "characters":
        normalized = _normalize_characters(source, base)
    elif stage == "locations":
        normalized = _normalize_locations(source, base)
    elif stage == "macro_outline":
        normalized = _normalize_macro_outline(source, base)
    elif stage == "opening_outline":
        normalized = _normalize_opening_outline(source, base)
    return normalized


def _session(db: Session, session_id: str) -> NovelCreationSession | None:
    return db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()


def _free_opencode_candidates(model: str) -> list[str]:
    if not model.startswith("opencode_cli:"):
        return [model]
    try:
        from ....services.opencode_onboarding import inspect_opencode

        inspected = inspect_opencode()
        discovered = [
            f"opencode_cli:{item['id']}"
            for item in inspected.get("free_models", [])
            if item.get("id")
        ]
    except Exception:
        discovered = []
    return [model, *[candidate for candidate in discovered if candidate != model]]


async def _stream_model_text(
    *,
    messages: list[dict[str, Any]],
    model: str,
    temperature: float,
    max_tokens: int,
    extra_body: dict[str, Any] | None,
) -> str:
    chunks: list[str] = []
    emitted_chars = 0
    last_report_at = 0.0
    operation_id = current_operation_id()
    generator = LLMGateway.stream_chat_completion(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=0,
        retry=0,
        extra_body=extra_body,
    )
    async for chunk in generator:
        chunks.append(chunk)
        emitted_chars += len(chunk)
        now = time.monotonic()
        if operation_id and now - last_report_at >= 2:
            last_report_at = now
            record_operation_signal(
                operation_id,
                "output",
                {"output_chars": emitted_chars},
                message="模型正在生成并校验立项内容",
            )
    return "".join(chunks)


async def _generate_compact_concepts_with_fallback(
    session: NovelCreationSession,
    model: str,
    *,
    context_manifest: Any,
    on_fallback: Any,
    input_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    candidates = _free_opencode_candidates(model)
    for index, candidate in enumerate(candidates):
        try:
            return await _generate_compact_concepts(
                session,
                candidate,
                context_manifest=context_manifest,
                input_snapshot=input_snapshot,
            )
        except Exception as exc:
            last_error = exc
            failure = classify_failure(str(exc))
            retryable = failure == "quota_or_rate_limit" or any(
                token in str(exc).lower() for token in ("model not found", "unavailable", "overloaded")
            )
            if not retryable or index == len(candidates) - 1:
                raise
            on_fallback(candidate, candidates[index + 1], str(exc))
    if last_error:
        raise last_error
    raise ValueError("当前没有可用的免费模型")


def _stage_contract(stage: str) -> str:
    contracts = {
        "world_style": "保留 writing_style/world_tone/story_structure/pacing/style_rules/forbidden_patterns/worldbuilding/display_groups 字段；writing_style、world_tone、story_structure、pacing 必须各自是非空字符串，不得返回对象或数组；worldbuilding 使用司命六维分类。",
        "characters": "返回 characters 数组和 relationships 数组。每个角色必须含 name、role_type（主角固定为 protagonist，其余为 supporting）和 goal；并保留年龄、外貌、位置、状态，以及 profile 的 core_motivation、inner_lack、core_belief、public_persona、hidden_persona、reveal_chapter、moral_taboo、voice、action_habit、trauma_trigger。不得把 characters 改成以人名为键的对象。",
        "locations": "返回 entries 数组和 relations 数组，不得重复实体或关系。关系必须含 source_title、target_title、relation_type、description、metadata。",
        "macro_outline": "返回 story_overview、core_conflict、ending_direction、target_chapters、volumes、stage_plan；每卷必须含 title、start_chapter、end_chapter、summary；只做全书宏观结构，不展开全部章节。",
        "opening_outline": "顶层恰好返回 chapters 数组和 sections 数组：chapters 恰好15章且每章保留 client_id；每章对应2至6个 sections，所有 section 只能放在顶层 sections 数组并通过 parent_client_id 关联章节，不得嵌套在 chapter 内。section 必须含 client_id、parent_client_id 及 metadata.scene_number/purpose/location/timeline/pov_character/characters/entry_state/exit_state/emotional_residue/unresolved_actions。",
        "final_review": "返回 ready、blocking、warnings、counts。只根据证据审阅，不擅自删改上游内容。",
    }
    return contracts.get(stage, "保持输入结构，只提高具体性、一致性和可执行性。")


def _validate_stage(stage: str, data: dict[str, Any]) -> None:
    if not isinstance(data, dict) or not data:
        raise ValueError("模型没有返回可用的阶段对象")
    if _looks_like_cli_metadata(data):
        raise ValueError("模型只返回了运行状态，没有返回可用的阶段正文")
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
            if not isinstance(item, dict) or not _text(item.get("role_type")) or not _text(item.get("goal") or item.get("current_goal"))
        ]
        if invalid:
            raise ValueError("以下角色缺少角色类型或当前目标：" + "、".join(invalid[:5]))
    if stage == "locations":
        entries = data.get("entries") if isinstance(data.get("entries"), list) else []
        relations = data.get("relations") if isinstance(data.get("relations"), list) else []
        if not entries:
            raise ValueError("地点与势力阶段没有返回实体数组")
        titles = {_text(item.get("title")).casefold() for item in entries if isinstance(item, dict) and _text(item.get("title"))}
        invalid_relations = [
            f"{_text(item.get('source_title')) or '未知起点'} → {_text(item.get('target_title')) or '未知终点'}"
            for item in relations
            if (
                not isinstance(item, dict)
                or not _text(item.get("source_title"))
                or not _text(item.get("target_title"))
                or not _text(item.get("relation_type"))
                or _text(item.get("source_title")).casefold() not in titles
                or _text(item.get("target_title")).casefold() not in titles
            )
        ]
        if invalid_relations:
            raise ValueError("以下地点关系缺少端点、类型或引用了不存在的实体：" + "、".join(invalid_relations[:5]))
    if stage == "macro_outline":
        missing = [field for field in ("story_overview", "core_conflict", "ending_direction") if not _text(data.get(field))]
        volumes = data.get("volumes") if isinstance(data.get("volumes"), list) else []
        if missing:
            raise ValueError("全书主线与卷纲缺少：" + "、".join(missing))
        if not volumes:
            raise ValueError("全书主线与卷纲没有返回分卷规划")
        invalid_volumes = [
            _text(item.get("title")) or f"第{index + 1}卷"
            for index, item in enumerate(volumes)
            if (
                not isinstance(item, dict)
                or not _text(item.get("summary"))
                or int(item.get("start_chapter") or 0) <= 0
                or int(item.get("end_chapter") or 0) < int(item.get("start_chapter") or 0)
            )
        ]
        if invalid_volumes:
            raise ValueError("以下分卷缺少有效章节范围或摘要：" + "、".join(invalid_volumes[:5]))
    if stage == "opening_outline":
        chapters = data.get("chapters") if isinstance(data.get("chapters"), list) else []
        sections = data.get("sections") if isinstance(data.get("sections"), list) else []
        if len(chapters) != 15:
            raise ValueError(f"前15章细纲必须恰好包含15章，当前为{len(chapters)}章")
        counts: dict[str, int] = {}
        for section in sections:
            if isinstance(section, dict):
                parent = _text(section.get("parent_client_id"))
                counts[parent] = counts.get(parent, 0) + 1
        invalid = [
            _text(chapter.get("title") or chapter.get("chapter_number") or chapter.get("client_id"))
            or f"第{index + 1}章"
            for index, chapter in enumerate(chapters)
            if counts.get(_text(chapter.get("client_id")), 0) not in range(2, 7)
        ]
        if invalid:
            raise ValueError("以下章节的场景数量不在2至6个之间：" + "、".join(invalid[:5]))
        required_metadata = {
            "scene_number", "purpose", "location", "timeline", "pov_character",
            "characters", "entry_state", "exit_state", "emotional_residue", "unresolved_actions",
        }
        invalid_sections = [
            _text(section.get("title") or section.get("client_id")) or f"第{index + 1}个场景"
            for index, section in enumerate(sections)
            if (
                not isinstance(section, dict)
                or not _text(section.get("client_id"))
                or not _text(section.get("parent_client_id"))
                or not required_metadata.issubset(set(section.get("metadata") or {}))
            )
        ]
        if invalid_sections:
            raise ValueError("以下场景缺少结构化信息：" + "、".join(invalid_sections[:5]))


def _validate_compact_concepts(concepts: Any) -> list[dict[str, Any]]:
    if not isinstance(concepts, list) or len(concepts) != 3:
        raise ValueError("模型必须一次返回恰好三张轻量创意卡")
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
            raise ValueError("三张轻量创意卡必须具有不同标题")
        titles.add(title)
        cards.append(raw)
    return cards


async def _generate_compact_concepts(
    session: NovelCreationSession,
    model: str,
    *,
    context_manifest: Any | None = None,
    input_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate decision-ready concepts, never a complete project blueprint."""
    draft = deepcopy(input_snapshot) if isinstance(input_snapshot, dict) else (session.draft_json if isinstance(session.draft_json, dict) else {})
    interview = draft.get("interview") if isinstance(draft.get("interview"), dict) else {}
    context = {
        "brief": _text(session.user_brief),
        "form": draft.get("form") or {},
        "interview_history": interview.get("history") or [],
        "interview_reason": _text(interview.get("reason")),
    }
    from ....modules.creation.interfaces.dependencies import render_creation_prompt

    system = render_creation_prompt(
        task_kind="生成三套轻量创意方向",
        task_rules=(
            "只生成恰好三张轻量创意卡，不生成完整世界观、配角表、卷纲或章节细纲。"
            "三张卡必须遵守作者约束，并在故事发动机、冲突结构和开篇压力上有实质差异。"
        ),
    )
    shape = {
        "concepts": [{
            "title": "不超过20字的标题",
            "subtitle": "一句定位",
            "logline": "不超过120字的一句话梗概",
            "protagonist_seed": {"name": "主角名", "identity": "身份", "goal": "即时目标", "lack": "内在缺口"},
            "world_hook": "不超过100字的世界钩子",
            "core_conflict": "不超过100字的核心冲突",
            "story_engine": "持续推进故事的机制",
            "opening_hook": "不超过100字的开篇钩子",
            "differentiators": ["差异点一", "差异点二"],
            "risks": ["一个创作风险"]
        }]
    }
    user = (
        "请严格返回恰好三张创意卡，字段必须与下列 JSON 结构一致。"
        "每张卡应在数百字内可读完，三张卡不得只是改标题。\n"
        f"输出结构：{json.dumps(shape, ensure_ascii=False)}\n"
        f"作者上下文：{json.dumps(context, ensure_ascii=False)}"
    )
    from ....services.content_store import content_root

    with activate_context_manifest(context_manifest) if context_manifest else nullcontext():
        raw = await _stream_model_text(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=model,
            temperature=0.8,
            max_tokens=3200,
            extra_body=LLMGateway.local_cli_extra_body(
                model,
                cwd=str(content_root()),
                base={"moshu_task_type": "planning", "storage_target": "session_draft"},
            ),
        )
    if not raw:
        raise RuntimeError("模型没有返回轻量创意卡")
    parsed = parse_json_object(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("模型返回的轻量创意卡不是有效 JSON")
    payload = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
    return _validate_compact_concepts(payload.get("concepts"))


async def _enhance_with_model(
    session: NovelCreationSession,
    stage: str,
    baseline: dict[str, Any],
    model: str,
    *,
    context_manifest: Any | None = None,
    input_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    draft = deepcopy(input_snapshot) if isinstance(input_snapshot, dict) else (session.draft_json if isinstance(session.draft_json, dict) else {})
    context = {
        "form": draft.get("form"),
        "selected_concept_id": draft.get("selected_concept_id"),
        "confirmed_stages": {
            name: value.get("data")
            for name, value in (draft.get("stages") or {}).items()
            if isinstance(value, dict) and value.get("status") == "confirmed"
        },
        "baseline": baseline,
    }
    from ....modules.creation.interfaces.dependencies import render_creation_prompt

    system = render_creation_prompt(
        task_kind=f"深化阶段：{STAGE_LABELS.get(stage, stage)}",
        task_rules=(
            "只深化当前阶段的 baseline，顶层只返回 data 字段；"
            "保留作者约束、已确认事实和专名，不提前生成下游阶段。"
        ),
    )
    user = (
        f"当前阶段：{STAGE_LABELS.get(stage, stage)}\n"
        f"结构契约：{_stage_contract(stage)}\n"
        "请在保留作者约束和已确认事实的前提下，深化 baseline；不要改变已经确认的专名。\n"
        f"上下文：{json.dumps(context, ensure_ascii=False)}"
    )
    from ....services.content_store import content_root

    with activate_context_manifest(context_manifest) if context_manifest else nullcontext():
        raw = await _stream_model_text(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=model,
            temperature=0.65,
            max_tokens=12000 if stage == "opening_outline" else 6000,
            extra_body=LLMGateway.local_cli_extra_body(
                model,
                cwd=str(content_root()),
                base={"moshu_task_type": "planning", "storage_target": "session_draft"},
            ),
        )
    if not raw:
        raise RuntimeError("没有收到模型的文字回复")
    parsed = parse_json_object(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("模型返回的阶段 JSON 格式不合法")
    data = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
    data = _normalize_stage_data(stage, data, baseline)
    _validate_stage(stage, data)
    return data


async def get_novel_creation_session(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session_id = _text(args.get("session_id"))
    session = _session(db, session_id)
    if not session:
        return {"tool": "get_novel_creation_session", "status": "skipped", "detail": "Session not found", "data": None}
    return {
        "tool": "get_novel_creation_session",
        "status": "ok",
        "detail": "Novel creation session loaded",
        "data": serialize_session(session),
    }


async def generate_novel_creation_stage(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session_id = _text(args.get("session_id"))
    stage = _text(args.get("stage"))
    session = _session(db, session_id)
    if not session:
        return {"tool": "generate_novel_creation_stage", "status": "skipped", "detail": "Session not found", "data": None}
    if stage not in {*STAGE_ORDER, "all"}:
        return {"tool": "generate_novel_creation_stage", "status": "skipped", "detail": "Unknown stage", "data": None}

    if isinstance(args.get("session_patch"), dict):
        patch_session(session, args["session_patch"])
    model = _text(args.get("model"))
    use_model = bool(args.get("use_model", bool(model)))
    auto_confirm = bool(args.get("auto_confirm", stage == "all"))
    existing_run_id = _text(args.get("_run_id"))
    run = db.query(NovelCreationStageRun).filter(NovelCreationStageRun.id == existing_run_id).first() if existing_run_id else None
    run_request = run.request_json if run and isinstance(run.request_json, dict) else {}
    current_draft = session.draft_json if isinstance(session.draft_json, dict) else {}
    working_draft = deepcopy(run_request.get("input_snapshot")) if isinstance(run_request.get("input_snapshot"), dict) else deepcopy(current_draft)
    orchestrator = ContextOrchestrator(db)
    manifest_id = _text(args.get("context_manifest_id")) or _text(getattr(run, "context_manifest_id", ""))
    manifest = orchestrator.get_manifest(manifest_id) if manifest_id else None
    if manifest is None:
        draft = working_draft
        manifest = orchestrator.prepare(
            project_id=None,
            task_type="new_project",
            model=model or None,
            execution_route="novel_creation",
            session_id=session.id,
            arguments={
                "session_id": session.id,
                "session": {"brief": session.user_brief, "draft": draft},
                "answers": ((draft.get("interview") or {}).get("history") if isinstance(draft.get("interview"), dict) else []),
                "confirmed_stages": draft.get("stages") or {},
                "author_constraints": session.user_brief or "",
                "stage": stage,
            },
        )
    usable, context_detail = orchestrator.validate(manifest)
    governed_args = {**args, "context_manifest_id": manifest.id}
    if run is None:
        run = create_run(db, session, stage, governed_args)
        commit_session(db)
    elif not run.context_manifest_id:
        run.context_manifest_id = manifest.id
        commit_session(db)
    if not usable:
        run.status = manifest.status
        run.current_message = context_detail
        run.next_action = "Review or override the context manifest, then retry this stage."
        commit_session(db)
        return {
            "tool": "generate_novel_creation_stage",
            "status": manifest.status,
            "detail": context_detail,
            "data": {"run": serialize_run(run), "session": serialize_session(session)},
        }

    active_stage = stage
    try:
        generated: dict[str, Any] = {}
        if stage == "concepts":
            add_run_event(
                db,
                run,
                "stage_progress",
                "running",
                "正在生成三套轻量创意",
                {"stage": "concepts", "model_source": model or "none", "storage_target": "session_draft"},
            )
            run.current_message = "正在生成三套轻量创意"
            commit_session(db)
            if not use_model or not model:
                raise ValueError("轻量创意需要选择可用模型后才能生成")
            def record_fallback(previous: str, following: str, reason: str) -> None:
                add_run_event(
                    db,
                    run,
                    "model_fallback",
                    "running",
                    f"免费模型 {previous} 暂时不可用，已明确切换为 {following}",
                    {
                        "previous_model": previous,
                        "model_source": following,
                        "failure_class": classify_failure(reason),
                    },
                )
                commit_session(db)

            concepts = await _generate_compact_concepts_with_fallback(
                session,
                model,
                context_manifest=manifest,
                on_fallback=record_fallback,
                input_snapshot=working_draft,
            )
            concept_stage = save_compact_concepts(session, concepts)
            generated["concepts"] = deepcopy(concept_stage.get("data") or {})
            add_run_event(
                db,
                run,
                "stage_completed",
                "ok",
                "三套轻量创意已保存",
                {"stage": "concepts", "storage_target": "session_draft"},
            )
            commit_session(db)
        else:
            stages = [name for name in STAGE_ORDER if name not in {"constraints", "concepts", "final_review"}] if stage == "all" else [stage]
            for name in stages:
                active_stage = name
                add_run_event(
                    db,
                    run,
                    "stage_progress",
                    "running",
                    f"正在生成{STAGE_LABELS.get(name, name)}",
                    {"stage": name, "model_source": model or "contract", "storage_target": "session_draft"},
                )
                run.current_message = f"正在生成{STAGE_LABELS.get(name, name)}"
                commit_session(db)
                baseline = derive_stage(session, name, working_draft)
                data, source = await stage_data_with_fallback(
                    db,
                    run,
                    session,
                    stage=name,
                    baseline=baseline,
                    model=model,
                    use_model=use_model,
                    quick_run=stage == "all",
                    manifest=manifest,
                    working_draft=working_draft,
                    enhance=_enhance_with_model,
                )
                data = _normalize_stage_data(name, data, baseline)
                _validate_stage(name, data)
                save_stage(session, name, data, confirm=auto_confirm, source=source)
                working_draft.setdefault("stages", {})[name] = {
                    "status": "confirmed" if auto_confirm else "generated",
                    "data": deepcopy(data),
                    "source": source,
                }
                generated[name] = deepcopy(data)
                add_run_event(
                    db,
                    run,
                    "stage_completed",
                    "ok",
                    f"{STAGE_LABELS.get(name, name)}已保存",
                    {"stage": name, "storage_target": "session_draft"},
                )
                commit_session(db)
        if stage == "all":
            final = derive_stage(session, "final_review", working_draft)
            save_stage(session, "final_review", final, confirm=False, source="contract")
            generated["final_review"] = final
            add_run_event(
                db,
                run,
                "stage_completed",
                "ok" if final.get("ready") else "warning",
                "最终审阅已完成",
                {"stage": "final_review", "ready": bool(final.get("ready")), "storage_target": "session_draft"},
            )
            commit_session(db)
        complete_run(db, run, {"stages": generated})
        orchestrator.mark_consumed(manifest)
        commit_session(db)
        db.refresh(run)
        return stage_tool_result("ok", "Novel creation stage generated", run, session)
    except Exception as exc:
        db.rollback()
        session = _session(db, session_id)
        run = db.query(NovelCreationStageRun).filter(NovelCreationStageRun.id == run.id).first()
        if run and session:
            fail_run(db, run, exc, failed_stage=active_stage)
            commit_session(db)
            return stage_tool_result("error", str(exc), run, session)
        return {"tool": "generate_novel_creation_stage", "status": "error", "detail": str(exc), "data": None}


async def submit_novel_creation_stage(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session_id = _text(args.get("session_id"))
    stage = _text(args.get("stage"))
    session = _session(db, session_id)
    if not session:
        return {"tool": "submit_novel_creation_stage", "status": "skipped", "detail": "Session not found", "data": None}
    if stage not in STAGE_ORDER:
        return {"tool": "submit_novel_creation_stage", "status": "skipped", "detail": "Unknown stage", "data": None}
    expected_revision = args.get("expected_revision")
    if expected_revision is not None and int(session.revision or 0) != int(expected_revision):
        return {
            "tool": "submit_novel_creation_stage",
            "status": "error",
            "detail": "Novel creation session revision conflict",
            "data": {
                "failure_class": "revision_conflict",
                "current_revision": int(session.revision or 0),
                "session": serialize_session(session),
            },
        }
    data = args.get("data")
    if not isinstance(data, dict):
        data = derive_stage(session, stage)
    try:
        data = _normalize_stage_data(stage, data)
        _validate_stage(stage, data)
        save_stage(session, stage, data, confirm=bool(args.get("confirm", True)), source=_text(args.get("source")) or "author")
        commit_session(db)
        return {
            "tool": "submit_novel_creation_stage",
            "status": "ok",
            "detail": f"{STAGE_LABELS[stage]}已保存",
            "data": serialize_session(session),
        }
    except Exception as exc:
        db.rollback()
        return {"tool": "submit_novel_creation_stage", "status": "error", "detail": str(exc), "data": None}
