"""Chapter CRUD, version snapshot, restore, and diff endpoints."""
import difflib
import json
import re
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.models import Chapter, ChapterSnapshot, OutlineNode, Project, WritingLog
from ..database.session import get_db
from ..schemas.chapter import (
    ChapterCreate,
    ChapterDetail,
    ChapterListItem,
    ChapterSnapshotItem,
    ChapterUpdate,
)

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


def _get_outline_node_or_404(
    db: Session,
    project_id: str,
    outline_node_id: Optional[str],
) -> Optional[OutlineNode]:
    if not outline_node_id:
        return None
    node = (
        db.query(OutlineNode)
        .filter(OutlineNode.id == outline_node_id, OutlineNode.project_id == project_id)
        .first()
    )
    if not node:
        raise ValidationError("关联大纲节点必须属于当前作品")
    return node


def _count_words(text: str) -> int:
    """Count CJK characters and Latin words in a practical writing-friendly way."""
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    without_cjk = re.sub(r"[\u4e00-\u9fff]", " ", text)
    latin_words = re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?", without_cjk)
    return len(cjk_chars) + len(latin_words)


def _apply_today_word_delta(db: Session, project_id: str, delta: int) -> None:
    """Accumulate today's net writing delta for manual saves/restores."""
    if delta == 0:
        return
    today = date.today()
    log = (
        db.query(WritingLog)
        .filter(WritingLog.project_id == project_id, WritingLog.date == today)
        .first()
    )
    if not log:
        log = WritingLog(project_id=project_id, date=today, total_words=0)
        db.add(log)
    log.total_words = max(0, (log.total_words or 0) + delta)


def _load_outline_nodes(db: Session, project_id: str) -> list[OutlineNode]:
    return (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.sort_order.asc(), OutlineNode.created_at.asc())
        .all()
    )


def _outline_sort_context(nodes: list[OutlineNode]) -> dict:
    node_by_id = {node.id: node for node in nodes}
    children_by_parent: dict[Optional[str], list[OutlineNode]] = {}
    for node in nodes:
        children_by_parent.setdefault(node.parent_id, []).append(node)
    for siblings in children_by_parent.values():
        siblings.sort(key=lambda item: (item.sort_order or 0, item.created_at or datetime.min))

    sort_keys: dict[str, tuple[int, ...]] = {}

    def walk(parent_id: Optional[str], prefix: tuple[int, ...]) -> None:
        for index, node in enumerate(children_by_parent.get(parent_id, [])):
            key = (*prefix, index)
            sort_keys[node.id] = key
            walk(node.id, key)

    walk(None, ())

    def path_for(node_id: Optional[str]) -> list[str]:
        if not node_id:
            return []
        path: list[str] = []
        current = node_by_id.get(node_id)
        visited: set[str] = set()
        while current and current.id not in visited:
            visited.add(current.id)
            path.append(current.title)
            current = node_by_id.get(current.parent_id) if current.parent_id else None
        return list(reversed(path))

    return {"nodes": node_by_id, "sort_keys": sort_keys, "path_for": path_for}


def _chapter_to_list_item(chapter: Chapter, outline_context: dict) -> dict:
    outline_node = outline_context["nodes"].get(chapter.outline_node_id)
    summary = chapter.summary
    key_events: list[str] = []
    if summary and summary.key_events:
        try:
            parsed = json.loads(summary.key_events)
            key_events = parsed if isinstance(parsed, list) else []
        except Exception:
            key_events = []
    data = ChapterListItem(
        id=chapter.id,
        project_id=chapter.project_id,
        outline_node_id=chapter.outline_node_id,
        title=chapter.title,
        word_count=chapter.word_count or 0,
        current_version=chapter.current_version or 1,
        outline_title=outline_node.title if outline_node else None,
        outline_status=outline_node.status if outline_node else None,
        outline_node_type=outline_node.node_type if outline_node else None,
        outline_path=outline_context["path_for"](chapter.outline_node_id),
        summary_text=summary.summary_text if summary else None,
        key_events=[str(item) for item in key_events[:12]],
        created_at=chapter.created_at,
        updated_at=chapter.updated_at,
    )
    return data.model_dump(mode="json")


def _chapter_to_detail(chapter: Chapter, outline_context: dict) -> dict:
    base = _chapter_to_list_item(chapter, outline_context)
    data = ChapterDetail(
        **base,
        content=chapter.content or "",
        snapshot_count=len(chapter.snapshots),
    )
    return data.model_dump(mode="json")


def _snapshot_to_item(snapshot: ChapterSnapshot) -> dict:
    return ChapterSnapshotItem.model_validate(snapshot).model_dump(mode="json")


def _create_snapshot(chapter: Chapter, trigger_type: str) -> ChapterSnapshot:
    return ChapterSnapshot(
        chapter_id=chapter.id,
        version_number=chapter.current_version or 1,
        content=chapter.content or "",
        word_count=chapter.word_count or 0,
        trigger_type=trigger_type,
    )


