"""Background scheduler engine for timed tasks."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import ScheduledTask
from ...database.session import SessionLocal

logger = logging.getLogger(__name__)

# Check interval in seconds
_CHECK_INTERVAL = 60

# Maximum concurrent tasks
_MAX_CONCURRENT = 3

# Active task threads
_active_tasks: dict[str, threading.Thread] = {}
_active_lock = threading.Lock()


def _compute_next_run(task: ScheduledTask) -> datetime | None:
    """Compute the next run time for a task."""
    now = datetime.utcnow()

    if task.cron_expr:
        try:
            from croniter import croniter
            cron = croniter(task.cron_expr, now)
            return cron.get_next(datetime)
        except Exception as exc:
            logger.error("Failed to parse cron expression '%s': %s", task.cron_expr, exc)
            return None

    if task.interval_minutes:
        return now + timedelta(minutes=task.interval_minutes)

    return None


def _execute_task(task_id: str) -> None:
    """Execute a scheduled task in a background thread."""
    db = SessionLocal()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        if not task:
            logger.error("Task %s not found", task_id)
            return

        task.last_run_at = datetime.utcnow()
        task.last_run_status = "running"
        db.commit()

        try:
            # Import here to avoid circular imports
            from ..workspace.executor import execute_workspace_action

            # Build a simple prompt that the workspace assistant can handle
            result = _run_task_prompt(db, task)
            task.last_run_status = "completed"
            task.last_run_output = result[:10000] if result else "完成"
        except Exception as exc:
            logger.exception("Task %s failed: %s", task_id, exc)
            task.last_run_status = "error"
            task.last_run_output = str(exc)[:10000]

        # Compute next run time
        task.next_run_at = _compute_next_run(task)
        task.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()
        with _active_lock:
            _active_tasks.pop(task_id, None)


def _run_task_prompt(db: Session, task: ScheduledTask) -> str:
    """Run a task through the agent tool chain.

    Uses the LLM with tool-calling support so the agent can search project
    data, call analysis tools, and persist results — not just a single LLM call.
    """
    import asyncio
    from ...ai.gateway import LLMGateway
    from ...services.workspace.executor import execute_workspace_action
    from ...services.workspace.registry import registry

    # Build system prompt with tool policy
    system_parts = ["你是一个定时任务执行助手。请根据用户的提示完成任务。"]
    if task.tool_policy:
        system_parts.append(f"可用工具：{', '.join(task.tool_policy)}")

    messages = [
        {"role": "system", "content": "\n".join(system_parts)},
        {"role": "user", "content": task.prompt},
    ]

    # Determine allowed tools
    tool_policy_set = set(task.tool_policy) if task.tool_policy else None
    tool_schemas = registry.get_schemas()
    if tool_policy_set:
        tool_schemas = [
            t for t in tool_schemas
            if t["function"]["name"] in tool_policy_set
        ]

    tool_logs: list[dict[str, Any]] = []

    async def _run_agent_loop() -> str:
        """Execute the agent loop with tool calling."""
        max_turns = 10
        final_content = ""

        for turn in range(max_turns):
            result = await LLMGateway.stream_chat_completion_with_tools(
                messages=messages,
                tools=tool_schemas,
                model=None,
                temperature=0.3,
                max_tokens=4000,
                timeout=120,
            )

            content = result.get("content", "")
            tool_calls = result.get("tool_calls", [])

            if not tool_calls:
                final_content = content
                break

            # Append assistant message with tool calls
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })

            # Execute each tool call
            for tc in tool_calls:
                tool_name = tc.get("function", {}).get("name", "")
                import json
                try:
                    args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}

                # Execute through workspace executor
                tool_result = await execute_workspace_action(
                    db, task.project_id,
                    {"tool": tool_name, "arguments": args},
                )

                # Log the tool call
                tool_logs.append({
                    "tool": tool_name,
                    "args_summary": str(args)[:200],
                    "status": tool_result.get("status", "unknown"),
                })

                # Append tool result to messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(tool_result, ensure_ascii=False)[:4000],
                })

        return final_content or "任务执行完成"

    try:
        return asyncio.run(_run_agent_loop())
    except Exception as exc:
        raise RuntimeError(f"Agent execution failed: {exc}") from exc


def check_and_run_tasks() -> None:
    """Check for due tasks and run them. Called periodically by the scheduler thread."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()

        # Find tasks that are due
        due_tasks = (
            db.query(ScheduledTask)
            .filter(
                ScheduledTask.status == "active",
                ScheduledTask.next_run_at <= now,
            )
            .limit(_MAX_CONCURRENT)
            .all()
        )

        for task in due_tasks:
            with _active_lock:
                if task.id in _active_tasks:
                    continue
                if len(_active_tasks) >= _MAX_CONCURRENT:
                    break

            logger.info("Starting scheduled task: %s (%s)", task.name, task.id)
            thread = threading.Thread(
                target=_execute_task,
                args=(task.id,),
                name=f"scheduler-{task.id}",
                daemon=True,
            )
            with _active_lock:
                _active_tasks[task.id] = thread
            thread.start()
    finally:
        db.close()


def _scheduler_loop() -> None:
    """Main scheduler loop that runs in a background thread."""
    logger.info("Scheduler engine started")
    while True:
        try:
            check_and_run_tasks()
        except Exception as exc:
            logger.exception("Scheduler check failed: %s", exc)
        time.sleep(_CHECK_INTERVAL)


_scheduler_thread: threading.Thread | None = None


def start_scheduler() -> None:
    """Start the background scheduler thread."""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        name="scheduler-engine",
        daemon=True,
    )
    _scheduler_thread.start()


def stop_scheduler() -> None:
    """Stop the background scheduler thread."""
    global _scheduler_thread
    if _scheduler_thread:
        # Thread is daemon, it will stop when process exits
        _scheduler_thread = None


def get_active_tasks() -> list[str]:
    """Get list of currently running task IDs."""
    with _active_lock:
        return list(_active_tasks.keys())
