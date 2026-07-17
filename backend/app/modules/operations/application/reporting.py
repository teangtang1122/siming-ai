"""Configured cross-module operation reporting port."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

CheckpointReporter = Callable[[Any, str | None, dict], None]
_checkpoint_reporter: CheckpointReporter | None = None


def configure_checkpoint_reporter(reporter: CheckpointReporter) -> None:
    global _checkpoint_reporter
    _checkpoint_reporter = reporter


def checkpoint_operation(db: Any, operation_id: str | None, *, payload: dict) -> None:
    if _checkpoint_reporter is None:
        raise RuntimeError("Operation checkpoint reporter has not been configured")
    _checkpoint_reporter(db, operation_id, payload)
