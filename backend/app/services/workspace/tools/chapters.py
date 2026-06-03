"""Chapter workspace tools."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import (
    Chapter,
    ChapterCharacter,
    CharacterChangeLog,
    CharacterTimeline,
    ChapterSummary,
    Character,
    Project,
)
from ....services.style_rules import _repair_forbidden_sentence_text
from ..generated_drafts import resolve_chapter_draft_content
from ..utils import find_outline_by_title_or_id


def _character_names(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(name) for name in value if name]


def _link_chapter_characters(
    db: Session,
    project_id: str,
    chapter_id: str,
    names: list[str],
    label: str,
) -> None:
    if not names:
        return
    characters = (
        db.query(Character)
        .filter(Character.project_id == project_id, Character.name.in_(names))
        .all()
    )
    for character in characters:
        db.add(ChapterCharacter(
            chapter_id=chapter_id,
            character_id=character.id,
            appearance_type="涉及",
            description=label,
        ))


async def create_chapter(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    title = str(args.get("title") or "").strip()
    content = str(args.get("content") or "")
    content = resolve_chapter_draft_content(
        project_id=project_id,
        provided_content=content,
        draft_id=str(args.get("draft_id") or args.get("content_ref") or "").strip() or None,
        outline_node_id=str(args.get("outline_node_id") or "").strip() or None,
        db=db,
    )
    if not title or not content.strip():
        return {"tool": "create_chapter", "status": "skipped", "detail": "章节标题或正文为空"}

    from ..run_recovery import generate_idempotency_key, check_idempotency
    _idem_key = generate_idempotency_key(db, "create_chapter", project_id, args)
    if _idem_key:
        _existing = check_idempotency(db, project_id, _idem_key)
        if _existing:
            return _existing

    involved = _character_names(args.get("involved_characters"))

    project = db.query(Project).filter(Project.id == project_id).first()
    skip_style_repair = bool(args.get("skip_style_repair") or args.get("skip_forbidden_repair"))
    if project and content.strip() and not skip_style_repair:
        content, _violations, _remaining = await _repair_forbidden_sentence_text(
            content, project, str(args.get("model") or "") or None
        )

    outline_node = None
    for ref in (args.get("outline_node_id"), args.get("outline_node_title"), args.get("outline_title")):
        outline_node = find_outline_by_title_or_id(db, project_id, ref)
        if outline_node:
            break

    chapter = Chapter(
        project_id=project_id,
        outline_node_id=outline_node.id if outline_node else None,
        title=title[:200],
        content=content,
        word_count=len(content),
        current_version=1,
    )
    db.add(chapter)
    db.flush()

    summary_text = str(args.get("summary") or "").strip()
    if summary_text:
        db.add(ChapterSummary(
            chapter_id=chapter.id,
            summary_text=summary_text[:20000],
            token_count=len(summary_text),
            ai_model=str(args.get("model") or "") or None,
        ))

    _link_chapter_characters(
        db, project_id, chapter.id, involved,
        f"由AI助手关联至章节「{title[:50]}」",
    )

    return {
        "tool": "create_chapter",
        "status": "ok",
        "detail": f"已创建章节：{chapter.title}（{len(content)} 字）",
        "data": {"id": chapter.id, "title": chapter.title, "word_count": len(content)},
    }


async def update_chapter(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    chapter = None
    for ref in (args.get("id"), args.get("chapter_id")):
        text = str(ref or "").strip()
        if text:
            chapter = db.query(Chapter).filter(
                Chapter.project_id == project_id, Chapter.id == text
            ).first()
            if chapter:
                break
    if not chapter:
        title_ref = str(args.get("title") or args.get("chapter_title") or "").strip()
        if title_ref:
            chapter = db.query(Chapter).filter(
                Chapter.project_id == project_id, Chapter.title == title_ref
            ).order_by(Chapter.created_at.desc()).first()
    if not chapter:
        outline_node = None
        for ref in (args.get("outline_node_id"), args.get("outline_node_title"), args.get("outline_title")):
            outline_node = find_outline_by_title_or_id(db, project_id, ref)
            if outline_node:
                break
        if outline_node:
            chapter = (
                db.query(Chapter)
                .filter(Chapter.project_id == project_id, Chapter.outline_node_id == outline_node.id)
                .order_by(Chapter.created_at.desc())
                .first()
            )
    if not chapter:
        return {"tool": "update_chapter", "status": "skipped", "detail": "未找到章节"}

    if args.get("title"):
        chapter.title = str(args.get("title")).strip()[:200]
    if "content" in args:
        new_content = str(args.get("content") or "")
        new_content = resolve_chapter_draft_content(
            project_id=project_id,
            provided_content=new_content,
            draft_id=str(args.get("draft_id") or args.get("content_ref") or "").strip() or None,
            outline_node_id=str(args.get("outline_node_id") or "").strip() or None,
            db=db,
        )
        project = db.query(Project).filter(Project.id == project_id).first()
        skip_style_repair = bool(args.get("skip_style_repair") or args.get("skip_forbidden_repair"))
        if project and new_content.strip() and not skip_style_repair:
            new_content, _violations, _remaining = await _repair_forbidden_sentence_text(
                new_content, project, str(args.get("model") or "") or None
            )
        chapter.content = new_content
        chapter.word_count = len(chapter.content)

    outline_node = None
    for ref in (args.get("outline_node_id"), args.get("outline_node_title"), args.get("outline_title")):
        outline_node = find_outline_by_title_or_id(db, project_id, ref)
        if outline_node:
            chapter.outline_node_id = outline_node.id
            break

    chapter.current_version = max(1, chapter.current_version or 1) + 1
    chapter.updated_at = datetime.utcnow()

    summary_text = str(args.get("summary") or "").strip()
    if summary_text:
        if chapter.summary:
            chapter.summary.summary_text = summary_text[:20000]
            chapter.summary.token_count = len(summary_text)
            chapter.summary.updated_at = datetime.utcnow()
            chapter.summary.ai_model = str(args.get("model") or "") or chapter.summary.ai_model
        else:
            db.add(ChapterSummary(
                chapter_id=chapter.id,
                summary_text=summary_text[:20000],
                token_count=len(summary_text),
                ai_model=str(args.get("model") or "") or None,
            ))

    if "involved_characters" in args:
        db.query(ChapterCharacter).filter(ChapterCharacter.chapter_id == chapter.id).delete()
        _link_chapter_characters(
            db, project_id, chapter.id,
            _character_names(args.get("involved_characters")),
            f"由AI助手更新章节「{chapter.title[:50]}」",
        )

    return {
        "tool": "update_chapter",
        "status": "ok",
        "detail": f"已更新章节：{chapter.title}（{len(chapter.content or '')} 字）",
        "data": {"id": chapter.id, "title": chapter.title, "word_count": len(chapter.content or "")},
    }


async def delete_chapter(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    chapter = None
    for ref in (args.get("id"), args.get("chapter_id")):
        text = str(ref or "").strip()
        if text:
            chapter = db.query(Chapter).filter(
                Chapter.project_id == project_id, Chapter.id == text
            ).first()
            if chapter:
                break
    if not chapter:
        title_ref = str(args.get("title") or args.get("chapter_title") or "").strip()
        if title_ref:
            chapter = db.query(Chapter).filter(
                Chapter.project_id == project_id, Chapter.title == title_ref
            ).first()
    if not chapter:
        return {"tool": "delete_chapter", "status": "skipped", "detail": "未找到章节"}

    title = chapter.title

    # Revert character changes introduced in this chapter
    change_logs = db.query(CharacterChangeLog).filter(
        CharacterChangeLog.chapter_id == chapter.id, CharacterChangeLog.confirmed == True
    ).all()
    reverted: list[str] = []
    for log_entry in change_logs:
        character = db.query(Character).filter(Character.id == log_entry.character_id).first()
        if character and log_entry.field_name in ("abilities", "personality", "background", "appearance"):
            old_val = log_entry.old_value
            if old_val and old_val != "（档案中无记录）":
                setattr(character, log_entry.field_name, old_val)
                reverted.append(character.name)
    if reverted:
        db.flush()

    db.query(CharacterChangeLog).filter(CharacterChangeLog.chapter_id == chapter.id).delete()
    db.query(CharacterTimeline).filter(CharacterTimeline.chapter_id == chapter.id).delete()
    db.query(ChapterCharacter).filter(ChapterCharacter.chapter_id == chapter.id).delete()
    db.query(ChapterSummary).filter(ChapterSummary.chapter_id == chapter.id).delete()
    db.delete(chapter)
    db.flush()

    detail = f"已删除章节：{title}"
    if reverted:
        detail += f"，已回退 {len(reverted)} 个角色的状态（{', '.join(reverted)}）"
    return {"tool": "delete_chapter", "status": "ok", "detail": detail}
