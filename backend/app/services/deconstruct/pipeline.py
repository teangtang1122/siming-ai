"""Pure data-processing functions for the deconstruct map-reduce pipeline."""
import json
import re
from typing import Optional

from sqlalchemy.orm import Session

from ...core.exceptions import ValidationError
from ...core.model_limits import ModelSafetyLimits
from ...database.models import Chapter
from ...prompts.deconstruct import MAP_JSON_TEMPLATE, MAP_OUTPUT_RULES, MAP_SYSTEM_PROMPT, map_instructions
from .constants import (
    CHAPTER_CHUNK_THRESHOLD,
    CHAPTER_SUB_CHUNK_SIZE,
    CHUNK_SIZE,
    FINAL_OUTPUT_ARRAY_MAX_ITEMS,
    REDUCE_BRIEF_MAX_CHARS_PER_CHUNK,
    REDUCE_BRIEF_MIN_CHARS_PER_CHUNK,
    REDUCE_INPUT_MAX_CHARS,
    REDUCE_INPUT_PROFILES,
    REDUCE_SECTION_SOURCE_FIELDS,
)


def split_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """Split text into chunks, trying to break at sentence boundaries."""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            for sep in ["\n\n", "。\n", "。", ". ", "!", "?", "\n", " "]:
                pos = text.rfind(sep, start, end)
                if pos > start + chunk_size // 2:
                    end = pos + 1
                    break
        chunks.append(text[start:end])
        start = end
    return chunks


def chapter_aware_chunks(chapters: list) -> list[str]:
    """Split chapters into map-ready chunks.

    Each chapter becomes one chunk with its title header.
    Only chapters longer than CHAPTER_CHUNK_THRESHOLD (6000 chars) are
    sub-split into ~CHAPTER_SUB_CHUNK_SIZE (3000 char) pieces at sentence
    boundaries, each sub-chunk keeping the chapter header for context.
    """
    chunks = []
    for chapter in chapters:
        header = f"{'=' * 40}\n{chapter.title}\n{'=' * 40}\n\n"
        content = chapter.content or ""
        full = header + content
        if len(full) <= CHAPTER_CHUNK_THRESHOLD:
            chunks.append(full)
        else:
            for sub in split_text(content, CHAPTER_SUB_CHUNK_SIZE):
                chunks.append(header + sub)
    return chunks


def squash_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clip_text(value: object, max_chars: int) -> str:
    text = squash_text(value)
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return f"{text[:max_chars - 3]}..."


def safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def clip_string_list(value: object, max_items: int, max_chars: int) -> list[str]:
    items = []
    for item in as_list(value):
        text = clip_text(item, max_chars)
        if text:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def profile_char_limit(profile: dict, key: str, item_char_limit: int) -> int:
    if profile.get("name") == "normal":
        return item_char_limit
    return min(item_char_limit, int(profile.get(key) or item_char_limit))


