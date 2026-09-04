"""Workspace tool to launch a local CLI agent worker."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import AgentRun, AgentRunEvent
from ....modules.story.domain.outline_contract import OUTLINE_PROPOSAL_MAX_NODES
from ....services.local_cli_agent_worker import start_local_cli_agent_worker
from ....services.operation_runtime import current_operation_id

_TERMINAL_RUN_STATES = {"completed", "failed", "cancelled"}


def _run_data(run: AgentRun) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "status": run.status,
        "summary": run.summary,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def _recent_events(db: Session, run_id: str, limit: int = 5) -> list[dict[str, Any]]:
    events = (
        db.query(AgentRunEvent)
        .filter(AgentRunEvent.run_id == run_id)
        .order_by(AgentRunEvent.sequence.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "sequence": event.sequence,
            "event_type": event.event_type,
            "status": event.status,
            "message": event.message,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
        for event in reversed(events)
    ]


async def start_local_cli_agent_run(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Start Claude/Codex/opencode as a Siming-managed CLI Agent worker."""
    if str(args.get("_context_execution_route") or "").strip() == "external_mcp":
        return {
            "tool": "start_local_cli_agent_run",
            "status": "skipped",
            "detail": (
                "当前已经在外部 MCP Agent 中，不能递归启动第二个 CLI。"
                "请使用 prepare_external_writing_context、start_agent_run、"
                "report_context_selected 和写入工具完成当前任务。"
            ),
            "data": None,
        }
    task_type = str(args.get("task_type") or "general").strip().lower()
    if task_type not in {"general", "cataloging", "writing", "outline_planning"}:
        task_type = "general"
    user_request = str(args.get("user_request") or args.get("request") or "").strip()
    provider = str(args.get("provider") or "").strip() or None
    parent_operation_id = (
        str(args.get("parent_operation_id") or "").strip()
        or current_operation_id()
    )

    context_arguments = {
        "chapter_id": str(args.get("chapter_id") or "").strip(),
        "outline_node_id": str(args.get("outline_node_id") or "").strip(),
        "parent_id": str(args.get("parent_id") or "").strip(),
        "insert_after_id": str(args.get("insert_after_id") or "").strip(),
        "batch_count": max(
            1,
            min(OUTLINE_PROPOSAL_MAX_NODES, int(args.get("batch_count") or 1)),
        ),
        "requirements": user_request,
        "pinned_chunk_ids": args.get("pinned_chunk_ids") if isinstance(args.get("pinned_chunk_ids"), list) else [],
        "pinned_source_ids": args.get("pinned_source_ids") if isinstance(args.get("pinned_source_ids"), list) else [],
        "parent_operation_id": parent_operation_id or "",
    }
    result = start_local_cli_agent_worker(
        db,
        project_id,
        user_request=user_request,
        task_type=task_type,
        provider=provider,
        context_manifest_id=str(args.get("context_manifest_id") or "").strip() or None,
        context_arguments=context_arguments,
    )
    return {
        "tool": "start_local_cli_agent_run",
        "status": result.get("status", "ok"),
        "detail": result.get("detail", ""),
        "data": result.get("data"),
    }


async def wait_local_cli_agent_run(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Wait for a Siming-managed local CLI run and validate the requested outcome."""
    run_id = str(args.get("run_id") or "").strip()
    if not run_id or run_id.startswith("{"):
        return {"tool": "wait_local_cli_agent_run", "status": "error", "detail": "本机 CLI 没有成功启动，未获得 run_id", "data": None}

    timeout_seconds = max(1, min(int(args.get("timeout_seconds") or 1800), 7200))
    startup_timeout_seconds = max(1, min(int(args.get("startup_timeout_seconds") or 10), timeout_seconds))
    poll_seconds = max(0.5, min(float(args.get("poll_seconds") or 2), 10))
    started = time.monotonic()

    run: AgentRun | None = None
    while True:
        db.expire_all()
        run = (
            db.query(AgentRun)
            .filter(AgentRun.id == run_id, AgentRun.project_id == project_id)
            .first()
        )
        if not run:
            return {"tool": "wait_local_cli_agent_run", "status": "skipped", "detail": "未找到本机 CLI 运行记录", "data": None}
        if run.status in _TERMINAL_RUN_STATES:
            break
        if run.status == "created" and time.monotonic() - started >= startup_timeout_seconds:
            return {
                "tool": "wait_local_cli_agent_run",
                "status": "error",
                "detail": f"本机 CLI 未在 {startup_timeout_seconds} 秒内开始运行；请检查 CLI 命令、登录状态和 MCP 配置",
                "data": {"run": _run_data(run), "events": _recent_events(db, run_id)},
            }
        if time.monotonic() - started >= timeout_seconds:
            return {
                "tool": "wait_local_cli_agent_run",
                "status": "error",
                "detail": f"本机 CLI 仍在运行，等待超过 {timeout_seconds} 秒；请在运行记录中查看进度",
                "data": {"run": _run_data(run), "events": _recent_events(db, run_id)},
            }
        await asyncio.sleep(poll_seconds)

    data: dict[str, Any] = {"run": _run_data(run), "events": _recent_events(db, run_id)}
    if run.status != "completed":
        return {
            "tool": "wait_local_cli_agent_run",
            "status": "error",
            "detail": run.summary or f"本机 CLI 运行失败：{run.status}",
            "data": data,
        }

    return {
        "tool": "wait_local_cli_agent_run",
        "status": "ok",
        "detail": "本机 CLI 运行完成",
        "data": data,
    }
