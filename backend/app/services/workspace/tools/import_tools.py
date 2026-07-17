"""Workspace import tools for text/file-based chapter ingestion."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import Project
from ....modules.story.application.content_sync import queue_content_sync
from ....modules.story.domain.content_sync import ContentSyncIntent, ContentSyncTarget
from ....schemas.importer import ImportSplitSuggestion
from ....services.import_service import build_split_preview, execute_import, parse_local_file


def _split_from_dict(item: object) -> ImportSplitSuggestion | None:
    if isinstance(item, ImportSplitSuggestion):
        return item
    if not isinstance(item, dict):
        return None
    try:
        return ImportSplitSuggestion(**item)
    except Exception:
        return None


def _normalize_splits(raw_splits: object) -> list[ImportSplitSuggestion]:
    items = raw_splits if isinstance(raw_splits, list) else []
    splits = [_split_from_dict(item) for item in items]
    return [item for item in splits if item is not None]


async def _resolve_splits(text: str, args: dict[str, Any]) -> tuple[list[ImportSplitSuggestion], str, bool, int]:
    splits = _normalize_splits(args.get("splits"))
    if splits:
        return splits, "provided", any(split.needs_review for split in splits), 0
    if bool(args.get("auto_split", True)) and text.strip():
        preview, method, needs_review, failed_blocks = await build_split_preview(text, args.get("model"))
        return _normalize_splits(preview), method, needs_review, failed_blocks
    return [], "none", False, 0


def _outline_node_id(args: dict[str, Any]) -> str | None:
    return str(args.get("outline_node_id") or "").strip() or None


async def preview_import_splits(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    text = str(args.get("text") or "")
    if len(text.strip()) < 100:
        return {"tool": "preview_import_splits", "status": "skipped", "detail": "导入文本太短，至少需要 100 个字符"}
    splits, method, needs_review, failed_blocks = await build_split_preview(text, args.get("model"))
    return {
        "tool": "preview_import_splits",
        "status": "ok",
        "detail": f"识别到 {len(splits)} 个章节切分点",
        "data": {
            "splits": splits,
            "total": len(splits),
            "method": method,
            "needs_review": needs_review,
            "failed_blocks": failed_blocks,
        },
    }


async def import_text_as_chapters(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    text = str(args.get("text") or "")
    if not text.strip():
        return {"tool": "import_text_as_chapters", "status": "skipped", "detail": "导入文本为空"}

    splits, method, needs_review, failed_blocks = await _resolve_splits(text, args)
    chapters = execute_import(db, project_id, text, splits, _outline_node_id(args))
    queue_content_sync(
        db,
        ContentSyncIntent(
            project_id=project_id,
            target=ContentSyncTarget.PROJECT,
            source="workspace_import",
        ),
    )
    return {
        "tool": "import_text_as_chapters",
        "status": "ok",
        "detail": f"已导入 {len(chapters)} 个章节",
        "data": {
            "chapters": chapters,
            "total": len(chapters),
            "method": method,
            "needs_review": needs_review,
            "failed_blocks": failed_blocks,
        },
    }


async def import_file_as_chapters(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    file_path = str(args.get("file_path") or args.get("path") or "").strip()
    if not file_path:
        return {"tool": "import_file_as_chapters", "status": "skipped", "detail": "缺少 file_path"}

    parsed = parse_local_file(file_path)
    text = parsed["text"]
    splits, method, needs_review, failed_blocks = await _resolve_splits(text, args)
    chapters = execute_import(db, project_id, text, splits, _outline_node_id(args))
    queue_content_sync(
        db,
        ContentSyncIntent(
            project_id=project_id,
            target=ContentSyncTarget.PROJECT,
            source="workspace_import",
        ),
    )
    return {
        "tool": "import_file_as_chapters",
        "status": "ok",
        "detail": f"已从文件导入 {len(chapters)} 个章节：{parsed['filename']}",
        "data": {
            "filename": parsed["filename"],
            "path": parsed.get("path"),
            "format": parsed["format"],
            "word_count": parsed["word_count"],
            "chapters": chapters,
            "total": len(chapters),
            "method": method,
            "needs_review": needs_review,
            "failed_blocks": failed_blocks,
        },
    }


async def import_file_as_project(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    """Create a project and import a local TXT/DOCX file into it as chapters."""
    file_path = str(args.get("file_path") or args.get("path") or "").strip()
    if not file_path:
        return {"tool": "import_file_as_project", "status": "skipped", "detail": "缺少 file_path"}

    parsed = parse_local_file(file_path)
    title = str(args.get("title") or "").strip() or Path(parsed["filename"]).stem or "未命名导入作品"

    from .projects import _project_payload, _tags_to_json

    project = Project(
        title=title[:200],
        description=str(args.get("description") or "") or None,
        tags=_tags_to_json(args.get("tags")),
        narrative_perspective=str(args.get("narrative_perspective") or "third_person")[:50],
        writing_style=str(args.get("writing_style") or "natural")[:50],
        forbidden_sentence_patterns=str(args.get("forbidden_sentence_patterns") or "") or None,
        rhetoric_guidelines=str(args.get("rhetoric_guidelines") or "") or None,
        short_sentences=bool(args.get("short_sentences") or False),
        custom_style_prompt=str(args.get("custom_style_prompt") or "") or None,
        daily_word_goal=int(args.get("daily_word_goal") or 6000),
    )
    db.add(project)
    db.flush()

    text = parsed["text"]
    splits, method, needs_review, failed_blocks = await _resolve_splits(text, args)
    chapters = execute_import(db, project.id, text, splits, None)
    queue_content_sync(
        db,
        ContentSyncIntent(
            project_id=project.id,
            target=ContentSyncTarget.PROJECT,
            source="workspace_import",
        ),
    )
    return {
        "tool": "import_file_as_project",
        "status": "ok",
        "detail": f"已创建作品并导入 {len(chapters)} 个章节：{project.title}",
        "data": {
            "project": _project_payload(project),
            "filename": parsed["filename"],
            "path": parsed.get("path"),
            "format": parsed["format"],
            "word_count": parsed["word_count"],
            "chapters": chapters,
            "total": len(chapters),
            "method": method,
            "needs_review": needs_review,
            "failed_blocks": failed_blocks,
        },
    }