def compact_map_result_for_reduce(
    result: dict,
    index: int,
    section_key: str,
    profile: dict,
    item_char_limit: int,
) -> dict:
    fields = REDUCE_SECTION_SOURCE_FIELDS.get(section_key) or {
        "characters", "events", "world_facts", "clues", "pacing", "narrative_mode", "themes", "techniques"
    }
    entry: dict = {"chunk_index": index}
    if not isinstance(result, dict):
        entry["_error"] = "missing_result"
        return entry
    if result.get("_error"):
        entry["_error"] = clip_text(result.get("_error"), profile_char_limit(profile, "short", item_char_limit))

    if "characters" in fields:
        source_characters = as_list(result.get("characters")) or as_list(result.get("character_profiles"))
        characters = []
        for item in source_characters[:profile["characters"]]:
            if not isinstance(item, dict):
                continue
            name = clip_text(item.get("name"), profile_char_limit(profile, "short", item_char_limit))
            if not name:
                continue
            fact_items = clip_string_list(
                item.get("facts"),
                profile["character_actions"] + profile["character_traits"],
                profile_char_limit(profile, "text", item_char_limit),
            )
            relationships = []
            for rel in as_list(item.get("relationships"))[:3]:
                if isinstance(rel, dict):
                    target_name = clip_text(rel.get("target_name"), profile_char_limit(profile, "short", item_char_limit))
                    rel_type = clip_text(rel.get("relationship_type"), profile_char_limit(profile, "short", item_char_limit))
                    description = clip_text(rel.get("description"), profile_char_limit(profile, "text", item_char_limit))
                else:
                    target_name = ""
                    rel_type = ""
                    description = clip_text(rel, profile_char_limit(profile, "text", item_char_limit))
                if target_name or description:
                    relationships.append({
                        "target_name": target_name,
                        "relationship_type": rel_type,
                        "description": description,
                    })
            appearances = []
            for ap in as_list(item.get("appearances"))[:3]:
                if isinstance(ap, dict):
                    appearances.append({
                        "chapter_title": clip_text(ap.get("chapter_title"), profile_char_limit(profile, "short", item_char_limit)),
                        "scene": clip_text(ap.get("scene"), profile_char_limit(profile, "text", item_char_limit)),
                        "role_in_scene": clip_text(ap.get("role_in_scene"), profile_char_limit(profile, "short", item_char_limit)),
                        "summary": clip_text(ap.get("summary"), profile_char_limit(profile, "text", item_char_limit)),
                    })
                else:
                    summary = clip_text(ap, profile_char_limit(profile, "text", item_char_limit))
                    if summary:
                        appearances.append({"chapter_title": "", "scene": "", "role_in_scene": "", "summary": summary})
            characters.append({
                "name": clip_text(name, profile_char_limit(profile, "short", item_char_limit)),
                "role_hint": clip_text(
                    item.get("role_hint") or item.get("role") or item.get("role_type"),
                    profile_char_limit(profile, "short", item_char_limit),
                ),
                "mentions": safe_int(item.get("mentions") or item.get("mention_count")),
                "appearance": clip_text(item.get("appearance"), profile_char_limit(profile, "text", item_char_limit)),
                "speech_style": clip_text(item.get("speech_style"), profile_char_limit(profile, "text", item_char_limit)),
                "actions": clip_string_list(
                    item.get("actions") or fact_items,
                    profile["character_actions"],
                    profile_char_limit(profile, "text", item_char_limit),
                ),
                "traits": clip_string_list(
                    item.get("traits") or fact_items,
                    profile["character_traits"],
                    profile_char_limit(profile, "short", item_char_limit),
                ),
                "relationships": relationships,
                "appearances": appearances,
            })
        if characters:
            entry["characters"] = characters

    if "events" in fields:
        events = []
        for item in as_list(result.get("events"))[:profile["events"]]:
            if not isinstance(item, dict):
                continue
            summary = clip_text(
                item.get("summary") or item.get("description"),
                profile_char_limit(profile, "text", item_char_limit),
            )
            if not summary:
                continue
            events.append({
                "summary": summary,
                "type": clip_text(item.get("type"), profile_char_limit(profile, "short", item_char_limit)),
                "characters": clip_string_list(
                    item.get("characters"),
                    profile["event_characters"],
                    profile_char_limit(profile, "short", item_char_limit),
                ),
                "importance": clip_text(item.get("importance"), profile_char_limit(profile, "short", item_char_limit)),
                "cause": clip_text(item.get("cause"), profile_char_limit(profile, "text", item_char_limit)),
                "effect": clip_text(item.get("effect"), profile_char_limit(profile, "text", item_char_limit)),
            })
        if events:
            entry["events"] = events

    if "world_facts" in fields:
        source_facts = as_list(result.get("world_facts")) or as_list(result.get("worldbuilding_entries"))
        world_facts = []
        for item in source_facts[:profile["world_facts"]]:
            if not isinstance(item, dict):
                continue
            fact = clip_text(item.get("fact") or item.get("content"), profile_char_limit(profile, "text", item_char_limit))
            title = clip_text(item.get("name") or item.get("title"), profile_char_limit(profile, "short", item_char_limit))
            if not fact and not title:
                continue
            world_facts.append({
                "dimension": clip_text(item.get("dimension"), profile_char_limit(profile, "short", item_char_limit)),
                "name": title,
                "fact": fact,
                "evidence": clip_text(
                    item.get("evidence") or item.get("plot_usage"),
                    profile_char_limit(profile, "evidence", item_char_limit),
                ),
            })
        if world_facts:
            entry["world_facts"] = world_facts

    if "clues" in fields:
        source_clues = as_list(result.get("clues")) or as_list(result.get("highlights"))
        clues = []
        for item in source_clues[:profile["clues"]]:
            if not isinstance(item, dict):
                continue
            detail = clip_text(
                item.get("detail") or item.get("description"),
                profile_char_limit(profile, "text", item_char_limit),
            )
            name = clip_text(item.get("item") or item.get("type"), profile_char_limit(profile, "short", item_char_limit))
            if not detail and not name:
                continue
            clues.append({
                "item": name,
                "detail": detail,
                "payoff_hint": clip_text(item.get("payoff_hint"), profile_char_limit(profile, "evidence", item_char_limit)),
            })
        if clues:
            entry["clues"] = clues

    if "pacing" in fields and result.get("pacing"):
        entry["pacing"] = clip_text(result.get("pacing"), profile_char_limit(profile, "short", item_char_limit))
    if "narrative_mode" in fields and result.get("narrative_mode"):
        entry["narrative_mode"] = clip_text(result.get("narrative_mode"), profile_char_limit(profile, "short", item_char_limit))
    if "themes" in fields:
        themes = clip_string_list(
            result.get("themes") or result.get("key_themes"),
            profile["themes"],
            profile_char_limit(profile, "short", item_char_limit),
        )
        if themes:
            entry["themes"] = themes
    if "techniques" in fields:
        techniques = clip_string_list(
            result.get("techniques") or result.get("writing_techniques"),
            profile["techniques"],
            profile_char_limit(profile, "short", item_char_limit),
        )
        if techniques:
            entry["techniques"] = techniques
    return entry


