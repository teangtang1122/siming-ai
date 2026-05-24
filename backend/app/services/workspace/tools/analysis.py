"""Conflict suggestion, character change detection, and worldbuilding conflict detection workspace tools."""
from __future__ import annotations

import json as _json
import re as _re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ....ai.gateway import LLMGateway
from ....database.models import (
    Chapter,
    ChapterCharacter,
    Character,
    CharacterChangeLog,
    CharacterRelationship,
    CharacterTimeline,
    Project,
    WorldbuildingEntry,
)
from ....prompts.analysis_prompts import (
    build_character_change_messages,
    build_conflict_suggestion_messages,
    build_new_worldbuilding_messages,
    build_worldbuilding_conflict_messages,
)
from ....prompts.chapter_evaluation_prompts import build_chapter_evaluation_messages
from ....services.context_builders import (
    _build_outline_context,
    _build_recent_summaries,
)
from ....services.style_rules import _detect_forbidden_sentence_violations


async def suggest_conflicts(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    prompt = str(args.get("prompt") or "").strip() or None

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "suggest_conflicts", "status": "skipped", "detail": "项目不存在", "data": []}

    outline_ctx = _build_outline_context(db, project_id, outline_node_id) if outline_node_id else "未限定大纲节点。"
    summaries = _build_recent_summaries(db, project_id, 5)

    characters = (
        db.query(Character)
        .filter(Character.project_id == project_id)
        .order_by(Character.updated_at.desc())
        .limit(10)
        .all()
    )
    char_context = "\n".join(
        f"- {c.name}（{c.role_type or '未分类'}）: {(c.personality or '')[:200]}"
        for c in characters
    ) or "暂无角色。"

    relationships = (
        db.query(CharacterRelationship)
        .filter(CharacterRelationship.project_id == project_id)
        .limit(20)
        .all()
    )
    character_ids = {r.character_a_id for r in relationships} | {r.character_b_id for r in relationships}
    name_map = {
        c.id: c.name
        for c in db.query(Character).filter(Character.id.in_(character_ids)).all()
    }
    rel_context = "\n".join(
        f"- {name_map.get(r.character_a_id, r.character_a_id[:8])} ↔ {name_map.get(r.character_b_id, r.character_b_id[:8])}: {r.relationship_type}"
        for r in relationships
    ) or "暂无已知关系。"

    messages = build_conflict_suggestion_messages(
        project_title=project.title,
        project_description=project.description or "",
        outline_ctx=outline_ctx,
        summaries=summaries,
        char_context=char_context,
        rel_context=rel_context,
        prompt=prompt or "",
    )

    model = str(args.get("model") or "") or None
    temperature = float(args.get("temperature") or 0.8)

    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            timeout=120,
            retry=1,
        )
    except Exception as exc:
        return {"tool": "suggest_conflicts", "status": "error", "detail": f"LLM 调用失败：{exc}", "data": []}

    suggestion_text = result.get("content", "")
    parsed = None
    try:
        parsed = _json.loads(suggestion_text.strip().removeprefix("```json").removesuffix("```").strip())
    except _json.JSONDecodeError:
        parsed = None

    conflicts = parsed.get("conflicts", []) if parsed else []
    return {
        "tool": "suggest_conflicts",
        "status": "ok",
        "detail": f"已生成 {len(conflicts)} 条冲突建议",
        "data": {
            "conflicts": conflicts,
            "model": result.get("model"),
        },
    }


