"""Read operations required by the cataloging HTTP interface."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class CatalogingQueries(Protocol):
    def get_job(self, project_id: str, job_id: str) -> Any | None: ...

    def get_candidate(self, project_id: str, candidate_id: str) -> Any | None: ...

    def list_jobs(self, project_id: str, *, limit: int = 20) -> Sequence[Any]: ...

    def list_runs(self, job_id: str) -> Sequence[Any]: ...

    def list_candidates(
        self,
        job_id: str,
        *,
        chapter_run_id: str | None = None,
        status: str | None = None,
        item_type: str | None = None,
        candidate_ids: Sequence[str] | None = None,
    ) -> Sequence[Any]: ...

    def list_facts(
        self,
        job_id: str,
        *,
        chapter_run_id: str | None = None,
        fact_type: str | None = None,
    ) -> Sequence[Any]: ...

    def get_run(self, job_id: str, run_id: str) -> Any | None: ...

    def first_awaiting_confirmation(self, job_id: str) -> Any | None: ...

    def first_resolution_candidate(self, job_id: str) -> Any | None: ...


__all__ = ["CatalogingQueries"]
