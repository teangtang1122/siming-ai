"""SQLAlchemy implementation of cataloging HTTP queries."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from .models import (
    CatalogingCandidate,
    CatalogingChapterRun,
    CatalogingFact,
    CatalogingJob,
)


class SqlAlchemyCatalogingQueries:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_job(self, project_id: str, job_id: str):
        return self.session.query(CatalogingJob).filter(
            CatalogingJob.id == job_id,
            CatalogingJob.project_id == project_id,
        ).first()

    def get_candidate(self, project_id: str, candidate_id: str):
        return self.session.query(CatalogingCandidate).filter(
            CatalogingCandidate.id == candidate_id,
            CatalogingCandidate.project_id == project_id,
        ).first()

    def list_jobs(self, project_id: str, *, limit: int = 20) -> Sequence[CatalogingJob]:
        return self.session.query(CatalogingJob).filter(
            CatalogingJob.project_id == project_id,
        ).order_by(CatalogingJob.created_at.desc()).limit(limit).all()

    def list_runs(self, job_id: str) -> Sequence[CatalogingChapterRun]:
        return self.session.query(CatalogingChapterRun).filter(
            CatalogingChapterRun.job_id == job_id,
        ).order_by(CatalogingChapterRun.chapter_order.asc()).all()

    def list_candidates(
        self,
        job_id: str,
        *,
        chapter_run_id: str | None = None,
        status: str | None = None,
        item_type: str | None = None,
        candidate_ids: Sequence[str] | None = None,
    ) -> Sequence[CatalogingCandidate]:
        query = self.session.query(CatalogingCandidate).filter(CatalogingCandidate.job_id == job_id)
        if chapter_run_id:
            query = query.filter(CatalogingCandidate.chapter_run_id == chapter_run_id)
        if status:
            query = query.filter(CatalogingCandidate.status == status)
        if item_type:
            query = query.filter(CatalogingCandidate.item_type == item_type)
        if candidate_ids:
            query = query.filter(CatalogingCandidate.id.in_(candidate_ids))
        return query.order_by(CatalogingCandidate.created_at.asc()).all()

    def list_facts(
        self,
        job_id: str,
        *,
        chapter_run_id: str | None = None,
        fact_type: str | None = None,
    ) -> Sequence[CatalogingFact]:
        query = self.session.query(CatalogingFact).filter(CatalogingFact.job_id == job_id)
        if chapter_run_id:
            query = query.filter(CatalogingFact.chapter_run_id == chapter_run_id)
        if fact_type:
            query = query.filter(CatalogingFact.fact_type == fact_type)
        return query.order_by(
            CatalogingFact.sort_order.asc(),
            CatalogingFact.created_at.asc(),
        ).all()

    def get_run(self, job_id: str, run_id: str):
        return self.session.query(CatalogingChapterRun).filter(
            CatalogingChapterRun.id == run_id,
            CatalogingChapterRun.job_id == job_id,
        ).first()

    def first_awaiting_confirmation(self, job_id: str):
        return self.session.query(CatalogingChapterRun).filter(
            CatalogingChapterRun.job_id == job_id,
            CatalogingChapterRun.status == "awaiting_confirmation",
        ).order_by(CatalogingChapterRun.chapter_order.asc()).first()

    def first_resolution_candidate(self, job_id: str):
        return self.session.query(CatalogingChapterRun).filter(
            CatalogingChapterRun.job_id == job_id,
            CatalogingChapterRun.status.in_(
                ["extracting", "facts_saved", "awaiting_confirmation", "failed"]
            ),
        ).order_by(CatalogingChapterRun.chapter_order.asc()).first()


__all__ = ["SqlAlchemyCatalogingQueries"]