async def detect_character_changes(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Detect character changes from chapter content.

    Two modes:
    - content+title: detect changes against current character states, return only (no DB writes).
      Used before create_chapter so Agent can apply changes via update_character.
    - chapter_id: detect and save change logs / timeline entries to DB.
    """
    chapter_title: str = ""
    chapter_text: str = ""
    chapter_id: str = ""

    raw_content = str(args.get("content") or "").strip()
    if raw_content:
        chapter_text = raw_content
        chapter_title = str(args.get("title") or args.get("chapter_title") or "").strip() or "未命名章节"
    else:
        chapter_id = str(args.get("chapter_id") or "").strip()
        if not chapter_id:
            return {"tool": "detect_character_changes", "status": "skipped", "detail": "缺少章节ID或正文内容", "data": []}
        chapter = db.query(Chapter).filter(Chapter.id == chapter_id, Chapter.project_id == project_id).first()
        if not chapter:
            return {"tool": "detect_character_changes", "status": "skipped", "detail": "章节不存在", "data": []}
        chapter_title = chapter.title
        chapter_text = chapter.content or ""

    if not chapter_text.strip():
        return {"tool": "detect_character_changes", "status": "skipped", "detail": "章节正文为空", "data": []}

    characters = (
        db.query(Character)
        .filter(Character.project_id == project_id, Character.is_evolution_tracked == True)
        .all()
    )
    if not characters:
        return {"tool": "detect_character_changes", "status": "ok", "detail": "没有开启演化追踪的角色", "data": {"changes": [], "total": 0}}

    character_by_id = {c.id: c for c in characters}
    char_payload = [
        {
            "id": c.id,
            "name": c.name,
            "personality": c.personality,
            "abilities": c.abilities,
            "background": c.background,
            "role_type": c.role_type,
        }
        for c in characters
    ]

    if len(chapter_text) > 8000:
        chapter_text = chapter_text[:8000] + "\n...(后续内容已截断)"

    messages = build_character_change_messages(
        chapter_title=chapter_title,
        chapter_text=chapter_text,
        char_payload=_json.dumps(char_payload, ensure_ascii=False),
    )

    model = str(args.get("model") or "") or None
    temperature = float(args.get("temperature") or 0.3)

    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            timeout=120,
            retry=1,
        )
    except Exception as exc:
        return {"tool": "detect_character_changes", "status": "error", "detail": f"LLM 调用失败：{exc}", "data": []}

    changes_text = result.get("content", "")
    changes = []
    try:
        changes = _json.loads(changes_text.strip().removeprefix("```json").removesuffix("```").strip())
    except _json.JSONDecodeError:
        pass

    allowed_change_types = {"skill", "experience", "relationship", "personality"}
    default_field_by_type = {
        "skill": "abilities",
        "experience": "background",
        "relationship": "background",
        "personality": "personality",
    }
    allowed_fields = {"abilities", "personality", "background", "appearance"}
    timeline_type_by_change = {
        "skill": "skill_gain",
        "experience": "key_decision",
        "relationship": "relationship_change",
        "personality": "emotional_turning_point",
    }

    detected_changes: list[dict] = []
    if isinstance(changes, list):
        for change in changes:
            if not isinstance(change, dict):
                continue
            char_id = str(change.get("character_id", "")).strip()
            if char_id not in character_by_id:
                continue
            change_type = str(change.get("change_type", "experience")).strip()
            if change_type not in allowed_change_types:
                change_type = "experience"
            field_name = str(change.get("field_name") or default_field_by_type[change_type]).strip()
            if field_name not in allowed_fields:
                field_name = default_field_by_type[change_type]
            old_val = str(change.get("old_value", ""))[:2000] if change.get("old_value") else None
            new_val = str(change.get("new_value", ""))[:2000] if change.get("new_value") else None
            confidence = str(change.get("confidence", "medium") or "medium")

            detected_changes.append({
                "character_id": char_id,
                "character_name": character_by_id[char_id].name,
                "change_type": change_type,
                "field_name": field_name,
                "old_value": old_val,
                "new_value": new_val,
                "confidence": confidence,
            })

            # Persist logs only when chapter is already saved
            if chapter_id:
                log = CharacterChangeLog(
                    character_id=char_id,
                    chapter_id=chapter_id,
                    change_type=change_type,
                    field_name=field_name,
                    old_value=old_val,
                    new_value=new_val,
                    confirmed=False,
                )
                db.add(log)
                db.flush()

                existing_chapter_char = (
                    db.query(ChapterCharacter)
                    .filter(
                        ChapterCharacter.chapter_id == chapter_id,
                        ChapterCharacter.character_id == char_id,
                    )
                    .first()
                )
                if not existing_chapter_char:
                    db.add(ChapterCharacter(
                        chapter_id=chapter_id,
                        character_id=char_id,
                        appearance_type="AI演化追踪",
                        description=f"检测到{change_type}变化，可信度：{confidence}",
                    ))

                timeline_type = timeline_type_by_change.get(change_type, "key_decision")
                db.add(CharacterTimeline(
                    character_id=char_id,
                    chapter_id=chapter_id,
                    event_type=timeline_type,
                    event_description=f"[{change_type}] {field_name}: {new_val or '见原文'}",
                    emotional_state_change=new_val if change_type == "personality" else None,
                ))

    if chapter_id:
        db.commit()

    return {
        "tool": "detect_character_changes",
        "status": "ok",
        "detail": f"检测到 {len(detected_changes)} 处角色变化",
        "data": {
            "changes": detected_changes,
            "total": len(detected_changes),
        },
    }


DIMENSION_LABELS: dict[str, str] = {
    "geography": "地理",
    "history": "历史",
    "factions": "势力",
    "power_system": "规则体系",
    "races": "种族",
    "culture": "文化",
}


async def detect_worldbuilding_conflicts(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Detect logical contradictions between worldbuilding entries."""
    entries = (
        db.query(WorldbuildingEntry)
        .filter(WorldbuildingEntry.project_id == project_id)
        .order_by(WorldbuildingEntry.dimension.asc(), WorldbuildingEntry.sort_order.asc())
        .all()
    )
    if len(entries) < 2:
        return {"tool": "detect_worldbuilding_conflicts", "status": "ok", "detail": "条目不足2个，无需检测矛盾", "data": {"conflicts": [], "total": 0}}

    entry_payload = [
        {
            "id": entry.id,
            "dimension": entry.dimension,
            "dimension_label": DIMENSION_LABELS.get(entry.dimension, entry.dimension),
            "title": entry.title,
            "content": entry.content,
        }
        for entry in entries
    ]
    messages = build_worldbuilding_conflict_messages(
        entry_payload=_json.dumps(entry_payload, ensure_ascii=False),
    )

    model = str(args.get("model") or "") or None
    temperature = float(args.get("temperature") or 0.2)

    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            timeout=120,
            retry=1,
        )
    except Exception as exc:
        return {"tool": "detect_worldbuilding_conflicts", "status": "error", "detail": f"LLM 调用失败：{exc}", "data": []}

    analysis = result.get("content", "")
    valid_entry_ids = {entry.id for entry in entries}

    # Parse conflicts
    conflicts = []
    try:
        stripped = analysis.strip()
        fence_match = _re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=_re.DOTALL | _re.IGNORECASE)
        if fence_match:
            stripped = fence_match.group(1).strip()
        parsed = _json.loads(stripped)
        raw_conflicts = parsed.get("conflicts", parsed if isinstance(parsed, list) else [])
        if isinstance(raw_conflicts, list):
            for item in raw_conflicts:
                if not isinstance(item, dict):
                    continue
                entry_a = str(item.get("entry_a_id", ""))
                entry_b = str(item.get("entry_b_id", ""))
                if entry_a not in valid_entry_ids or entry_b not in valid_entry_ids:
                    continue
                conflicts.append({
                    "entry_a_id": entry_a,
                    "entry_b_id": entry_b,
                    "dimension": item.get("dimension", ""),
                    "severity": item.get("severity", "low"),
                    "summary": item.get("summary", ""),
                    "detail": item.get("detail", ""),
                })
    except (_json.JSONDecodeError, AttributeError):
        conflicts = []

    return {
        "tool": "detect_worldbuilding_conflicts",
        "status": "ok",
        "detail": f"发现 {len(conflicts)} 处设定矛盾" if conflicts else "暂未检测到明显设定矛盾",
        "data": {
            "conflicts": conflicts,
            "total": len(conflicts),
            "model": result.get("model"),
        },
    }


