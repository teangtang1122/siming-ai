"""Read-only access to files explicitly imported into the Siming content root."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session


async def list_imported_files(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    from app.services.content_store import content_root

    imported_dir = content_root() / ".imported"
    cursor = max(0, int(args.get("cursor") or 0))
    limit = max(1, min(int(args.get("limit") or 3), 3))
    all_files: list[dict[str, Any]] = []
    if imported_dir.exists():
        for path in sorted(
            imported_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True
        ):
            if not path.is_file():
                continue
            stat = path.stat()
            all_files.append(
                {
                    "filename": path.name,
                    "path": str(path),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
            )
    files = all_files[cursor : cursor + limit]
    next_cursor = cursor + len(files) if cursor + len(files) < len(all_files) else None
    return {
        "tool": "list_imported_files",
        "status": "ok",
        "data": {
            "files": files,
            "directory": str(imported_dir),
            "cursor": cursor,
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
        },
    }


async def read_imported_file(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    from app.services.content_store import content_root

    filename = str(args.get("filename") or "").strip()
    if not filename:
        return {
            "tool": "read_imported_file",
            "status": "skipped",
            "detail": "filename is required",
            "data": None,
        }
    imported_dir = (content_root() / ".imported").resolve()
    file_path = (imported_dir / filename).resolve()
    if not file_path.is_relative_to(imported_dir):
        return {
            "tool": "read_imported_file",
            "status": "skipped",
            "detail": "访问被拒绝",
            "data": None,
        }
    if not file_path.is_file():
        return {
            "tool": "read_imported_file",
            "status": "skipped",
            "detail": "文件不存在",
            "data": None,
        }
    try:
        max_size = max(1, min(int(args.get("max_size") or 4_000), 4_000))
    except (TypeError, ValueError):
        max_size = 4_000
    try:
        offset_chars = max(0, int(args.get("offset_chars") or 0))
    except (TypeError, ValueError):
        offset_chars = 0
    full_content = file_path.read_text(encoding="utf-8")
    end = min(len(full_content), offset_chars + max_size)
    content = full_content[offset_chars:end]
    has_more = end < len(full_content)
    return {
        "tool": "read_imported_file",
        "status": "ok",
        "data": {
            "filename": filename,
            "content": content,
            "size": len(full_content),
            "path": str(file_path),
            "offset_chars": offset_chars,
            "returned_chars": len(content),
            "next_offset_chars": end if has_more else None,
            "has_more": has_more,
        },
    }