def brief_map_result_for_reduce(result: dict, index: int, section_key: str, max_chars: int) -> dict:
    if not isinstance(result, dict):
        return {"chunk_index": index, "summary": "missing_result"}
    if result.get("_error"):
        return {"chunk_index": index, "summary": f"error:{clip_text(result.get('_error'), max_chars)}"}

    parts: list[str] = []
    if section_key == "worldbuilding":
        facts = as_list(result.get("world_facts")) or as_list(result.get("worldbuilding_entries"))
        parts.extend(
            squash_text(item.get("fact") or item.get("content") or item.get("title"))
            for item in facts
            if isinstance(item, dict)
        )
    elif section_key == "characters":
        chars = as_list(result.get("characters")) or as_list(result.get("character_profiles"))
        parts.extend(
            f"{item.get('name') or ''}:{item.get('role_hint') or item.get('role') or item.get('role_type') or ''}"
            for item in chars
            if isinstance(item, dict)
        )
    else:
        events = as_list(result.get("events"))
        parts.extend(
            squash_text(item.get("summary") or item.get("description"))
            for item in events
            if isinstance(item, dict)
        )
    if not parts:
        parts.extend(clip_string_list(result.get("themes") or result.get("techniques"), 3, 24))
    return {"chunk_index": index, "summary": clip_text("；".join(part for part in parts if part), max_chars)}


def reduce_source_payload(entries: list[dict], section_key: str, profile_name: str, omitted_chunks: int = 0) -> dict:
    return {
        "_meta": {
            "section": section_key,
            "profile": profile_name,
            "chunks": len(entries) + omitted_chunks,
            "omitted_chunks": omitted_chunks,
            "note": "输入已按单条字段限长压缩；省略号表示该字段被截断。",
        },
        "chunks": entries,
    }


