"""Project-folder tools for Siming 2.x file-backed content."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import Project
from ....services.content_store import (
    ensure_project_folder,
    sync_project_to_files,
    refresh_project_from_files,
)


TEXT_SUFFIXES = {".md", ".json", ".txt", ".yml", ".yaml", ".csv"}
MAX_READ_CHARS = 200_000
MAX_WRITE_CHARS = 1_500_000
CANONICAL_DIRS = {"chapters", "characters", "worldbuilding", "outline", "relationships"}


def _resolve_project(db: Session, current_project_id: str, args: dict[str, Any]) -> Project | None:
    target_id = str(args.get("project_id") or args.get("id") or current_project_id or "").strip()
    if not target_id:
        return None
    return db.query(Project).filter(Project.id == target_id).first()


def _folder(db: Session, project: Project) -> Path:
    return ensure_project_folder(db, project).resolve()


def _safe_path(folder: Path, raw_path: object) -> Path:
    text = str(raw_path or "").strip().replace("\\", "/")
    if not text:
        raise ValueError("path is required")
    candidate = Path(text)
    if candidate.is_absolute():
        path = candidate.resolve()
    else:
        path = (folder / text).resolve()
    path.relative_to(folder)
    return path


def _rel(path: Path, folder: Path) -> str:
    return path.resolve().relative_to(folder.resolve()).as_posix()


def _file_item(path: Path, folder: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": _rel(path, folder),
        "name": path.name,
        "kind": "directory" if path.is_dir() else "file",
        "size": 0 if path.is_dir() else stat.st_size,
        "modified_at": stat.st_mtime,
    }


async def get_project_files_info(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    project = _resolve_project(db, project_id, args)
    if not project:
        return {"tool": "get_project_files_info", "status": "skipped", "detail": "未找到作品"}
    folder = _folder(db, project)
    return {
        "tool": "get_project_files_info",
        "status": "ok",
        "detail": f"作品目录：{folder}",
        "data": {
            "project_id": project.id,
            "title": project.title,
            "folder_path": str(folder),
            "manifest": "moshu-project.json",
            "standard_dirs": ["chapters", "characters", "worldbuilding", "outline", "relationships", "outbox"],
        },
    }


async def list_project_files(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    project = _resolve_project(db, project_id, args)
    if not project:
        return {"tool": "list_project_files", "status": "skipped", "detail": "未找到作品"}
    folder = _folder(db, project)
    rel_dir = str(args.get("path") or "").strip()
    try:
        target = folder if not rel_dir else _safe_path(folder, rel_dir)
    except ValueError:
        return {"tool": "list_project_files", "status": "skipped", "detail": "文件路径超出作品目录"}
    if not target.exists() or not target.is_dir():
        return {"tool": "list_project_files", "status": "skipped", "detail": "目录不存在"}
    limit = max(1, min(int(args.get("limit") or 200), 1000))
    items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:limit]
    return {
        "tool": "list_project_files",
        "status": "ok",
        "detail": f"列出 {len(items)} 个项目文件",
        "data": {"project_id": project.id, "folder_path": str(folder), "items": [_file_item(item, folder) for item in items]},
    }


async def read_project_file(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    project = _resolve_project(db, project_id, args)
    if not project:
        return {"tool": "read_project_file", "status": "skipped", "detail": "未找到作品"}
    folder = _folder(db, project)
    try:
        path = _safe_path(folder, args.get("path"))
    except ValueError:
        return {"tool": "read_project_file", "status": "skipped", "detail": "文件路径超出作品目录"}
    if not path.exists() or not path.is_file():
        return {"tool": "read_project_file", "status": "skipped", "detail": "文件不存在"}
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return {"tool": "read_project_file", "status": "skipped", "detail": "仅支持读取文本类项目文件"}
    max_chars = max(1, min(int(args.get("max_chars") or MAX_READ_CHARS), MAX_READ_CHARS))
    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > max_chars
    return {
        "tool": "read_project_file",
        "status": "ok",
        "detail": f"已读取文件：{_rel(path, folder)}",
        "data": {
            "project_id": project.id,
            "path": _rel(path, folder),
            "content": text[:max_chars],
            "truncated": truncated,
            "total_chars": len(text),
        },
    }


async def write_project_file(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    project = _resolve_project(db, project_id, args)
    if not project:
        return {"tool": "write_project_file", "status": "skipped", "detail": "未找到作品"}
    folder = _folder(db, project)
    try:
        path = _safe_path(folder, args.get("path"))
    except ValueError:
        return {"tool": "write_project_file", "status": "skipped", "detail": "文件路径超出作品目录"}
    rel_parts = path.resolve().relative_to(folder.resolve()).parts
    if rel_parts and rel_parts[0] in CANONICAL_DIRS:
        return {
            "tool": "write_project_file",
            "status": "skipped",
            "detail": "2.1 起章节/角色/大纲/世界观文件是只读镜像；请使用对应 create/update/delete 工具写入数据库后自动刷新镜像",
        }
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return {"tool": "write_project_file", "status": "skipped", "detail": "仅支持写入文本类项目文件"}
    content = str(args.get("content") or "")
    if len(content) > MAX_WRITE_CHARS:
        return {"tool": "write_project_file", "status": "skipped", "detail": "写入内容过长，请分块写入"}
    if path.exists() and not bool(args.get("overwrite", True)):
        return {"tool": "write_project_file", "status": "skipped", "detail": "文件已存在，未覆盖"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return {
        "tool": "write_project_file",
        "status": "ok",
        "detail": f"已写入文件：{_rel(path, folder)}",
        "data": {"project_id": project.id, "path": _rel(path, folder), "chars": len(content)},
    }


async def search_project_files(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    project = _resolve_project(db, project_id, args)
    if not project:
        return {"tool": "search_project_files", "status": "skipped", "detail": "未找到作品"}
    folder = _folder(db, project)
    query = str(args.get("query") or "").strip()
    if not query:
        return {"tool": "search_project_files", "status": "skipped", "detail": "搜索词为空"}
    root_arg = str(args.get("path") or "").strip()
    try:
        root = folder if not root_arg else _safe_path(folder, root_arg)
    except ValueError:
        return {"tool": "search_project_files", "status": "skipped", "detail": "文件路径超出作品目录"}
    if not root.exists():
        return {"tool": "search_project_files", "status": "skipped", "detail": "搜索目录不存在"}
    limit = max(1, min(int(args.get("limit") or 50), 200))
    context_chars = max(20, min(int(args.get("context_chars") or 120), 500))
    matches: list[dict[str, Any]] = []
    files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
    for path in files:
        if len(matches) >= limit:
            break
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        lower_text = text.lower()
        lower_query = query.lower()
        start = 0
        while len(matches) < limit:
            index = lower_text.find(lower_query, start)
            if index < 0:
                break
            left = max(0, index - context_chars)
            right = min(len(text), index + len(query) + context_chars)
            matches.append({
                "path": _rel(path, folder),
                "offset": index,
                "snippet": text[left:right],
            })
            start = index + len(query)
    return {
        "tool": "search_project_files",
        "status": "ok",
        "detail": f"找到 {len(matches)} 处文件匹配",
        "data": {"project_id": project.id, "query": query, "matches": matches},
    }


async def sync_project_files(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    project = _resolve_project(db, project_id, args)
    if not project:
        return {"tool": "sync_project_files", "status": "skipped", "detail": "未找到作品"}
    direction = str(args.get("direction") or "db_to_files").strip()
    if direction in {"db_to_files", "export"}:
        sync_project_to_files(db, project.id)
    elif direction in {"both"}:
        if not bool(args.get("confirm_import_from_files")):
            return {
                "tool": "sync_project_files",
                "status": "skipped",
                "detail": "2.1 默认禁止从文件镜像反向覆盖数据库；如确需修复导入，请传 confirm_import_from_files=true",
            }
        refresh_project_from_files(db, project.id)
        sync_project_to_files(db, project.id)
    elif direction in {"files_to_db", "import"}:
        if not bool(args.get("confirm_import_from_files")):
            return {
                "tool": "sync_project_files",
                "status": "skipped",
                "detail": "2.1 默认禁止从文件镜像反向覆盖数据库；如确需修复导入，请传 confirm_import_from_files=true",
            }
        refresh_project_from_files(db, project.id)
    else:
        return {"tool": "sync_project_files", "status": "skipped", "detail": f"未知同步方向：{direction}"}
    db.flush()
    return {
        "tool": "sync_project_files",
        "status": "ok",
        "detail": f"项目文件已同步：{direction}",
        "data": {"project_id": project.id, "direction": direction, "folder_path": project.folder_path},
    }
