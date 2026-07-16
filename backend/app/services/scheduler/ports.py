"""Application-provided ports used by the scheduler domain service."""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from ...database.models import ScheduledTask

ScheduledTaskRunner = Callable[[Session, ScheduledTask], str]

_task_runner: ScheduledTaskRunner | None = None


def configure_task_runner(runner: ScheduledTaskRunner) -> None:
    """Bind the workspace implementation at the application composition root."""
    global _task_runner
    _task_runner = runner


def run_scheduled_task(db: Session, task: ScheduledTask) -> str:
    if _task_runner is None:
        raise RuntimeError("Scheduled task runner is not configured.")
    return _task_runner(db, task)


__all__ = [
    "ScheduledTaskRunner",
    "configure_task_runner",
    "run_scheduled_task",
]