def serialize_reduce_payload_with_budget(
    entries: list[dict],
    section_key: str,
    profile_name: str,
    input_char_limit: int,
) -> str:
    kept: list[dict] = []
    omitted = 0
    for entry in entries:
        candidate = kept + [entry]
        text = json.dumps(reduce_source_payload(candidate, section_key, profile_name, omitted), ensure_ascii=False, separators=(",", ":"))
        if len(text) <= input_char_limit:
            kept = candidate
        else:
            omitted += 1
    return json.dumps(reduce_source_payload(kept, section_key, profile_name, omitted), ensure_ascii=False, separators=(",", ":"))


def clean_map_results_for_reduce(map_results: list[dict], section_key: str, limits: ModelSafetyLimits) -> list[dict]:
    profile = REDUCE_INPUT_PROFILES[0]
    return [
        compact_map_result_for_reduce(result, index, section_key, profile, limits.deconstruct_item_char_limit)
        for index, result in enumerate(map_results)
    ]


def reduce_source_text(map_results: list[dict], section_key: str, limits: ModelSafetyLimits) -> str:
    input_char_limit = limits.deconstruct_input_char_limit or REDUCE_INPUT_MAX_CHARS
    item_char_limit = limits.deconstruct_item_char_limit or input_char_limit
    for profile in REDUCE_INPUT_PROFILES:
        entries = [
            compact_map_result_for_reduce(result, index, section_key, profile, item_char_limit)
            for index, result in enumerate(map_results)
        ]
        payload = reduce_source_payload(entries, section_key, profile["name"])
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(text) <= input_char_limit:
            return text

    per_chunk_chars = REDUCE_BRIEF_MAX_CHARS_PER_CHUNK
    if map_results:
        per_chunk_chars = max(
            REDUCE_BRIEF_MIN_CHARS_PER_CHUNK,
            min(REDUCE_BRIEF_MAX_CHARS_PER_CHUNK, (input_char_limit - 2000) // len(map_results)),
        )
    brief_entries = [
        brief_map_result_for_reduce(result, index, section_key, per_chunk_chars)
        for index, result in enumerate(map_results)
    ]
    text = json.dumps(reduce_source_payload(brief_entries, section_key, "brief"), ensure_ascii=False, separators=(",", ":"))
    if len(text) <= input_char_limit:
        return text
    return serialize_reduce_payload_with_budget(brief_entries, section_key, "brief-truncated", input_char_limit)


def guard_final_output(value: object, string_limit: int, depth: int = 0) -> object:
    if isinstance(value, str):
        return clip_text(value, string_limit)
    if isinstance(value, list):
        guarded = [
            guard_final_output(item, max(400, string_limit // 2), depth + 1)
            for item in value[:FINAL_OUTPUT_ARRAY_MAX_ITEMS]
        ]
        if len(value) > FINAL_OUTPUT_ARRAY_MAX_ITEMS:
            guarded.append({"_truncated_items": len(value) - FINAL_OUTPUT_ARRAY_MAX_ITEMS})
        return guarded
    if isinstance(value, dict):
        next_limit = max(400, string_limit // 2) if depth > 0 else string_limit
        return {str(key): guard_final_output(item, next_limit, depth + 1) for key, item in value.items()}
    return value


def sanitize_reduce_section_output(section_key: str, data: dict, limits: ModelSafetyLimits) -> dict:
    sanitized = guard_final_output(data, limits.deconstruct_item_char_limit)
    if isinstance(sanitized, dict):
        sanitized["_output_guarded"] = True
        sanitized["_section"] = section_key
        return sanitized
    return {"_error": "reduce_parse_failed", "_raw": "sanitized output is not an object"}


def default_reduce_result(options: dict) -> dict:
    return {
        "golden_three": None,
        "structure": {"volumes": [], "total_estimated_chapters": 0},
        "plot_nodes": [],
        "characters": [],
        "worldbuilding_entries": [],
        "highlights": [],
        "rhythm_curve": [] if options.get("rhythm") else None,
        "patterns": [] if options.get("patterns") else None,
        "reduce_sections": [],
        "reduce_errors": {},
    }


def reduce_section_keys(options: dict) -> list[str]:
    keys = []
    if options.get("outline"):
        keys.append("outline")
    if options.get("characters"):
        keys.append("characters")
    if options.get("worldbuilding"):
        keys.append("worldbuilding")
    if options.get("rhythm") or options.get("patterns"):
        keys.append("rhythm_patterns")
    if options.get("golden_three"):
        keys.append("golden_three")
    return keys


def merge_reduce_section(target: dict, section_key: str, section_data: dict, options: dict) -> None:
    if section_key == "outline":
        target["structure"] = section_data.get("structure") or target["structure"]
        target["plot_nodes"] = section_data.get("plot_nodes") or []
        target["highlights"] = section_data.get("highlights") or []
    elif section_key == "characters":
        target["characters"] = section_data.get("characters") or []
    elif section_key == "worldbuilding":
        target["worldbuilding_entries"] = section_data.get("worldbuilding_entries") or []
    elif section_key == "rhythm_patterns":
        target["rhythm_curve"] = section_data.get("rhythm_curve") if options.get("rhythm") else None
        target["patterns"] = section_data.get("patterns") if options.get("patterns") else None
    elif section_key == "golden_three":
        target["golden_three"] = section_data.get("golden_three")

    if section_data.get("structure") and not target["structure"].get("volumes"):
        target["structure"] = section_data.get("structure")
    if section_data.get("plot_nodes") and not target["plot_nodes"]:
        target["plot_nodes"] = section_data.get("plot_nodes")
    if section_data.get("characters") and not target["characters"]:
        target["characters"] = section_data.get("characters")
    if section_data.get("worldbuilding_entries") and not target["worldbuilding_entries"]:
        target["worldbuilding_entries"] = section_data.get("worldbuilding_entries")
    if section_data.get("highlights") and not target["highlights"]:
        target["highlights"] = section_data.get("highlights")
    if options.get("rhythm") and section_data.get("rhythm_curve") and not target.get("rhythm_curve"):
        target["rhythm_curve"] = section_data.get("rhythm_curve")
    if options.get("patterns") and section_data.get("patterns") and not target.get("patterns"):
        target["patterns"] = section_data.get("patterns")
    if section_data.get("golden_three") and not target.get("golden_three"):
        target["golden_three"] = section_data.get("golden_three")


def summarize_chunk_result(result: dict, index: int) -> dict:
    events = result.get("events") if isinstance(result.get("events"), list) else []
    characters = result.get("characters") if isinstance(result.get("characters"), list) else []
    highlights = result.get("highlights") if isinstance(result.get("highlights"), list) else []
    clues = result.get("clues") if isinstance(result.get("clues"), list) else []

    event_descriptions = [
        str(item.get("description") or item.get("summary") or "").strip()
        for item in events[:5]
        if isinstance(item, dict) and str(item.get("description") or item.get("summary") or "").strip()
    ]
    character_names = [
        str(item.get("name") or "").strip()
        for item in characters[:8]
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    highlight_descriptions = [
        str(item.get("description") or item.get("detail") or item.get("item") or "").strip()
        for item in highlights[:3]
        if isinstance(item, dict) and str(item.get("description") or item.get("detail") or item.get("item") or "").strip()
    ]
    if not highlight_descriptions:
        highlight_descriptions = [
            str(item.get("detail") or item.get("item") or "").strip()
            for item in clues[:3]
            if isinstance(item, dict) and str(item.get("detail") or item.get("item") or "").strip()
        ]

    if result.get("_error"):
        error_label = {
            "empty_response": "模型返回空内容，已自动重试但仍未得到JSON",
            "parse_failed": "模型返回内容不是合法JSON，已自动重试但仍解析失败",
            "truncated_json": "模型输出疑似被截断，已自动重试但仍无法修复",
            "repair_failed": "模型返回内容不是合法JSON，自动修复失败",
            "timeout": "模型调用超时，已跳过该分块以保证整体继续",
            "llm_failed": "模型调用失败",
            "missing_result": "该分块没有生成结果",
        }.get(result.get("_error"), "模型输出解析失败")
        raw = str(result.get("_raw") or "").strip()
        summary = f"{error_label}{f'：{raw[:240]}' if raw else ''}"
        status = "failed"
    else:
        summary = "；".join(event_descriptions[:3]) or "该分块已完成结构化分析"
        status = "completed"

    return {
        "index": index,
        "status": status,
        "summary": summary,
        "characters": character_names,
        "events": event_descriptions,
        "highlights": highlight_descriptions,
        "pacing": result.get("pacing"),
        "narrative_mode": result.get("narrative_mode"),
        "error": result.get("_error"),
        "raw": result,
    }


def build_source_from_payload(project, payload, db: Session) -> tuple[str, str, list[str], list]:
    title = payload.title or project.title
    chapter_ids = payload.chapter_ids or []

    if chapter_ids:
        chapters = (
            db.query(Chapter)
            .filter(Chapter.project_id == project.id, Chapter.id.in_(chapter_ids))
            .all()
        )
        chapter_map = {chapter.id: chapter for chapter in chapters}
        ordered = [chapter_map[chapter_id] for chapter_id in chapter_ids if chapter_id in chapter_map]
        if not ordered:
            raise ValidationError("请选择有效章节进行拆书")
        text = "\n\n".join(
            f"{'=' * 40}\n{chapter.title}\n{'=' * 40}\n\n{chapter.content or ''}"
            for chapter in ordered
        )
        if len(ordered) == 1:
            title = ordered[0].title
        else:
            title = f"{project.title}（{len(ordered)}章拆书）"
        return text, title, [chapter.id for chapter in ordered], ordered

    if payload.text and payload.text.strip():
        return payload.text.strip(), title, [], []

    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project.id)
        .order_by(Chapter.created_at.asc())
        .all()
    )
    if not chapters:
        raise ValidationError("当前作品没有可拆书的章节")
    text = "\n\n".join(
        f"{'=' * 40}\n{chapter.title}\n{'=' * 40}\n\n{chapter.content or ''}"
        for chapter in chapters
    )
    return text, f"{project.title}（全书拆书）", [chapter.id for chapter in chapters], chapters


def build_golden_three_source(project, payload, db: Session) -> tuple[str, list[str]]:
    """Golden-three analysis is limited to the project's first three chapters."""
    if payload.text and not payload.chapter_ids:
        return payload.text.strip()[:12000], []

    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project.id)
        .order_by(Chapter.created_at.asc())
        .limit(3)
        .all()
    )
    text = "\n\n".join(
        f"{'=' * 40}\n第 {index + 1} 章：{chapter.title}\n{'=' * 40}\n\n{chapter.content or ''}"
        for index, chapter in enumerate(chapters)
    )
    return text[:16000], [chapter.id for chapter in chapters]


def resolve_report_chunks(project, report_data: dict, db: Session) -> list[str]:
    chunks = report_data.get("source_chunks")
    if isinstance(chunks, list) and all(isinstance(item, str) for item in chunks):
        return chunks

    selected_ids = report_data.get("selected_chapter_ids") or []
    if selected_ids:
        chapters = (
            db.query(Chapter)
            .filter(Chapter.project_id == project.id, Chapter.id.in_(selected_ids))
            .all()
        )
        chapter_map = {chapter.id: chapter for chapter in chapters}
        ordered = [chapter_map[chapter_id] for chapter_id in selected_ids if chapter_id in chapter_map]
        return chapter_aware_chunks(ordered)

    raise ValidationError("该报告没有可重跑的原始分块；手动粘贴文本生成的旧报告需要重新开始拆书")


def build_map_messages(chunk: str, index: int, options: dict, retry_tip: str = "") -> list[dict]:
    return [
        {"role": "system", "content": MAP_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "{}{}\n\n"
                "请严格按这个JSON结构输出，不要省略外层花括号：\n{}\n\n"
                "分析以下小说文本片段（第{}段）：\n\n{}{}"
            ).format(map_instructions(options), MAP_OUTPUT_RULES, MAP_JSON_TEMPLATE, index + 1, chunk[:4000], retry_tip),
        },
    ]
