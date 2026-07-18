"""API router for scheduled tasks."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.operations.application.scheduled_tasks import get_scheduled_tasks
from ..schemas.scheduler import (
    ScheduledTaskCreate,
    ScheduledTaskListResponse,
    ScheduledTaskResponse,
    ScheduledTaskRunResponse,
    ScheduledTaskUpdate,
)

router = APIRouter(prefix="/projects/{project_id}/scheduled-tasks", tags=["scheduler"])


def _task_to_response(task: dict) -> ScheduledTaskResponse:
    return ScheduledTaskResponse(**task)


@router.get("")
def list_scheduled_tasks(
    project_id: str,
    db: Session = Depends(get_db),
):
    """List all scheduled tasks for a project."""
    get_project_or_404(db, project_id)
    tasks = get_scheduled_tasks().list(db, project_id)
    payload = ScheduledTaskListResponse(
        items=[_task_to_response(t) for t in tasks],
        total=len(tasks),
    )
    return ApiResponse.success(data=payload)


@router.post("")
def create_scheduled_task(
    project_id: str,
    body: ScheduledTaskCreate,
    db: Session = Depends(get_db),
):
    """Create a new scheduled task."""
    get_project_or_404(db, project_id)

    # Validate cron expression if provided
    if body.cron_expr:
        try:
            from croniter import croniter
            croniter(body.cron_expr)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid cron expression: {exc}")

    task = get_scheduled_tasks().create(
        db,
        project_id,
        body.model_dump(),
    )

    return ApiResponse.success(data=_task_to_response(task), message="定时任务已创建")


@router.get("/{task_id}")
def get_scheduled_task(
    project_id: str,
    task_id: str,
    db: Session = Depends(get_db),
):
    """Get a scheduled task by ID."""
    get_project_or_404(db, project_id)
    task = get_scheduled_tasks().get(db, project_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return ApiResponse.success(data=_task_to_response(task))


@router.put("/{task_id}")
def update_scheduled_task(
    project_id: str,
    task_id: str,
    body: ScheduledTaskUpdate,
    db: Session = Depends(get_db),
):
    """Update a scheduled task."""
    get_project_or_404(db, project_id)
    values = body.model_dump(exclude_unset=True)
    if body.cron_expr is not None:
        # Validate cron expression
        if body.cron_expr:
            try:
                from croniter import croniter
                croniter(body.cron_expr)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid cron expression: {exc}")
    if body.status is not None:
        if body.status not in ("active", "paused"):
            raise HTTPException(status_code=400, detail="Status must be 'active' or 'paused'")
    task = get_scheduled_tasks().update(db, project_id, task_id, values)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return ApiResponse.success(data=_task_to_response(task), message="定时任务已更新")


@router.delete("/{task_id}")
def delete_scheduled_task(
    project_id: str,
    task_id: str,
    db: Session = Depends(get_db),
):
    """Delete a scheduled task."""
    get_project_or_404(db, project_id)
    if not get_scheduled_tasks().delete(db, project_id, task_id):
        raise HTTPException(status_code=404, detail="Task not found")

    return ApiResponse.success(data={"status": "ok", "detail": "Task deleted"}, message="定时任务已删除")


@router.post("/{task_id}/run-now")
def run_scheduled_task_now(
    project_id: str,
    task_id: str,
    db: Session = Depends(get_db),
):
    """Run a scheduled task immediately."""
    get_project_or_404(db, project_id)
    if not get_scheduled_tasks().get(db, project_id, task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    if get_scheduled_tasks().is_running(task_id):
        raise HTTPException(status_code=409, detail="Task is already running")

    # Run task synchronously (blocking)
    started_at = datetime.utcnow()
    try:
        task = get_scheduled_tasks().run_now(db, project_id, task_id)
        payload = ScheduledTaskRunResponse(
            task_id=task_id,
            status=task["last_run_status"] or "completed",
            output=task["last_run_output"],
            started_at=started_at,
            completed_at=datetime.utcnow(),
        )
        return ApiResponse.success(data=payload, message="定时任务执行完成")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Task execution failed: {exc}")


@router.get("/{task_id}/logs")
def get_scheduled_task_logs(
    project_id: str,
    task_id: str,
    db: Session = Depends(get_db),
):
    """Get the last run output for a scheduled task."""
    get_project_or_404(db, project_id)
    task = get_scheduled_tasks().get(db, project_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return ApiResponse.success(data={
        "task_id": task_id,
        "last_run_at": task["last_run_at"],
        "last_run_status": task["last_run_status"],
        "last_run_output": task["last_run_output"],
    })
