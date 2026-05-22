"""Chapter workspace tools."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....database.models import Chapter
from ..types import WorkspaceActionDependencies
from ..utils import find_outline_by_title_or_id


def _character_names(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(name) for name in value if name]


async def create_chapter(
    db: Session,
    project_id: str,
    args: dict[str, Any],
    deps: WorkspaceActionDependencies,
) -> dict:
    title = str(args.get("title") or "").strip()
    content = str(args.get("content") or "")
    if not title or not content.strip():
        return {"tool": "create_chapter", "status": "skipped", "detail": "章节标题或正文为空"}

    project = deps.get_project(db, project_id)
    violations = deps.detect_forbidden_sentence_violations(content, project)
    if violations:
        try:
            model = str(args.get("model") or "") or None
            content, before, remaining = await deps.repair_forbidden_sentence_text(
                content,
                project,
                model,
                None,
            )
        except Exception:
            pass  # fall through with original content

    outline_node = None
    for ref in (args.get("outline_node_id"), args.get("outline_node_title"), args.get("outline_title")):
        outline_node = find_outline_by_title_or_id(db, project_id, ref)
        if outline_node:
            break
    outline_node_id = outline_node.id if outline_node else None

    existing = None
    if outline_node_id:
        existing = (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.outline_node_id == outline_node_id)
            .order_by(Chapter.created_at.desc())
            .first()
        ) or (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.title == title)
            .order_by(Chapter.created_at.desc())
            .first()
        )
    if not existing:
        existing = (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.title == title)
            .order_by(Chapter.created_at.desc())
            .first()
        )

    involved_characters = _character_names(args.get("involved_characters"))
    model = str(args.get("model") or "") or None
    if existing:
        existing = deps.finalize_assistant_chapter(
            db,
            existing,
            title,
            content,
            str(args.get("summary") or ""),
            involved_characters,
            model,
        )
        return {
            "tool": "create_chapter",
            "status": "ok",
            "detail": f"已更新章节：{existing.title}",
            "data": {"id": existing.id, "title": existing.title},
        }

    chapter = deps.create_assistant_chapter(
        db,
        project_id,
        title[:200],
        content,
        outline_node_id,
        str(args.get("summary") or ""),
        involved_characters,
        model,
    )
    if not chapter:
        return {"tool": "create_chapter", "status": "skipped", "detail": "章节创建失败"}
    return {
        "tool": "create_chapter",
        "status": "ok",
        "detail": f"已创建章节：{chapter.title}",
        "data": {"id": chapter.id, "title": chapter.title},
    }