async def detect_new_worldbuilding(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Detect new worldbuilding concepts from chapter content.

    Compares chapter text against existing worldbuilding entries and returns
    suggested new entries for settings the chapter introduces but aren't yet
    recorded in the database. Read-only — no DB writes.
    """
    chapter_text = str(args.get("content") or "").strip()
    chapter_title = str(args.get("title") or "").strip() or "未命名章节"

    if not chapter_text:
        return {"tool": "detect_new_worldbuilding", "status": "skipped", "detail": "缺少章节正文（content）", "data": {"entries": [], "total": 0}}

    # Build lightweight summary of existing entries for the LLM
    entries = (
        db.query(WorldbuildingEntry)
        .filter(WorldbuildingEntry.project_id == project_id)
        .order_by(WorldbuildingEntry.dimension.asc())
        .all()
    )
    existing_summary = "\n".join(
        f"- [{DIMENSION_LABELS.get(e.dimension, e.dimension)}] {e.title}: {(e.content or '')[:150]}"
        for e in entries
    ) if entries else "暂无已有世界观设定。"

    if len(chapter_text) > 8000:
        chapter_text = chapter_text[:8000] + "\n...(后续内容已截断)"

    messages = build_new_worldbuilding_messages(
        chapter_title=chapter_title,
        chapter_text=chapter_text,
        existing_entries_summary=existing_summary,
    )

    model = str(args.get("model") or "") or None
    temperature = float(args.get("temperature") or 0.3)

    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            timeout=120,
            retry=1,
        )
    except Exception as exc:
        return {"tool": "detect_new_worldbuilding", "status": "error", "detail": f"LLM 调用失败：{exc}", "data": []}

    raw = result.get("content", "")
    entries_list = []
    try:
        clean = raw.strip().removeprefix("```json").removesuffix("```").strip()
        parsed = _json.loads(clean)
        raw_entries = parsed.get("entries", [])
        if isinstance(raw_entries, list):
            for item in raw_entries:
                if not isinstance(item, dict):
                    continue
                entries_list.append({
                    "title": str(item.get("title", "")).strip(),
                    "dimension": str(item.get("dimension", "culture")).strip(),
                    "content_hint": str(item.get("content_hint", "")).strip(),
                    "relevance": str(item.get("relevance", "medium")).strip(),
                })
    except (_json.JSONDecodeError, AttributeError):
        entries_list = []

    return {
        "tool": "detect_new_worldbuilding",
        "status": "ok",
        "detail": f"发现 {len(entries_list)} 个新设定建议" if entries_list else "未发现需要新建的设定",
        "data": {
            "entries": entries_list,
            "total": len(entries_list),
        },
    }


async def detect_forbidden_patterns(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    text = str(args.get("text") or "").strip()
    if not text:
        return {"tool": "detect_forbidden_patterns", "status": "skipped", "detail": "缺少要检测的文本（text）", "data": {"violations": [], "total": 0}}

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "detect_forbidden_patterns", "status": "skipped", "detail": "项目不存在", "data": {}}

    violations = _detect_forbidden_sentence_violations(text, project)
    return {
        "tool": "detect_forbidden_patterns",
        "status": "ok",
        "detail": f"检测到 {len(violations)} 处禁用句式" if violations else "未检测到禁用句式",
        "data": {
            "violations": violations,
            "total": len(violations),
        },
    }


async def evaluate_chapter(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Evaluate chapter quality using an 8-dimension 80-point rubric.

    Accepts either a chapter_id (for saved chapters) or raw content+title
    (for evaluating chapter_writer output before persisting).
    """
    chapter_title: str = ""
    chapter_content: str = ""

    raw_content = str(args.get("content") or "").strip()
    if raw_content:
        chapter_content = raw_content
        chapter_title = str(args.get("title") or args.get("chapter_title") or "").strip() or "未命名章节"
    else:
        chapter_id = str(args.get("chapter_id") or "").strip()
        if not chapter_id:
            return {"tool": "evaluate_chapter", "status": "skipped", "detail": "缺少章节ID或正文内容", "data": {}}
        chapter = db.query(Chapter).filter(Chapter.id == chapter_id, Chapter.project_id == project_id).first()
        if not chapter:
            return {"tool": "evaluate_chapter", "status": "skipped", "detail": "章节不存在", "data": {}}
        if not (chapter.content or "").strip():
            return {"tool": "evaluate_chapter", "status": "skipped", "detail": "章节正文为空", "data": {}}
        chapter_title = chapter.title
        chapter_content = chapter.content

    messages = build_chapter_evaluation_messages(
        chapter_title=chapter_title,
        chapter_content=chapter_content,
    )

    model = str(args.get("model") or "") or None
    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=0.2,
            timeout=120,
            retry=1,
        )
    except Exception as exc:
        return {"tool": "evaluate_chapter", "status": "error", "detail": f"LLM 调用失败：{exc}", "data": {}}

    raw = result.get("content", "")
    parsed = None
    try:
        clean = raw.strip().removeprefix("```json").removesuffix("```").strip()
        parsed = _json.loads(clean)
    except (_json.JSONDecodeError, AttributeError):
        parsed = None

    if not parsed:
        return {"tool": "evaluate_chapter", "status": "error", "detail": "评估结果解析失败", "data": {"raw": raw[:500]}}

    # Persist to DB when evaluating a saved chapter
    if not raw_content:
        try:
            chapter.quality_score = parsed.get("total_score")
            chapter.quality_detail = _json.dumps(parsed, ensure_ascii=False)
            chapter.quality_evaluated_at = datetime.utcnow()
            db.flush()
        except Exception:
            pass

    return {
        "tool": "evaluate_chapter",
        "status": "ok",
        "detail": f"总分 {parsed.get('total_score', 0)}/80 — {parsed.get('overall_assessment', '')[:80]}",
        "data": parsed,
    }
