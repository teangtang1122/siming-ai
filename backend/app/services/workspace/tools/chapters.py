"""Chapter workspace tools."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ....core.utils import count_words
from ....database.models import (
    Chapter,
    ChapterCharacter,
    ChapterSnapshot,
    CharacterChangeLog,
    CharacterTimeline,
    ChapterSummary,
    Character,
    Project,
)
from ....services.chapter_service import (
    create_snapshot,
    diff_snapshots,
    ensure_current_snapshot,
    restore_chapter_from_snapshot,
    snapshot_to_item,
)
from ....services.content_store import delete_project_file, sync_chapter_to_file
from ....services.narrative_ledger import restore_ledger_checkpoint
from ....services.style_rules import _repair_forbidden_sentence_text
from ..generated_drafts import get_chapter_draft_meta, resolve_chapter_draft_content
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


def _find_chapter(db: Session, project_id: str, args: dict[str, Any]) -> Chapter | None:
    for ref in (args.get("id"), args.get("chapter_id")):
        text = str(ref or "").strip()
        if text:
            chapter = db.query(Chapter).filter(
                Chapter.project_id == project_id,
                Chapter.id == text,
            ).first()
            if chapter:
                return chapter
    title_ref = str(args.get("title") or args.get("chapter_title") or "").strip()
    if title_ref:
        chapter = (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.title == title_ref)
            .order_by(Chapter.created_at.desc())
            .first()
        )
        if chapter:
            return chapter
    outline_node = None
    for ref in (args.get("outline_node_id"), args.get("outline_node_title"), args.get("outline_title")):
        outline_node = find_outline_by_title_or_id(db, project_id, ref)
        if outline_node:
            break
    if outline_node:
        return (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.outline_node_id == outline_node.id)
            .order_by(Chapter.created_at.desc())
            .first()
        )
    return None


def _chapter_version_data(chapter: Chapter) -> dict[str, Any]:
    return {
        "id": chapter.id,
        "chapter_id": chapter.id,
        "title": chapter.title,
        "word_count": chapter.word_count or 0,
        "current_version": chapter.current_version or 1,
        "updated_at": chapter.updated_at.isoformat() if chapter.updated_at else None,
    }


def _chapter_snapshots(db: Session, chapter: Chapter) -> list[ChapterSnapshot]:
    return (
        db.query(ChapterSnapshot)
        .filter(ChapterSnapshot.chapter_id == chapter.id)
        .order_by(ChapterSnapshot.version_number.desc(), ChapterSnapshot.created_at.desc())
        .all()
    )


def _find_snapshot(db: Session, chapter: Chapter, args: dict[str, Any]) -> ChapterSnapshot | None:
    snapshot_id = str(args.get("snapshot_id") or args.get("version_id") or "").strip()
    if snapshot_id:
        return (
            db.query(ChapterSnapshot)
            .filter(ChapterSnapshot.chapter_id == chapter.id, ChapterSnapshot.id == snapshot_id)
            .first()
        )
    raw_version = args.get("version_number")
    if raw_version in (None, ""):
        raw_version = args.get("version")
    if raw_version not in (None, ""):
        try:
            version_number = int(raw_version)
        except (TypeError, ValueError):
            version_number = None
        if version_number:
            return (
                db.query(ChapterSnapshot)
                .filter(
                    ChapterSnapshot.chapter_id == chapter.id,
                    ChapterSnapshot.version_number == version_number,
                )
                .order_by(ChapterSnapshot.created_at.desc())
                .first()
            )
    snapshots = _chapter_snapshots(db, chapter)
    target = str(args.get("target") or "previous").strip().lower()
    if target in {"first", "initial", "oldest", "最初", "初版", "第一版"}:
        return snapshots[-1] if snapshots else None
    if target in {"latest", "newest", "最新"}:
        return snapshots[0] if snapshots else None
    current_version = chapter.current_version or 1
    for snapshot in snapshots:
        if (snapshot.version_number or 0) < current_version:
            return snapshot
    return None


async def create_chapter(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    draft_id = str(args.get("draft_id") or args.get("content_ref") or "").strip() or None
    draft_meta = get_chapter_draft_meta(project_id, draft_id, db=db) if draft_id else None
    title = str(args.get("title") or (draft_meta or {}).get("title") or "").strip()
    content = str(args.get("content") or "")
    content = resolve_chapter_draft_content(
        project_id=project_id,
        provided_content=content,
        draft_id=draft_id,
        outline_node_id=str(args.get("outline_node_id") or (draft_meta or {}).get("outline_node_id") or "").strip() or None,
        db=db,
    )
    outline_node = None
    for ref in (
        args.get("outline_node_id") or (draft_meta or {}).get("outline_node_id"),
        args.get("outline_node_title"),
        args.get("outline_title"),
    ):
        outline_node = find_outline_by_title_or_id(db, project_id, ref)
        if outline_node:
            break
    if not title and outline_node:
        title = str(outline_node.title or "").strip()
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

    chapter = Chapter(
        project_id=project_id,
        outline_node_id=outline_node.id if outline_node else None,
        title=title[:200],
        content=content,
        word_count=count_words(content),
        current_version=1,
    )
    db.add(chapter)
    db.flush()
    db.add(create_snapshot(chapter, "ai_insert"))
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
    if project:
        sync_chapter_to_file(db, project, chapter)
        db.flush()

    return {
        "tool": "create_chapter",
        "status": "ok",
        "detail": f"已创建章节：{chapter.title}（{count_words(content)} 字）",
        "data": {
            "id": chapter.id,
            "chapter_id": chapter.id,
            "title": chapter.title,
            "word_count": count_words(content),
            "current_version": chapter.current_version or 1,
            "snapshot_count": 1,
        },
    }


async def update_chapter(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    project = db.query(Project).filter(Project.id == project_id).first()
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {"tool": "update_chapter", "status": "skipped", "detail": "未找到章节"}

    ensure_current_snapshot(db, chapter, "manual_save")

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
        skip_style_repair = bool(args.get("skip_style_repair") or args.get("skip_forbidden_repair"))
        if project and new_content.strip() and not skip_style_repair:
            new_content, _violations, _remaining = await _repair_forbidden_sentence_text(
                new_content, project, str(args.get("model") or "") or None
            )
        chapter.content = new_content
        chapter.word_count = count_words(chapter.content)

    outline_node = None
    for ref in (args.get("outline_node_id"), args.get("outline_node_title"), args.get("outline_title")):
        outline_node = find_outline_by_title_or_id(db, project_id, ref)
        if outline_node:
            chapter.outline_node_id = outline_node.id
            break

    chapter.current_version = max(1, chapter.current_version or 1) + 1
    chapter.updated_at = datetime.utcnow()
    trigger_type = (str(args.get("trigger_type") or "ai_insert").strip() or "ai_insert")[:50]
    db.add(create_snapshot(chapter, trigger_type))

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
    if project:
        sync_chapter_to_file(db, project, chapter)
        db.flush()

    return {
        "tool": "update_chapter",
        "status": "ok",
        "detail": f"已更新章节：{chapter.title}（{count_words(chapter.content or '')} 字）",
        "data": {
            "id": chapter.id,
            "chapter_id": chapter.id,
            "title": chapter.title,
            "word_count": count_words(chapter.content or ""),
            "current_version": chapter.current_version or 1,
        },
    }


async def list_chapter_versions(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {"tool": "list_chapter_versions", "status": "skipped", "detail": "未找到章节", "data": None}
    snapshots = _chapter_snapshots(db, chapter)
    items = [snapshot_to_item(snapshot) for snapshot in snapshots]
    return {
        "tool": "list_chapter_versions",
        "status": "ok",
        "detail": f"章节「{chapter.title}」共有 {len(items)} 个版本快照，当前 v{chapter.current_version or 1}",
        "data": {
            "chapter": _chapter_version_data(chapter),
            "items": items,
            "total": len(items),
        },
    }


async def restore_chapter_version(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    project = db.query(Project).filter(Project.id == project_id).first()
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {"tool": "restore_chapter_version", "status": "skipped", "detail": "未找到章节", "data": None}
    snapshot = _find_snapshot(db, chapter, args)
    if not snapshot:
        return {
            "tool": "restore_chapter_version",
            "status": "skipped",
            "detail": "没有找到可恢复的版本；请先调用 list_chapter_versions 查看可用快照",
            "data": {"chapter": _chapter_version_data(chapter), "items": [snapshot_to_item(s) for s in _chapter_snapshots(db, chapter)]},
        }
    if (snapshot.version_number or 0) >= (chapter.current_version or 1) and not (
        args.get("snapshot_id") or args.get("version_id") or args.get("version_number")
    ):
        return {
            "tool": "restore_chapter_version",
            "status": "skipped",
            "detail": "当前章节没有更早的可回退版本",
            "data": {"chapter": _chapter_version_data(chapter), "items": [snapshot_to_item(s) for s in _chapter_snapshots(db, chapter)]},
        }
    restored = restore_chapter_from_snapshot(db, chapter, snapshot)
    ledger_restore = restore_ledger_checkpoint(db, project_id, chapter, snapshot.id)
    if project:
        sync_chapter_to_file(db, project, chapter)
        db.flush()
    return {
        "tool": "restore_chapter_version",
        "status": "ok",
        "detail": f"已将「{chapter.title}」恢复到 v{snapshot.version_number}，当前记录为 v{chapter.current_version or 1}",
        "data": {
            "chapter": _chapter_version_data(chapter),
            "restored_from": snapshot_to_item(snapshot),
            "restore_snapshot": snapshot_to_item(restored),
            "content_preview": (chapter.content or "")[:500],
            "ledger_checkpoint_id": ledger_restore["ledger_checkpoint_id"],
            "ledger_restored_count": ledger_restore["restored_count"],
            "ledger_conflicts": ledger_restore["conflicts"],
        },
    }


async def diff_chapter_versions(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {"tool": "diff_chapter_versions", "status": "skipped", "detail": "未找到章节", "data": None}
    from_args = dict(args)
    to_args = dict(args)
    from_args["snapshot_id"] = args.get("from_snapshot_id") or args.get("base_snapshot_id")
    to_args["snapshot_id"] = args.get("to_snapshot_id") or args.get("target_snapshot_id")
    if not from_args["snapshot_id"]:
        from_args["version_number"] = args.get("from_version")
    if not to_args["snapshot_id"]:
        to_args["version_number"] = args.get("to_version")
    from_snapshot = _find_snapshot(db, chapter, from_args)
    to_snapshot = _find_snapshot(db, chapter, to_args)
    if not from_snapshot or not to_snapshot:
        return {
            "tool": "diff_chapter_versions",
            "status": "skipped",
            "detail": "需要两个可识别的版本；请先调用 list_chapter_versions",
            "data": {"chapter": _chapter_version_data(chapter), "items": [snapshot_to_item(s) for s in _chapter_snapshots(db, chapter)]},
        }
    return {
        "tool": "diff_chapter_versions",
        "status": "ok",
        "detail": f"已对比 v{from_snapshot.version_number} 与 v{to_snapshot.version_number}",
        "data": diff_snapshots(from_snapshot, to_snapshot),
    }


async def delete_chapter(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    project = db.query(Project).filter(Project.id == project_id).first()
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
    content_file_path = chapter.content_file_path

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
    if project:
        delete_project_file(project, content_file_path)
    db.flush()

    detail = f"已删除章节：{title}"
    if reverted:
        detail += f"，已回退 {len(reverted)} 个角色的状态（{', '.join(reverted)}）"
    return {"tool": "delete_chapter", "status": "ok", "detail": detail}
