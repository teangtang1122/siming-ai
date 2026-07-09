"""Story granularity archive, audit, and repair tools."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import (
    CatalogingCandidate,
    CatalogingChapterRun,
    CatalogingJob,
    Chapter,
    ChapterCharacter,
    Character,
    OutlineNode,
    Project,
)
from ....services.cataloging.applier import apply_candidates_for_run
from ....services.cataloging.candidate_store import create_candidate_from_raw
from ....services.cataloging.candidate_validation import inspect_candidate_coverage
from ....services.story_granularity import (
    CHARACTER_STATE_FIELDS,
    chapter_outline_node,
    estimate_scene_count,
    granularity_contract_prompt,
    inspect_candidate_coverage_items,
    inspect_chapter_granularity,
)
from ..generated_drafts import resolve_chapter_draft_content


def _clean_text(value: Any, limit: int = 1200) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _summary_from_content(chapter: Chapter, content: str) -> tuple[str, list[str], list[dict[str, str]]]:
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n+", content or "") if p.strip()]
    if not paragraphs:
        paragraphs = [_clean_text(content, 600)] if content else []
    key_events = [p[:180] for p in paragraphs[:5] if p]
    summary_seed = "；".join(key_events[:3]) if key_events else "正文已保存，等待后续精修建档。"
    summary = f"{chapter.title}：{summary_seed}"
    scene_count = estimate_scene_count(content)
    if scene_count <= 1:
        return summary[:2000], key_events, []
    chunk_size = max(1, len(paragraphs) // scene_count)
    scenes: list[dict[str, str]] = []
    for index in range(scene_count):
        chunk = paragraphs[index * chunk_size:(index + 1) * chunk_size]
        if not chunk and paragraphs:
            chunk = [paragraphs[min(index, len(paragraphs) - 1)]]
        scene_summary = "；".join(chunk[:2])[:500] or summary[:300]
        scenes.append({
            "title": f"场景{index + 1}",
            "summary": scene_summary,
        })
    return summary[:2000], key_events, scenes[:6]


def _linked_characters_for_chapter(db: Session, project_id: str, chapter: Chapter, content: str) -> list[Character]:
    linked_ids = {
        row.character_id
        for row in db.query(ChapterCharacter).filter(ChapterCharacter.chapter_id == chapter.id).all()
        if row.character_id
    }
    characters: list[Character] = []
    if linked_ids:
        characters.extend(db.query(Character).filter(Character.id.in_(linked_ids)).all())
    seen = {c.id for c in characters}
    if len(characters) < 12:
        for character in db.query(Character).filter(Character.project_id == project_id).limit(80).all():
            if character.id in seen:
                continue
            if character.name and character.name in content:
                characters.append(character)
                seen.add(character.id)
            if len(characters) >= 12:
                break
    return characters[:12]


def _fallback_candidates(db: Session, project_id: str, chapter: Chapter, content: str) -> list[dict[str, Any]]:
    outline = chapter_outline_node(db, project_id, chapter)
    chapter_title = outline.title if outline else chapter.title
    summary, key_events, scenes = _summary_from_content(chapter, content)
    candidates: list[dict[str, Any]] = [
        {
            "type": "chapter_summary",
            "summary_text": summary,
            "key_events": key_events,
            "scene_count": max(1, len(scenes) or estimate_scene_count(content)),
            "scenes": scenes,
            "confidence": 0.55,
            "evidence": "写章后自动归档兜底摘要",
        },
        {
            "type": "outline_update" if outline else "outline_create",
            "title": chapter_title,
            "node_type": "chapter",
            "summary": summary,
            "actual_summary": summary,
            "status": "completed",
            "confidence": 0.55,
            "evidence": "写章后自动归档兜底大纲",
        },
    ]
    if scenes:
        for index, scene in enumerate(scenes[:6], start=1):
            candidates.append({
                "type": "outline_create",
                "title": f"{chapter_title} / 场景{index}：{scene['title']}",
                "node_type": "section",
                "parent_title": chapter_title,
                "summary": scene["summary"],
                "actual_summary": scene["summary"],
                "status": "completed",
                "confidence": 0.5,
                "evidence": "写章后自动归档兜底场景",
            })
    for character in _linked_characters_for_chapter(db, project_id, chapter, content):
        payload = {
            "type": "character_state_update",
            "id": character.id,
            "name": character.name,
            "life_status": character.life_status or "unknown",
            "current_location": character.current_location or "本章正文未明示",
            "physical_state": character.physical_state or "本章正文未明示变化",
            "mental_state": character.mental_state or "本章正文未明示变化",
            "current_goal": character.current_goal or "本章正文未明示",
            "active_conflict": character.active_conflict or "",
            "realm_or_level": character.realm_or_level or "",
            "abilities_state": character.abilities_state or "",
            "items_or_assets": character.items_or_assets or "",
            "confidence": 0.45,
            "evidence": "写章后自动归档兜底角色状态",
        }
        if character.appearance:
            payload["appearance"] = character.appearance
        if character.age:
            payload["age"] = character.age
        candidates.append(payload)
    return candidates


def _parse_candidate_output(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            items = parsed.get("candidates") or parsed.get("items") or parsed.get("nodes")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    except Exception:
        pass
    items: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if isinstance(parsed, dict):
            items.append(parsed)
    return items


async def _generate_candidates_with_llm(
    db: Session,
    project_id: str,
    chapter: Chapter,
    content: str,
    *,
    model: str | None,
    source: str,
) -> list[dict[str, Any]]:
    if not model:
        return []
    from ....ai.gateway import LLMGateway
    project = db.query(Project).filter(Project.id == project_id).first()
    characters = _linked_characters_for_chapter(db, project_id, chapter, content)
    outline = chapter_outline_node(db, project_id, chapter)
    char_payload = [
        {
            "id": c.id,
            "name": c.name,
            "aliases": [a.alias for a in (c.aliases or []) if a.alias],
            "age": c.age,
            "appearance": c.appearance,
            "current_location": c.current_location,
            "physical_state": c.physical_state,
            "mental_state": c.mental_state,
            "current_goal": c.current_goal,
            "active_conflict": c.active_conflict,
        }
        for c in characters
    ]
    system = (
        "你是司命的写后建档器。只输出 JSONL；每行一个候选对象，不要输出 Markdown。"
        "候选必须可直接交给 save/apply cataloging candidate 流程。"
        f"\n{granularity_contract_prompt()}"
    )
    user = {
        "project_title": project.title if project else "",
        "chapter": {"id": chapter.id, "title": chapter.title},
        "outline": {
            "id": outline.id,
            "title": outline.title,
            "summary": outline.summary,
            "node_type": outline.node_type,
        } if outline else None,
        "characters": char_payload,
        "state_fields_required": list(CHARACTER_STATE_FIELDS),
        "source": source,
        "chapter_text": content[:12000],
    }
    try:
        result = await LLMGateway.chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            model=model,
            temperature=0.2,
            max_tokens=4000,
            timeout=120,
            retry=1,
            extra_body={"moshu_task_type": "cataloging", "moshu_project_id": project_id},
        )
    except Exception:
        return []
    return _parse_candidate_output(result.get("content") or "")


def _resolve_chapter(db: Session, project_id: str, args: dict[str, Any]) -> tuple[Chapter | None, str]:
    chapter_id = str(args.get("chapter_id") or args.get("id") or "").strip()
    if chapter_id:
        chapter = db.query(Chapter).filter(Chapter.project_id == project_id, Chapter.id == chapter_id).first()
        if chapter:
            return chapter, ""
        return None, "章节不存在"
    outline_node_id = str(args.get("outline_node_id") or "").strip()
    if outline_node_id:
        chapter = (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.outline_node_id == outline_node_id)
            .order_by(Chapter.created_at.desc())
            .first()
        )
        if chapter:
            return chapter, ""
    return None, "缺少已保存章节ID；请先 create_chapter 再归档"


def _raw_candidates(args: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = args.get("candidates")
    if isinstance(candidates, dict):
        for key in ("candidates", "items", "nodes"):
            if isinstance(candidates.get(key), list):
                candidates = candidates[key]
                break
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, dict)]


def _ensure_minimum_candidates(
    db: Session,
    project_id: str,
    chapter: Chapter,
    content: str,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    coverage = inspect_candidate_coverage_items(candidates)
    fallback = _fallback_candidates(db, project_id, chapter, content)
    if coverage.has_chapter_summary and coverage.has_chapter_outline:
        if coverage.character_state_count > 0:
            return candidates
        state_fallback = [item for item in fallback if item.get("type") == "character_state_update"]
        return [*candidates, *state_fallback]
    needed = list(candidates)
    if not coverage.has_chapter_summary:
        needed.extend(item for item in fallback if item.get("type") == "chapter_summary")
    if not coverage.has_chapter_outline:
        needed.extend(item for item in fallback if str(item.get("type")) in {"outline_create", "outline_update"} and item.get("node_type") == "chapter")
    if coverage.section_count == 0:
        needed.extend(item for item in fallback if str(item.get("type")) in {"outline_create", "outline_update"} and item.get("node_type") == "section")
    if coverage.character_state_count == 0:
        needed.extend(item for item in fallback if item.get("type") == "character_state_update")
    return needed


async def archive_chapter_after_write(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Create and optionally apply standard cataloging candidates after writing."""
    mode = str(args.get("mode") or "auto").strip().lower()
    mode = mode if mode in {"auto", "manual"} else "auto"
    source = str(args.get("source") or "internal_writer").strip() or "internal_writer"
    generate_if_missing = bool(args.get("generate_if_missing", True))
    chapter, error = _resolve_chapter(db, project_id, args)
    if not chapter:
        return {"tool": "archive_chapter_after_write", "status": "skipped", "detail": error, "data": None}

    content = resolve_chapter_draft_content(
        project_id=project_id,
        provided_content=str(args.get("content") or "").strip(),
        draft_id=str(args.get("draft_id") or args.get("content_ref") or "").strip() or None,
        outline_node_id=str(args.get("outline_node_id") or chapter.outline_node_id or "").strip() or None,
        db=db,
    ).strip() or chapter.content or ""
    if not content.strip():
        return {"tool": "archive_chapter_after_write", "status": "skipped", "detail": "章节正文为空", "data": None}

    candidates = _raw_candidates(args)
    generated_by = "provided"
    if not candidates and generate_if_missing:
        model = str(args.get("model") or "").strip() or None
        candidates = await _generate_candidates_with_llm(
            db,
            project_id,
            chapter,
            content,
            model=model,
            source=source,
        )
        generated_by = "llm" if candidates else "fallback"
    if generate_if_missing:
        candidates = _ensure_minimum_candidates(db, project_id, chapter, content, candidates)
    if not candidates:
        return {
            "tool": "archive_chapter_after_write",
            "status": "skipped",
            "detail": "没有可归档候选",
            "data": None,
        }

    job = CatalogingJob(
        project_id=project_id,
        status="running",
        execution_mode=mode,
        execution_backend="post_write",
        total_chapters=1,
        completed_chapters=0,
        failed_chapters=0,
        model=str(args.get("model") or "")[:200] or None,
        model_source=source[:50],
        provider=str(args.get("provider") or "")[:80] or None,
    )
    db.add(job)
    db.flush()
    run = CatalogingChapterRun(
        job_id=job.id,
        project_id=project_id,
        chapter_id=chapter.id,
        status="extracting",
        chapter_order=0,
        started_at=datetime.utcnow(),
    )
    db.add(run)
    db.flush()

    created: list[CatalogingCandidate] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, raw in enumerate(candidates):
        result = create_candidate_from_raw(
            db,
            job,
            run,
            raw,
            index,
            source_task=f"post_write:{source}"[:50],
        )
        if result.get("candidate"):
            created.append(result["candidate"])
        elif result.get("skipped") or result.get("duplicate"):
            skipped.append(result)
        elif result.get("error"):
            errors.append(result)
    db.flush()

    coverage = inspect_candidate_coverage(created)
    warnings = list(coverage.warnings)
    if errors:
        warnings.append("candidate_parse_errors")
    applied_events: list[dict[str, Any]] = []
    if not coverage.is_complete:
        run.status = "failed"
        run.error = "归档候选缺少：" + ", ".join(coverage.missing)
        job.status = "paused_on_failure"
        job.failed_chapters = 1
        job.error = run.error
    elif mode == "manual":
        run.status = "awaiting_confirmation"
        job.status = "waiting_confirmation"
        job.blocked_chapter_id = chapter.id
    else:
        run.status = "applying"
        db.flush()
        applied_events = apply_candidates_for_run(db, job, run)
        has_failed = any(event.get("type") == "candidate_apply_failed" for event in applied_events)
        run.status = "completed_with_warnings" if has_failed else "completed"
        run.completed_at = datetime.utcnow()
        run.updated_at = datetime.utcnow()
        job.status = "completed_with_warnings" if has_failed else "completed"
        job.completed_chapters = 1
        job.failed_chapters = 1 if has_failed else 0
        job.last_completed_chapter_id = chapter.id if not has_failed else None
        job.completed_at = datetime.utcnow()
    job.updated_at = datetime.utcnow()
    db.commit()

    return {
        "tool": "archive_chapter_after_write",
        "status": "ok" if coverage.is_complete else "error",
        "detail": f"写后归档：候选 {len(created)} 条，应用 {len(applied_events)} 条",
        "data": {
            "job_id": job.id,
            "chapter_run_id": run.id,
            "chapter_id": chapter.id,
            "mode": mode,
            "source": source,
            "generated_by": generated_by,
            "candidate_count": len(created),
            "applied_count": len(applied_events),
            "skipped_count": len(skipped),
            "error_count": len(errors),
            "coverage": coverage.to_dict(),
            "warnings": warnings,
            "applied_events": applied_events,
        },
    }


