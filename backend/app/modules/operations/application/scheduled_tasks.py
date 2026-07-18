"""Application contract for project scheduled tasks."""

from __future__ import annotations

from typing import Any, Protocol


class ScheduledTaskPort(Protocol):
    def list(self, session: Any, project_id: str) -> list[dict]: ...

    def create(self, session: Any, project_id: str, values: dict) -> dict: ...

    def get(self, session: Any, project_id: str, task_id: str) -> dict | None: ...

    def update(
        self,
        session: Any,
        project_id: str,
        task_id: str,
        values: dict,
    ) -> dict | None: ...

    def delete(self, session: Any, project_id: str, task_id: str) -> bool: ...

    def run_now(self, session: Any, project_id: str, task_id: str) -> dict | None: ...

    def is_running(self, task_id: str) -> bool: ...


_tasks: ScheduledTaskPort | None = None


def configure_scheduled_tasks(tasks: ScheduledTaskPort) -> None:
    global _tasks
    _tasks = tasks


def get_scheduled_tasks() -> ScheduledTaskPort:
    if _tasks is None:
        raise RuntimeError("Scheduled tasks have not been configured")
    return _tasks


__all__ = ["ScheduledTaskPort", "configure_scheduled_tasks", "get_scheduled_tasks"]
