"""Workspace tool to launch a local CLI agent worker."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ....core.utils import count_words
from ....database.models import AgentRun, AgentRunEvent, Chapter, ChapterDraft, Project
from ....services.storage_contract import storage_health
from ....services.local_cli_agent_worker import start_local_cli_agent_worker


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


def _validate_writing_result(
    db: Session,
    project_id: str,
    run: AgentRun,
    args: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    outline_node_id = str(args.get("outline_node_id") or "").strip()
    project = db.query(Project).filter(Project.id == project_id).first()
    since = run.created_at or datetime.utcnow()

    query = db.query(Chapter).filter(Chapter.project_id == project_id)
    if outline_node_id:
        query = query.filter(Chapter.outline_node_id == outline_node_id)
    else:
        query = query.filter(
            (Chapter.created_at >= since) | (Chapter.updated_at >= since)
        )
    chapters = query.order_by(Chapter.updated_at.desc(), Chapter.created_at.desc()).limit(5).all()
    data: dict[str, Any] = {
        "chapters": [
            {
                "chapter_id": chapter.id,
                "title": chapter.title,
                "outline_node_id": chapter.outline_node_id,
                "word_count": chapter.word_count or 0,
                "content_file_path": chapter.content_file_path,
                "updated_at": chapter.updated_at.isoformat() if chapter.updated_at else None,
            }
            for chapter in chapters
        ],
    }
    if chapters:
        return True, f"本机 CLI 写作已入库：{chapters[0].title}", data

    drafts = (
        db.query(ChapterDraft)
        .filter(ChapterDraft.project_id == project_id, ChapterDraft.created_at >= since)
        .order_by(ChapterDraft.created_at.desc())
        .limit(5)
        .all()
    )
    data["drafts"] = [
        {
            "draft_id": draft.id,
            "title": draft.title,
            "outline_node_id": draft.outline_node_id,
            "word_count": count_words(draft.content or ""),
            "created_at": draft.created_at.isoformat() if draft.created_at else None,
        }
        for draft in drafts
    ]
    storage = storage_health(db, project, since=since) if project else {}
    data["storage_health"] = storage
    data["orphan_chapter_files"] = storage.get("orphan_chapter_files", [])
    if data["orphan_chapter_files"]:
        detail = "本机 CLI 已结束，但没有发现章节写入数据库；检测到未入库的章节镜像文件，请显式修复导入或重试。"
    elif data["drafts"]:
        detail = "本机 CLI 只保存了章节草稿，但没有调用 create_chapter 入库；请重试或用草稿创建章节。"
    else:
        detail = "本机 CLI 已结束，但没有发现章节草稿或章节入库记录。"
    data["repair_hint"] = "镜像目录不是权威数据源；修复时请显式调用 sync_project_files(direction='import', confirm_import_from_files=true)，或重新通过 create_chapter 入库。"
    return False, detail, data


async def start_local_cli_agent_run(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Start Claude/Codex/opencode as a Siming-managed CLI Agent worker."""
    task_type = str(args.get("task_type") or args.get("mode") or "general").strip().lower()
    if task_type not in {"general", "cataloging", "writing"}:
        task_type = "general"
    user_request = str(args.get("user_request") or args.get("request") or "").strip()
    provider = str(args.get("provider") or "").strip() or None
    result = start_local_cli_agent_worker(
        db,
        project_id,
        user_request=user_request,
        task_type=task_type,
        provider=provider,
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
    task_type = str(args.get("task_type") or args.get("mode") or "").strip().lower()
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

    if task_type == "writing":
        ok, detail, validation = _validate_writing_result(db, project_id, run, args)
        data["validation"] = validation
        return {
            "tool": "wait_local_cli_agent_run",
            "status": "ok" if ok else "error",
            "detail": detail,
            "data": data,
        }

    return {
        "tool": "wait_local_cli_agent_run",
        "status": "ok",
        "detail": "本机 CLI 运行完成",
        "data": data,
    }
