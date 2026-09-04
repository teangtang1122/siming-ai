"""Read, restore, diff, and delete chapter data.

Creating and editing chapter prose is intentionally absent here. AI writing
produces a pending ``ChapterDraft``; only the author-facing chapter HTTP API
can turn the current editor text into an official chapter.  Restore and delete
reuse the canonical chapter workspace so every surface gets the same
cataloging rollback semantics.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session

from ....database.models import Chapter, ChapterSnapshot
from ....modules.story.application.content_sync import queue_content_sync
from ....modules.story.infrastructure.chapters import SqlAlchemyChapterWorkspace
from ....services.chapter_service import diff_snapshots, snapshot_to_item
from ..utils import find_outline_by_title_or_id


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
    for ref in (
        args.get("outline_node_id"),
        args.get("outline_node_title"),
        args.get("outline_title"),
    ):
        outline_node = find_outline_by_title_or_id(
            db, project_id, ref, node_type="chapter"
        )
        if outline_node:
            break
    if outline_node:
        return (
            db.query(Chapter)
            .filter(
                Chapter.project_id == project_id,
                Chapter.outline_node_id == outline_node.id,
            )
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
        .order_by(
            ChapterSnapshot.version_number.desc(),
            ChapterSnapshot.created_at.desc(),
        )
        .all()
    )


def _find_snapshot(
    db: Session,
    chapter: Chapter,
    args: dict[str, Any],
) -> ChapterSnapshot | None:
    snapshot_id = str(args.get("snapshot_id") or args.get("version_id") or "").strip()
    if snapshot_id:
        return (
            db.query(ChapterSnapshot)
            .filter(
                ChapterSnapshot.chapter_id == chapter.id,
                ChapterSnapshot.id == snapshot_id,
            )
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


def _queue_sync_intents(db: Session, intents: list[Any]) -> None:
    for intent in intents:
        queue_content_sync(db, intent)


async def list_chapter_versions(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {
            "tool": "list_chapter_versions",
            "status": "skipped",
            "detail": "未找到章节",
            "data": None,
        }
    snapshots = _chapter_snapshots(db, chapter)
    items = [snapshot_to_item(snapshot) for snapshot in snapshots]
    return {
        "tool": "list_chapter_versions",
        "status": "ok",
        "detail": (
            f"章节「{chapter.title}」共有 {len(items)} 个版本快照，"
            f"当前 v{chapter.current_version or 1}"
        ),
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
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {
            "tool": "restore_chapter_version",
            "status": "skipped",
            "detail": "未找到章节",
            "data": None,
        }
    snapshot = _find_snapshot(db, chapter, args)
    if not snapshot:
        return {
            "tool": "restore_chapter_version",
            "status": "skipped",
            "detail": "没有找到可恢复的版本；请先调用 list_chapter_versions 查看可用快照",
            "data": {
                "chapter": _chapter_version_data(chapter),
                "items": [snapshot_to_item(item) for item in _chapter_snapshots(db, chapter)],
            },
        }
    if (snapshot.version_number or 0) >= (chapter.current_version or 1) and not (
        args.get("snapshot_id")
        or args.get("version_id")
        or args.get("version_number")
    ):
        return {
            "tool": "restore_chapter_version",
            "status": "skipped",
            "detail": "当前章节没有更早的可回退版本",
            "data": {
                "chapter": _chapter_version_data(chapter),
                "items": [snapshot_to_item(item) for item in _chapter_snapshots(db, chapter)],
            },
        }

    restored_from = snapshot_to_item(snapshot)
    mutation = SqlAlchemyChapterWorkspace(db).restore(
        project_id,
        chapter.id,
        snapshot.id,
    )
    _queue_sync_intents(db, mutation.sync_intents)
    commit_session(db)
    recatalog_ids = mutation.data.get("recatalog_required_chapter_ids", [])
    return {
        "tool": "restore_chapter_version",
        "status": "ok",
        "detail": (
            f"已将「{chapter.title}」恢复到 v{snapshot.version_number}；"
            f"该章及后续 {max(len(recatalog_ids) - 1, 0)} 章需要重新建档"
        ),
        "data": {
            **mutation.data,
            "restored_from": restored_from,
            "content_preview": (chapter.content or "")[:500],
        },
    }


async def diff_chapter_versions(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {
            "tool": "diff_chapter_versions",
            "status": "skipped",
            "detail": "未找到章节",
            "data": None,
        }
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
            "data": {
                "chapter": _chapter_version_data(chapter),
                "items": [snapshot_to_item(item) for item in _chapter_snapshots(db, chapter)],
            },
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
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {"tool": "delete_chapter", "status": "skipped", "detail": "未找到章节"}

    title = chapter.title
    mutation = SqlAlchemyChapterWorkspace(db).delete(project_id, chapter.id)
    _queue_sync_intents(db, mutation.sync_intents)
    commit_session(db)
    recatalog_ids = mutation.data.get("recatalog_required_chapter_ids", [])
    detail = f"已删除章节：{title}，并回退该章建档产生的系统状态"
    if recatalog_ids:
        detail += f"；后续 {len(recatalog_ids)} 章已标记为需要重新建档"
    warnings = (mutation.data.get("cataloging_rollback") or {}).get("warnings") or []
    if warnings:
        detail += f"；另有 {len(warnings)} 项作者数据因存在后续使用而保留，请复核"
    return {
        "tool": "delete_chapter",
        "status": "ok",
        "detail": detail,
        "data": mutation.data,
    }


__all__ = [
    "delete_chapter",
    "diff_chapter_versions",
    "list_chapter_versions",
    "restore_chapter_version",
]
