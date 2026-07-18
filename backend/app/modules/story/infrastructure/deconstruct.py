"""SQLAlchemy read adapter for book deconstruction."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ....core.db_helpers import get_project_or_404
from ....services.deconstruct.report_store import report_payload
from .entities import Chapter, DeconstructionReport


class SqlAlchemyDeconstructionReader:
    def __init__(self, session: Session) -> None:
        self._session = session

    def preview(self, project_id: str) -> dict:
        get_project_or_404(self._session, project_id)
        chapters = (
            self._session.query(Chapter)
            .filter(Chapter.project_id == project_id)
            .order_by(Chapter.created_at.asc())
            .all()
        )
        chapter_opts = [
            {
                "id": chapter.id,
                "title": chapter.title,
                "word_count": chapter.word_count or 0,
                "preview": (chapter.content or "")[:200],
            }
            for chapter in chapters
        ]
        total_words = sum(chapter.word_count or 0 for chapter in chapters)
        combined_text = "\n\n".join(
            f"{'=' * 40}\n{chapter.title}\n{'=' * 40}\n\n{chapter.content or ''}"
            for chapter in chapters
        )
        return {
            "chapters": chapter_opts,
            "total_chapters": len(chapters),
            "total_words": total_words,
            "can_deconstruct": total_words > 500,
            "combined_text": (
                combined_text if total_words <= 80000 else combined_text[:80000]
            ),
        }

    def reports(self, project_id: str, limit: int = 20) -> dict:
        get_project_or_404(self._session, project_id)
        reports = (
            self._session.query(DeconstructionReport)
            .filter(DeconstructionReport.project_id == project_id)
            .order_by(DeconstructionReport.created_at.desc())
            .limit(limit)
            .all()
        )
        items: list[dict] = []
        for report in reports:
            payload = report_payload(report)
            items.append(
                {
                    "id": report.id,
                    "title": payload.get("title") or report.source_filename,
                    "status": report.status,
                    "phase": payload.get("phase", report.status),
                    "total_chunks": payload.get("total_chunks", 0),
                    "completed_chunks": payload.get("completed_chunks", 0),
                    "failed_chunks": payload.get("failed_chunks", 0),
                    "total_words": payload.get("total_words", 0),
                    "created_at": (
                        report.created_at.isoformat() if report.created_at else None
                    ),
                    "completed_at": payload.get("completed_at"),
                }
            )
        return {"items": items, "total": len(items)}


__all__ = ["SqlAlchemyDeconstructionReader"]
