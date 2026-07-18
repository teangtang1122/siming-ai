"""Project folder storage contract audits.

Siming treats the database as authoritative. The project folder is a readable
mirror, except for explicit repair/import flows. These helpers detect files
created directly in canonical mirror folders so the UI can explain why the
database and frontend do not show them yet.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.utils import count_words
from app.database.models import Chapter, Project
from app.services.content_store import parse_chapter_markdown


def _rel(path: Path, base: Path) -> str:
    return path.resolve().relative_to(base.resolve()).as_posix()


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def find_orphan_chapter_files(
    db: Session,
    project: Project,
    *,
    since: datetime | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return chapter mirror files that are not linked to any DB chapter."""
    if not project.folder_path:
        return []
    folder = Path(project.folder_path)
    chapters_dir = folder / "chapters"
    if not chapters_dir.exists():
        return []

    db_chapters = db.query(Chapter).filter(Chapter.project_id == project.id).all()
    known_ids = {str(chapter.id) for chapter in db_chapters}
    known_paths = {str(chapter.content_file_path or "") for chapter in db_chapters if chapter.content_file_path}
    cutoff = since - timedelta(minutes=5) if since else None
    items: list[dict[str, Any]] = []

    for path in sorted(chapters_dir.glob("*.md"), key=_mtime, reverse=True):
        try:
            stat = path.stat()
            modified_at = datetime.fromtimestamp(stat.st_mtime)
        except OSError:
            continue
        if cutoff and modified_at < cutoff:
            continue
        rel_path = _rel(path, folder)
        if rel_path in known_paths:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            meta, content = parse_chapter_markdown(text)
        except Exception:
            meta, content = {}, ""
        chapter_id = str(meta.get("id") or "").strip()
        if chapter_id and chapter_id in known_ids:
            continue
        title = str(meta.get("title") or "").strip() or path.stem
        items.append({
            "path": rel_path,
            "id": chapter_id or None,
            "title": title,
            "word_count": int(meta.get("word_count") or count_words(content or "")),
            "modified_at": modified_at.isoformat(),
        })
        if len(items) >= limit:
            break
    return items


def storage_health(db: Session, project: Project, *, since: datetime | None = None) -> dict[str, Any]:
    from app.modules.story.application.content_sync import content_sync_health

    orphan_chapter_files = find_orphan_chapter_files(db, project, since=since)
    sync_queue = content_sync_health(db, project.id)
    return {
        "storage_target": "database_authoritative",
        "sync_queue": sync_queue,
        "orphan_chapter_files": orphan_chapter_files,
        "orphan_chapter_file_count": len(orphan_chapter_files),
        "next_action": (
            "sync_project_files(direction='import', confirm_import_from_files=true)"
            if orphan_chapter_files else None
        ),
        "warning": (
            "Detected chapter mirror files that are not in the database. "
            "The frontend only shows database chapters; import explicitly or recreate through create_chapter."
            if orphan_chapter_files else None
        ) or (
            "The database update succeeded, but one or more mirror updates need retrying."
            if sync_queue["failed_count"] else None
        ),
    }