async def inspect_story_granularity(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    chapter_id = str(args.get("chapter_id") or "").strip()
    limit = max(1, min(500, int(args.get("limit") or 200)))
    query = db.query(Chapter).filter(Chapter.project_id == project_id)
    if chapter_id:
        query = query.filter(Chapter.id == chapter_id)
    chapters = query.order_by(Chapter.created_at.asc()).limit(limit).all()
    items = [inspect_chapter_granularity(db, project_id, chapter) for chapter in chapters]
    missing_counts: dict[str, int] = {}
    warning_counts: dict[str, int] = {}
    for item in items:
        for key in item["missing"]:
            missing_counts[key] = missing_counts.get(key, 0) + 1
        for key in item["warnings"]:
            warning_counts[key] = warning_counts.get(key, 0) + 1
    return {
        "tool": "inspect_story_granularity",
        "status": "ok",
        "detail": f"已审计 {len(items)} 个章节，发现 {sum(missing_counts.values())} 个硬缺口、{sum(warning_counts.values())} 个警告",
        "data": {
            "chapters_checked": len(items),
            "missing_counts": missing_counts,
            "warning_counts": warning_counts,
            "chapters": items,
        },
    }


async def repair_story_granularity(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    mode = str(args.get("mode") or "manual").strip().lower()
    mode = mode if mode in {"auto", "manual"} else "manual"
    chapter_id = str(args.get("chapter_id") or "").strip()
    limit = max(1, min(100, int(args.get("limit") or 20)))
    query = db.query(Chapter).filter(Chapter.project_id == project_id)
    if chapter_id:
        query = query.filter(Chapter.id == chapter_id)
    chapters = query.order_by(Chapter.created_at.asc()).limit(limit).all()
    repaired: list[dict[str, Any]] = []
    for chapter in chapters:
        audit = inspect_chapter_granularity(db, project_id, chapter)
        if audit["ok"] and not bool(args.get("force")):
            continue
        result = await archive_chapter_after_write(db, project_id, {
            "chapter_id": chapter.id,
            "outline_node_id": chapter.outline_node_id,
            "mode": mode,
            "source": "repair",
            "generate_if_missing": True,
            "model": args.get("model"),
        })
        repaired.append({
            "chapter_id": chapter.id,
            "title": chapter.title,
            "status": result.get("status"),
            "data": result.get("data"),
        })
    return {
        "tool": "repair_story_granularity",
        "status": "ok",
        "detail": f"已创建 {len(repaired)} 个颗粒度修复归档运行",
        "data": {
            "mode": mode,
            "repaired": repaired,
        },
    }