def _diff_snapshots(from_snapshot: ChapterSnapshot, to_snapshot: ChapterSnapshot) -> dict:
    from_lines = (from_snapshot.content or "").splitlines()
    to_lines = (to_snapshot.content or "").splitlines()
    matcher = difflib.SequenceMatcher(a=from_lines, b=to_lines)

    changes: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        changes.append(
            {
                "type": tag,
                "from_start": i1,
                "from_end": i2,
                "to_start": j1,
                "to_end": j2,
                "from_lines": from_lines[i1:i2],
                "to_lines": to_lines[j1:j2],
            }
        )

    return {
        "from_snapshot": _snapshot_to_item(from_snapshot),
        "to_snapshot": _snapshot_to_item(to_snapshot),
        "changes": changes,
        "total_changes": len([item for item in changes if item["type"] != "equal"]),
    }


@router.get("/projects/{project_id}/chapters")
def list_chapters(project_id: str, db: Session = Depends(get_db)):
    """Get chapter list ordered by outline tree structure."""
    get_project_or_404(db, project_id)
    outline_context = _outline_sort_context(_load_outline_nodes(db, project_id))
    chapters = db.query(Chapter).filter(Chapter.project_id == project_id).all()

    def sort_key(chapter: Chapter):
        outline_key = outline_context["sort_keys"].get(chapter.outline_node_id)
        if outline_key is None:
            return (1, (999999,), chapter.created_at or datetime.min)
        return (0, outline_key, chapter.created_at or datetime.min)

    chapters.sort(key=sort_key)
    items = [_chapter_to_list_item(chapter, outline_context) for chapter in chapters]
    return ApiResponse.success(data={"items": items, "total": len(items)})


@router.post("/projects/{project_id}/chapters")
def create_chapter(project_id: str, payload: ChapterCreate, db: Session = Depends(get_db)):
    """Create a chapter linked to an optional outline node."""
    get_project_or_404(db, project_id)
    _get_outline_node_or_404(db, project_id, payload.outline_node_id)
    chapter = Chapter(
        project_id=project_id,
        outline_node_id=payload.outline_node_id,
        title=payload.title,
        content=payload.content or "",
        word_count=_count_words(payload.content or ""),
        current_version=1,
    )
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    outline_context = _outline_sort_context(_load_outline_nodes(db, project_id))
    return ApiResponse.success(data=_chapter_to_detail(chapter, outline_context), message="章节已创建")


@router.get("/projects/{project_id}/chapters/{chapter_id}")
def get_chapter_detail(project_id: str, chapter_id: str, db: Session = Depends(get_db)):
    """Get chapter detail with full content."""
    get_project_or_404(db, project_id)
    chapter = _get_chapter_or_404(db, project_id, chapter_id)
    outline_context = _outline_sort_context(_load_outline_nodes(db, project_id))
    return ApiResponse.success(data=_chapter_to_detail(chapter, outline_context))


@router.put("/projects/{project_id}/chapters/{chapter_id}")
def save_chapter(
    project_id: str,
    chapter_id: str,
    payload: ChapterUpdate,
    db: Session = Depends(get_db),
):
    """Save chapter fields and create a version snapshot in the same transaction."""
    get_project_or_404(db, project_id)
    chapter = _get_chapter_or_404(db, project_id, chapter_id)
    update_data = payload.model_dump(exclude_unset=True)
    trigger_type = update_data.pop("trigger_type", "manual_save")
    if not update_data:
        raise ValidationError("未提供任何更新字段")

    old_word_count = chapter.word_count or _count_words(chapter.content or "")

    if "outline_node_id" in update_data:
        _get_outline_node_or_404(db, project_id, update_data["outline_node_id"])
        chapter.outline_node_id = update_data["outline_node_id"]
    if "title" in update_data:
        chapter.title = update_data["title"]
    if "content" in update_data:
        chapter.content = update_data["content"] or ""

    chapter.word_count = _count_words(chapter.content or "")
    _apply_today_word_delta(db, project_id, chapter.word_count - old_word_count)
    chapter.current_version = (chapter.current_version or 1) + 1
    db.add(_create_snapshot(chapter, trigger_type))
    db.commit()
    db.refresh(chapter)
    outline_context = _outline_sort_context(_load_outline_nodes(db, project_id))
    return ApiResponse.success(data=_chapter_to_detail(chapter, outline_context), message="章节已保存")


@router.delete("/projects/{project_id}/chapters/{chapter_id}")
def delete_chapter(project_id: str, chapter_id: str, db: Session = Depends(get_db)):
    """Delete a chapter and its snapshots."""
    get_project_or_404(db, project_id)
    chapter = _get_chapter_or_404(db, project_id, chapter_id)
    db.delete(chapter)
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
    items = [_snapshot_to_item(snapshot) for snapshot in snapshots]
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
    return ApiResponse.success(data=_diff_snapshots(from_snapshot, to_snapshot))




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
    old_word_count = chapter.word_count or _count_words(chapter.content or "")

    chapter.content = snapshot.content
    chapter.word_count = snapshot.word_count or _count_words(snapshot.content or "")
    _apply_today_word_delta(db, project_id, chapter.word_count - old_word_count)
    chapter.current_version = (chapter.current_version or 1) + 1
    db.add(_create_snapshot(chapter, "restore"))
    db.commit()
    db.refresh(chapter)
    outline_context = _outline_sort_context(_load_outline_nodes(db, project_id))
    return ApiResponse.success(data=_chapter_to_detail(chapter, outline_context), message="章节已恢复")
