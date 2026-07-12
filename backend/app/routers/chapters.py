"""Chapter CRUD, version snapshot, restore, and diff endpoints."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..core.db_helpers import get_outline_node_or_404, get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..core.utils import count_words
from ..database.models import Chapter, ChapterSnapshot
from ..database.session import get_db
from ..schemas.chapter import ChapterCreate, ChapterUpdate
from ..services.chapter_service import (
    chapter_to_detail,
    chapter_to_list_item,
    create_snapshot,
    diff_snapshots,
    ensure_current_snapshot,
    restore_chapter_from_snapshot,
    snapshot_to_item,
)
from ..services.narrative_ledger import restore_ledger_checkpoint
from ..services.narrative_governance import create_narrative_checkpoint
from ..services.content_store import (
    delete_project_file,
    sync_chapter_to_file,
)
from ..services.outline_service import load_outline_nodes, outline_sort_context

router = APIRouter(tags=["chapters"])


def _get_chapter_or_404(db: Session, project_id: str, chapter_id: str) -> Chapter:
    chapter = (
        db.query(Chapter)
        .filter(Chapter.id == chapter_id, Chapter.project_id == project_id)
        .first()
    )
    if not chapter:
        raise NotFoundError("章节不存在")
    return chapter


def _get_snapshot_or_404(
    db: Session,
    project_id: str,
    chapter_id: str,
    snapshot_id: str,
) -> ChapterSnapshot:
    snapshot = (
        db.query(ChapterSnapshot)
        .join(Chapter, Chapter.id == ChapterSnapshot.chapter_id)
        .filter(
            Chapter.project_id == project_id,
            Chapter.id == chapter_id,
            ChapterSnapshot.id == snapshot_id,
        )
        .first()
    )
    if not snapshot:
        raise NotFoundError("章节快照不存在")
    return snapshot


@router.get("/projects/{project_id}/chapters")
def list_chapters(project_id: str, db: Session = Depends(get_db)):
    """Get chapter list ordered by outline tree structure."""
    project = get_project_or_404(db, project_id)
    outline_context = outline_sort_context(load_outline_nodes(db, project_id))
    chapters = db.query(Chapter).filter(Chapter.project_id == project_id).all()

    def sort_key(chapter: Chapter):
        outline_key = outline_context["sort_keys"].get(chapter.outline_node_id)
        if outline_key is None:
            return (1, (999999,), chapter.created_at or datetime.min)
        return (0, outline_key, chapter.created_at or datetime.min)

    chapters.sort(key=sort_key)
    items = [chapter_to_list_item(chapter, outline_context) for chapter in chapters]
    return ApiResponse.success(data={"items": items, "total": len(items)})


@router.post("/projects/{project_id}/chapters")
def create_chapter(project_id: str, payload: ChapterCreate, db: Session = Depends(get_db)):
    """Create a chapter linked to an optional outline node."""
    project = get_project_or_404(db, project_id)
    get_outline_node_or_404(db, project_id, payload.outline_node_id)
    chapter = Chapter(
        project_id=project_id,
        outline_node_id=payload.outline_node_id,
        title=payload.title,
        content=payload.content or "",
        word_count=count_words(payload.content or ""),
        current_version=1,
    )
    db.add(chapter)
    db.flush()
    db.add(create_snapshot(chapter, "manual_save"))
    create_narrative_checkpoint(db, project_id, chapter=chapter, label=f"{chapter.title} 创建", trigger_type="chapter_create")
    db.commit()
    db.refresh(chapter)
    sync_chapter_to_file(db, project, chapter)
    db.commit()
    db.refresh(chapter)
    outline_context = outline_sort_context(load_outline_nodes(db, project_id))
    return ApiResponse.success(data=chapter_to_detail(chapter, outline_context), message="章节已创建")


@router.get("/projects/{project_id}/chapters/{chapter_id}")
def get_chapter_detail(project_id: str, chapter_id: str, db: Session = Depends(get_db)):
    """Get chapter detail with full content."""
    get_project_or_404(db, project_id)
    chapter = _get_chapter_or_404(db, project_id, chapter_id)
    outline_context = outline_sort_context(load_outline_nodes(db, project_id))
    return ApiResponse.success(data=chapter_to_detail(chapter, outline_context))


@router.put("/projects/{project_id}/chapters/{chapter_id}")
def save_chapter(
    project_id: str,
    chapter_id: str,
    payload: ChapterUpdate,
    db: Session = Depends(get_db),
):
    """Save chapter fields and create a version snapshot in the same transaction."""
    project = get_project_or_404(db, project_id)
    chapter = _get_chapter_or_404(db, project_id, chapter_id)
    update_data = payload.model_dump(exclude_unset=True)
    trigger_type = update_data.pop("trigger_type", "manual_save")
    if not update_data:
        raise ValidationError("未提供任何更新字段")
    ensure_current_snapshot(db, chapter, "manual_save")

    if "outline_node_id" in update_data:
        get_outline_node_or_404(db, project_id, update_data["outline_node_id"])
        chapter.outline_node_id = update_data["outline_node_id"]
    if "title" in update_data:
        chapter.title = update_data["title"]
    if "content" in update_data:
        chapter.content = update_data["content"] or ""

    chapter.word_count = count_words(chapter.content or "")
    chapter.current_version = (chapter.current_version or 1) + 1
    db.add(create_snapshot(chapter, trigger_type))
    create_narrative_checkpoint(db, project_id, chapter=chapter, label=f"{chapter.title} v{chapter.current_version}", trigger_type=trigger_type)
    db.commit()
    db.refresh(chapter)
    sync_chapter_to_file(db, project, chapter)
    db.commit()
    db.refresh(chapter)
    outline_context = outline_sort_context(load_outline_nodes(db, project_id))
    return ApiResponse.success(data=chapter_to_detail(chapter, outline_context), message="章节已保存")


@router.delete("/projects/{project_id}/chapters/{chapter_id}")
def delete_chapter(project_id: str, chapter_id: str, db: Session = Depends(get_db)):
    """Delete a chapter and its snapshots."""
    project = get_project_or_404(db, project_id)
    chapter = _get_chapter_or_404(db, project_id, chapter_id)
    content_file_path = chapter.content_file_path
    db.delete(chapter)
    delete_project_file(project, content_file_path)
    db.commit()
    return ApiResponse.success(message="章节已删除")


@router.get("/projects/{project_id}/chapters/{chapter_id}/snapshots")
def list_chapter_snapshots(project_id: str, chapter_id: str, db: Session = Depends(get_db)):
    """Get version snapshot history for a chapter."""
    get_project_or_404(db, project_id)
    chapter = _get_chapter_or_404(db, project_id, chapter_id)
    snapshots = (
        db.query(ChapterSnapshot)
        .filter(ChapterSnapshot.chapter_id == chapter.id)
        .order_by(ChapterSnapshot.version_number.desc(), ChapterSnapshot.created_at.desc())
        .all()
    )
    items = [snapshot_to_item(snapshot) for snapshot in snapshots]
    return ApiResponse.success(data={"items": items, "total": len(items)})


@router.get("/projects/{project_id}/chapters/{chapter_id}/snapshots/diff")
def diff_chapter_snapshots(
    project_id: str,
    chapter_id: str,
    from_snapshot_id: str = Query(..., description="Base snapshot ID"),
    to_snapshot_id: str = Query(..., description="Target snapshot ID"),
    db: Session = Depends(get_db),
):
    """Compare two snapshots and return line-based diff marks."""
    get_project_or_404(db, project_id)
    _get_chapter_or_404(db, project_id, chapter_id)
    from_snapshot = _get_snapshot_or_404(db, project_id, chapter_id, from_snapshot_id)
    to_snapshot = _get_snapshot_or_404(db, project_id, chapter_id, to_snapshot_id)
    return ApiResponse.success(data=diff_snapshots(from_snapshot, to_snapshot))


@router.get("/projects/{project_id}/chapters/{chapter_id}/snapshots/{snapshot_id}")
def get_chapter_snapshot_detail(
    project_id: str,
    chapter_id: str,
    snapshot_id: str,
    db: Session = Depends(get_db),
):
    """Get one chapter snapshot including its saved content."""
    get_project_or_404(db, project_id)
    snapshot = _get_snapshot_or_404(db, project_id, chapter_id, snapshot_id)
    data = snapshot_to_item(snapshot)
    data["content"] = snapshot.content or ""
    return ApiResponse.success(data=data)


@router.post("/projects/{project_id}/chapters/{chapter_id}/restore/{snapshot_id}")
def restore_chapter_snapshot(
    project_id: str,
    chapter_id: str,
    snapshot_id: str,
    db: Session = Depends(get_db),
):
    """Restore a chapter to a snapshot and create a new restore snapshot."""
    get_project_or_404(db, project_id)
    chapter = _get_chapter_or_404(db, project_id, chapter_id)
    snapshot = _get_snapshot_or_404(db, project_id, chapter_id, snapshot_id)
    restore_chapter_from_snapshot(db, chapter, snapshot)
    ledger_restore = restore_ledger_checkpoint(db, project_id, chapter, snapshot.id)
    db.commit()
    db.refresh(chapter)
    project = get_project_or_404(db, project_id)
    sync_chapter_to_file(db, project, chapter)
    db.commit()
    db.refresh(chapter)
    outline_context = outline_sort_context(load_outline_nodes(db, project_id))
    data = chapter_to_detail(chapter, outline_context)
    data["ledger_checkpoint_id"] = ledger_restore["ledger_checkpoint_id"]
    data["ledger_restored_count"] = ledger_restore["restored_count"]
    data["ledger_conflicts"] = ledger_restore["conflicts"]
    return ApiResponse.success(data=data, message="章节已恢复")
