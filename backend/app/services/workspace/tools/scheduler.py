"""Scheduled task workspace tools."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import ScheduledTask
from ...scheduler.engine import _compute_next_run, _execute_task, get_active_tasks


def _task_payload(task: ScheduledTask) -> dict:
    return {
        "id": task.id,
        "project_id": task.project_id,
        "name": task.name,
        "prompt": task.prompt,
        "cron_expr": task.cron_expr,
        "interval_minutes": task.interval_minutes,
        "tool_policy": task.tool_policy or [],
        "status": task.status,
        "last_run_at": task.last_run_at.isoformat() if task.last_run_at else None,
        "last_run_status": task.last_run_status,
        "last_run_output": task.last_run_output,
        "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


def _find_task(db: Session, project_id: str, args: dict[str, Any]) -> ScheduledTask | None:
    task_id = str(args.get("id") or args.get("task_id") or "").strip()
    if task_id:
        return db.query(ScheduledTask).filter(ScheduledTask.id == task_id, ScheduledTask.project_id == project_id).first()
    name = str(args.get("name") or "").strip()
    if name:
        return db.query(ScheduledTask).filter(ScheduledTask.project_id == project_id, ScheduledTask.name == name).first()
    return None


def _validate_cron(cron_expr: str | None) -> str | None:
    if not cron_expr:
        return None
    from croniter import croniter

    croniter(cron_expr)
    return cron_expr


async def list_scheduled_tasks(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    tasks = (
        db.query(ScheduledTask)
        .filter(ScheduledTask.project_id == project_id)
        .order_by(ScheduledTask.created_at.desc())
        .all()
    )
    return {
        "tool": "list_scheduled_tasks",
        "status": "ok",
        "detail": f"共 {len(tasks)} 个自动任务",
        "data": {"items": [_task_payload(task) for task in tasks], "total": len(tasks)},
    }


async def create_scheduled_task(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    name = str(args.get("name") or "").strip()
    prompt = str(args.get("prompt") or "").strip()
    if not name or not prompt:
        return {"tool": "create_scheduled_task", "status": "skipped", "detail": "任务名称或提示词为空"}

    from ..idempotency import check_idempotency, generate_idempotency_key

    idem_key = generate_idempotency_key(db, "create_scheduled_task", project_id, args)
    if idem_key:
        existing = check_idempotency(db, project_id, idem_key)
        if existing:
            return existing

    cron_expr = _validate_cron(str(args.get("cron_expr") or "").strip() or None)
    interval = args.get("interval_minutes")
    task = ScheduledTask(
        project_id=project_id,
        name=name[:200],
        prompt=prompt,
        cron_expr=cron_expr,
        interval_minutes=max(1, int(interval)) if interval is not None else None,
        tool_policy=args.get("tool_policy") if isinstance(args.get("tool_policy"), list) else [],
        status=str(args.get("status") or "active"),
    )
    task.next_run_at = _compute_next_run(task)
    db.add(task)
    db.flush()
    return {
        "tool": "create_scheduled_task",
        "status": "ok",
        "detail": f"已创建自动任务：{task.name}",
        "data": _task_payload(task),
    }


async def update_scheduled_task(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    task = _find_task(db, project_id, args)
    if not task:
        return {"tool": "update_scheduled_task", "status": "skipped", "detail": "未找到自动任务"}
    if "name" in args and args.get("name"):
        task.name = str(args.get("name")).strip()[:200]
    if "prompt" in args:
        task.prompt = str(args.get("prompt") or "")
    if "cron_expr" in args:
        task.cron_expr = _validate_cron(str(args.get("cron_expr") or "").strip() or None)
    if "interval_minutes" in args:
        value = args.get("interval_minutes")
        task.interval_minutes = max(1, int(value)) if value is not None else None
    if "tool_policy" in args and isinstance(args.get("tool_policy"), list):
        task.tool_policy = args.get("tool_policy") or []
    if "status" in args:
        status = str(args.get("status") or task.status)
        if status not in {"active", "paused"}:
            return {"tool": "update_scheduled_task", "status": "skipped", "detail": "状态只能是 active 或 paused"}
        task.status = status
    task.next_run_at = _compute_next_run(task)
    task.updated_at = datetime.utcnow()
    db.flush()
    return {
        "tool": "update_scheduled_task",
        "status": "ok",
        "detail": f"已更新自动任务：{task.name}",
        "data": _task_payload(task),
    }


async def delete_scheduled_task(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    task = _find_task(db, project_id, args)
    if not task:
        return {"tool": "delete_scheduled_task", "status": "skipped", "detail": "未找到自动任务"}
    name = task.name
    task_id = task.id
    db.delete(task)
    db.flush()
    return {"tool": "delete_scheduled_task", "status": "ok", "detail": f"已删除自动任务：{name}", "data": {"id": task_id}}


async def run_scheduled_task_now(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    task = _find_task(db, project_id, args)
    if not task:
        return {"tool": "run_scheduled_task_now", "status": "skipped", "detail": "未找到自动任务"}
    if task.id in get_active_tasks():
        return {"tool": "run_scheduled_task_now", "status": "skipped", "detail": "任务正在运行中"}
    _execute_task(task.id)
    db.refresh(task)
    return {
        "tool": "run_scheduled_task_now",
        "status": "ok",
        "detail": f"已执行自动任务：{task.name}",
        "data": _task_payload(task),
    }
