"""SQLAlchemy and scheduler-engine implementation for scheduled tasks."""

from __future__ import annotations

from datetime import datetime

from ....architecture.uow import SqlAlchemyUnitOfWork
from ....services.scheduler.engine import _compute_next_run, _execute_task, get_active_tasks
from .legacy_models import ScheduledTask


def _payload(task: ScheduledTask) -> dict:
    return {
        "id": task.id,
        "project_id": task.project_id,
        "name": task.name,
        "prompt": task.prompt,
        "cron_expr": task.cron_expr,
        "interval_minutes": task.interval_minutes,
        "tool_policy": task.tool_policy or [],
        "status": task.status,
        "last_run_at": task.last_run_at,
        "last_run_status": task.last_run_status,
        "last_run_output": task.last_run_output,
        "next_run_at": task.next_run_at,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def _find(session, project_id: str, task_id: str) -> ScheduledTask | None:
    return (
        session.query(ScheduledTask)
        .filter(ScheduledTask.id == task_id, ScheduledTask.project_id == project_id)
        .first()
    )


class SqlAlchemyScheduledTasks:
    def list(self, session, project_id: str) -> list[dict]:
        rows = (
            session.query(ScheduledTask)
            .filter(ScheduledTask.project_id == project_id)
            .order_by(ScheduledTask.created_at.desc())
            .all()
        )
        return [_payload(row) for row in rows]

    def create(self, session, project_id: str, values: dict) -> dict:
        with SqlAlchemyUnitOfWork.from_session(session) as uow:
            row = ScheduledTask(project_id=project_id, status="active", **values)
            session.add(row)
            session.flush()
            row.next_run_at = _compute_next_run(row)
            uow.commit()
            session.refresh(row)
        return _payload(row)

    def get(self, session, project_id: str, task_id: str) -> dict | None:
        row = _find(session, project_id, task_id)
        return _payload(row) if row else None

    def update(self, session, project_id: str, task_id: str, values: dict) -> dict | None:
        row = _find(session, project_id, task_id)
        if not row:
            return None
        with SqlAlchemyUnitOfWork.from_session(session) as uow:
            for field, value in values.items():
                setattr(row, field, value)
            row.next_run_at = _compute_next_run(row)
            row.updated_at = datetime.utcnow()
            uow.commit()
            session.refresh(row)
        return _payload(row)

    def delete(self, session, project_id: str, task_id: str) -> bool:
        row = _find(session, project_id, task_id)
        if not row:
            return False
        with SqlAlchemyUnitOfWork.from_session(session) as uow:
            session.delete(row)
            uow.commit()
        return True

    def run_now(self, session, project_id: str, task_id: str) -> dict | None:
        row = _find(session, project_id, task_id)
        if not row:
            return None
        _execute_task(task_id)
        session.refresh(row)
        return _payload(row)

    def is_running(self, task_id: str) -> bool:
        return task_id in get_active_tasks()


__all__ = ["SqlAlchemyScheduledTasks"]
