"""Configured entry point for persistent context rebuilds."""

from __future__ import annotations

from collections.abc import Callable

_runner: Callable[[str], None] | None = None


def configure_context_rebuild_runner(runner: Callable[[str], None]) -> None:
    global _runner
    _runner = runner


def run_context_rebuild(job_id: str) -> None:
    if _runner is None:
        raise RuntimeError("Context rebuild runner has not been configured")
    _runner(job_id)
