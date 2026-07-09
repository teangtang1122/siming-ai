"""Validation helpers for deciding when a cataloging chapter is writable."""
from __future__ import annotations

from typing import Any, Iterable

from ..story_granularity import CandidateCoverage, inspect_candidate_coverage_items


def inspect_candidate_coverage(candidates: Iterable[Any]) -> CandidateCoverage:
    """Return the shared story-granularity coverage for candidate rows."""
    return inspect_candidate_coverage_items(candidates)
