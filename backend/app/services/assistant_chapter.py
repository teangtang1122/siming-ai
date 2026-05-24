"""Assistant chapter helpers — creation, placeholder, finalization, and action execution."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.utils import count_words
from ..database.models import (
    Chapter,
    ChapterCharacter,
    ChapterSnapshot,
    ChapterSummary,
    Character,
)
from ..services.context_builders import _get_outline_node_or_404
from ..services.style_rules import _detect_forbidden_sentence_violations, _repair_forbidden_sentence_text
from .workspace import execute_workspace_action


async def _execute_workspace_action(db: Session, project_id: str, action: dict) -> dict:
    tool = str(action.get("tool") or "").strip()
    args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}

    if tool == "create_chapter" and args.get("content"):
        project = get_project_or_404(db, project_id)
        violations = _detect_forbidden_sentence_violations(str(args.get("content")), project)
        if violations:
            try:
                model = str(args.get("model") or "") or None
                repaired, before, remaining = await _repair_forbidden_sentence_text(
                    str(args.get("content")),
                    project,
                    model,
                    None,
                )
                args = {**args, "content": repaired}
                action = {**action, "arguments": args}
            except Exception:
                pass

    return await execute_workspace_action(db, project_id, action)


def _create_assistant_chapter(
    db: Session,
    project_id: str,
    title: str,
    content: str,
    outline_node_id: Optional[str],
    summary_text: str,
    involved_character_names: list[str],
    model: Optional[str],
) -> Optional[Chapter]:
    title = (title or "").strip()[:200]
    content = (content or "").strip()
    if not title or not content:
        return None
    outline_node = _get_outline_node_or_404(db, project_id, outline_node_id)
    chapter = Chapter(
        project_id=project_id,
        outline_node_id=outline_node.id if outline_node else None,
        title=title,
        content=content,
        word_count=count_words(content),
        current_version=1,
    )
    db.add(chapter)
    db.flush()
    db.add(ChapterSummary(
        chapter_id=chapter.id,
        summary_text=(summary_text or title)[:20000],
        key_events=None,
        token_count=len(summary_text or title),
        ai_model=model,
    ))
    names = {name.strip() for name in involved_character_names if name and name.strip()}
    if names:
        characters = (
            db.query(Character)
            .filter(Character.project_id == project_id, Character.name.in_(names))
            .all()
        )
        for character in characters:
            db.add(ChapterCharacter(
                chapter_id=chapter.id,
                character_id=character.id,
                appearance_type="AI助手识别",
                description="由自动写作助手创建章节时关联",
            ))
    return chapter


def _chapter_brief(chapter: Chapter) -> dict:
    return {
        "id": chapter.id,
        "title": chapter.title,
        "outline_node_id": chapter.outline_node_id,
        "word_count": chapter.word_count or 0,
    }


def _create_assistant_chapter_placeholder(
    db: Session,
    project_id: str,
    title: str,
    outline_node_id: Optional[str],
) -> Chapter:
    outline_node = _get_outline_node_or_404(db, project_id, outline_node_id)
    clean_title = (title or "AI生成章节").strip()[:200] or "AI生成章节"
    chapter = Chapter(
        project_id=project_id,
        outline_node_id=outline_node.id if outline_node else None,
        title=clean_title,
        content="（AI正在生成正文，完成后会自动写入。）",
        word_count=0,
        current_version=1,
    )
    db.add(chapter)
    db.flush()
    return chapter


def _finalize_assistant_chapter(
    db: Session,
    chapter: Chapter,
    title: str,
    content: str,
    summary_text: str,
    involved_character_names: list[str],
    model: Optional[str],
) -> Chapter:
    clean_title = (title or chapter.title or "AI生成章节").strip()[:200] or "AI生成章节"
    clean_content = (content or "").strip()
    chapter.title = clean_title
    chapter.content = clean_content
    chapter.word_count = count_words(clean_content)
    chapter.current_version = max(1, chapter.current_version or 1) + 1
    chapter.updated_at = datetime.utcnow()
    db.add(ChapterSnapshot(
        chapter_id=chapter.id,
        version_number=chapter.current_version,
        content=clean_content,
        word_count=chapter.word_count,
        trigger_type="ai_insert",
    ))

    if chapter.summary:
        chapter.summary.summary_text = (summary_text or clean_title)[:20000]
        chapter.summary.key_events = None
        chapter.summary.token_count = len(summary_text or clean_title)
        chapter.summary.ai_model = model
        chapter.summary.updated_at = datetime.utcnow()
    else:
        db.add(ChapterSummary(
            chapter_id=chapter.id,
            summary_text=(summary_text or clean_title)[:20000],
            key_events=None,
            token_count=len(summary_text or clean_title),
            ai_model=model,
        ))

    names = {name.strip() for name in involved_character_names if name and name.strip()}
    if names:
        db.query(ChapterCharacter).filter(ChapterCharacter.chapter_id == chapter.id).delete()
        characters = (
            db.query(Character)
            .filter(Character.project_id == chapter.project_id, Character.name.in_(names))
            .all()
        )
        for character in characters:
            db.add(ChapterCharacter(
                chapter_id=chapter.id,
                character_id=character.id,
                appearance_type="AI助手识别",
                description="由自动写作助手创建章节时关联",
            ))
    return chapter
