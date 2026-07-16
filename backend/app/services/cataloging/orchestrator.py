"""Sequential SSE orchestrator for project cataloging."""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator

from sqlalchemy.orm import Session

from ...ai.gateway import LLMGateway
from ...ai.local_cli_adapter import is_local_cli_provider
from ...database.models import CatalogingCandidate, CatalogingChapterRun, CatalogingJob, Chapter, Project
from ...database.session import SessionLocal
from ..content_store import ensure_project_folder, sync_chapter_to_file, sync_project_to_files
from .applier import apply_candidates_for_run, candidate_to_dict
from .candidate_store import try_create_candidate
from .constants import (
    CATALOGING_FACTS_PROMPT_LIMIT,
    CATALOGING_MAX_TOKENS,
    CATALOGING_STAGE_MAX_ATTEMPTS,
    CATALOGING_TIMEOUT_SECONDS,
)
from .context import build_full_cataloging_context, ordered_chapters
from .facts import facts_text, try_parse_fact_line
from .fact_store import clear_candidates_for_run, clear_facts_for_run, create_fact, load_facts_for_run
from .jsonl import clean_jsonl_text
from .job_control import refresh_job_progress
from .model_selection import cataloging_extra_body
from .staged_prompts import (
    CATALOGING_MERGED_SYSTEM_PROMPT,
    CATALOGING_RESOLUTION_SYSTEM_PROMPT,
    FACT_EXTRACTION_SYSTEM_PROMPT,
    build_fact_extraction_prompt,
    build_merged_cataloging_prompt,
    build_resolution_prompt,
)
from .targeted_context import build_targeted_context


LOCAL_FACT_EXTRACTION_SYSTEM_PROMPT = """你是作品建档事实抽取器。只读当前章节正文，输出 JSONL。
每行一个 JSON 对象，不要 Markdown，不要解释，不要输出数组。
必须先输出 1 行 chapter_overview；再按正文内容输出 1-6 行 outline_fact、character_fact、worldbuilding_fact、relationship_fact 或 identity_hint。
格式：{"fact_type":"chapter_overview","confidence":0.8,"evidence":"短依据","payload":{"summary":"本章发生了什么","key_events":["事件"]}}
如果信息不完整，也要根据正文输出最低可用事实；不确定内容写入 payload.uncertainty。"""


def sse_event(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def job_to_dict(job: CatalogingJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "project_id": job.project_id,
        "status": job.status,
        "execution_mode": job.execution_mode,
        "execution_backend": job.execution_backend or "internal_llm",
        "agent_run_id": job.agent_run_id,
        "operation_id": job.operation_id,
        "current_chapter_id": job.current_chapter_id,
        "last_completed_chapter_id": job.last_completed_chapter_id,
        "blocked_chapter_id": job.blocked_chapter_id,
        "context_integrity": job.context_integrity,
        "total_chapters": job.total_chapters or 0,
        "completed_chapters": job.completed_chapters or 0,
        "failed_chapters": job.failed_chapters or 0,
        "model": job.model,
        "effective_model": job.model,
        "model_source": job.model_source,
        "provider": job.provider,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def run_to_dict(run: CatalogingChapterRun) -> dict[str, Any]:
    chapter = run.chapter
    return {
        "id": run.id,
        "job_id": run.job_id,
        "chapter_id": run.chapter_id,
        "chapter_title": chapter.title if chapter else "",
        "status": run.status,
        "chapter_order": run.chapter_order,
        "error": run.error,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def _model_provider(model: str | None) -> str:
    try:
        provider, _ = LLMGateway.model_identity(model, {"moshu_task_type": "cataloging"})
        return provider
    except Exception:
        return (model or "").split(":", 1)[0].lower()


def _is_local_runtime_provider(provider: str) -> bool:
    return provider == "local_llama_cpp"


def _cataloging_pipeline_mode(provider: str | None = None) -> str:
    if _is_local_runtime_provider(provider or ""):
        return "staged"
    value = os.getenv("SIMING_CATALOGING_PIPELINE", "merged").strip().lower()
    if value in {"staged", "two_stage", "two-stage"}:
        return "staged"
    return "merged"


def _chapter_prompt_content(content: str, *, local_runtime: bool) -> str:
    text = (content or "").strip()
    limit = 7000 if local_runtime else 24000
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n【系统截断提示】章节过长，已先截取前半部分用于本地建档；请基于可见正文输出最低可用事实。"


def _fact_prompt_messages(
    *,
    chapter_title: str,
    chapter_content: str,
    chapter_file: str,
    model: str | None,
) -> list[dict[str, str]]:
    provider = _model_provider(model)
    local_runtime = _is_local_runtime_provider(provider)
    if chapter_file and is_local_cli_provider(provider):
        user_content = (
            f"当前章节标题：{chapter_title}\n\n"
            f"当前章节 UTF-8 镜像文件：{chapter_file}\n\n"
            "请完整读取附件中的章节正文，按系统规则输出事实 JSONL。"
            "先输出 chapter_overview，再输出角色、关系、世界观、大纲和身份线索事实。"
        )
    elif local_runtime:
        user_content = (
            f"当前章节标题：{chapter_title}\n\n"
            f"当前章节正文：\n{_chapter_prompt_content(chapter_content, local_runtime=True)}\n\n"
            "请输出 2-7 行事实 JSONL。"
        )
    else:
        user_content = build_fact_extraction_prompt(
            chapter_title,
            _chapter_prompt_content(chapter_content, local_runtime=False),
        )
    return [
        {"role": "system", "content": LOCAL_FACT_EXTRACTION_SYSTEM_PROMPT if local_runtime else FACT_EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _clean_model_json_text(text: str) -> str:
    value = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I)
    return clean_jsonl_text(value).strip()


def _facts_from_json_value(value: Any) -> list[dict[str, Any]]:
    items: list[Any]
    if isinstance(value, dict) and isinstance(value.get("facts"), list):
        items = value["facts"]
    elif isinstance(value, list):
        items = value
    else:
        items = [value]
    facts: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        parsed = try_parse_fact_line(json.dumps(item, ensure_ascii=False))
        fact = parsed.get("fact")
        if fact:
            facts.append(fact)
    return facts


def _salvage_facts_from_text(text: str) -> list[dict[str, Any]]:
    clean = _clean_model_json_text(text)
    if not clean:
        return []
    try:
        return _facts_from_json_value(json.loads(clean))
    except Exception:
        pass
    facts: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    index = 0
    while index < len(clean):
        brace = clean.find("{", index)
        bracket = clean.find("[", index)
        starts = [pos for pos in (brace, bracket) if pos >= 0]
        if not starts:
            break
        start = min(starts)
        try:
            value, offset = decoder.raw_decode(clean[start:])
        except Exception:
            index = start + 1
            continue
        facts.extend(_facts_from_json_value(value))
        index = start + max(offset, 1)
    return facts


def _fallback_summary_from_chapter(chapter_title: str, chapter_content: str) -> str:
    text = re.sub(r"\s+", " ", (chapter_content or "").strip())
    if not text:
        return f"{chapter_title}：章节正文为空，已创建最低可用章节概览。"
    sentences = re.split(r"(?<=[。！？!?])", text)
    summary = "".join(sentences[:3]).strip() or text[:240]
    if len(summary) > 260:
        summary = summary[:260].rstrip() + "..."
    return summary


def _fallback_facts_from_chapter(chapter_title: str, chapter_content: str) -> list[dict[str, Any]]:
    summary = _fallback_summary_from_chapter(chapter_title, chapter_content)
    return [
        {
            "fact_type": "chapter_overview",
            "confidence": 0.45,
            "evidence": "本地模型未输出可解析事实，系统按章节正文生成最低可用概览。",
            "payload": {"summary": summary, "key_events": [summary[:80]], "uncertainty": "自动兜底事实，建议人工复核"},
        },
        {
            "fact_type": "outline_fact",
            "confidence": 0.45,
            "evidence": "章节标题与正文摘要",
            "payload": {
                "title_hint": chapter_title or "未命名章节",
                "node_type": "chapter",
                "summary": summary,
                "characters": [],
                "hook": "自动兜底生成，后续可重新建档优化",
            },
        },
    ]


def create_cataloging_job(
    db: Session,
    project_id: str,
    execution_mode: str,
    model: str | None,
    chapter_ids: list[str] | None,
    execution_backend: str = "internal_llm",
    model_source: str | None = None,
    provider: str | None = None,
) -> CatalogingJob:
    from ..operation_runtime import ensure_operation

    chapters = ordered_chapters(db, project_id, chapter_ids)
    job = CatalogingJob(
        project_id=project_id,
        status="queued",
        execution_mode=execution_mode if execution_mode in {"auto", "manual"} else "auto",
        execution_backend=execution_backend,
        total_chapters=len(chapters),
        completed_chapters=0,
        failed_chapters=0,
        model=model,
        model_source=model_source,
        provider=provider,
    )
    db.add(job)
    db.flush()
    operation = ensure_operation(
        db,
        source_kind="cataloging",
        source_id=job.id,
        project_id=project_id,
        title=f"作品建档 · {len(chapters)} 章",
        status="queued",
        phase="queued",
        message="建档任务已创建，正在准备第一章",
        model_source=model,
        tool_mode=execution_backend,
        resume_url=f"/project/{project_id}?view=cataloging",
        can_pause=True,
        can_cancel=True,
        can_retry=True,
        progress_mode="determinate",
        progress_current=0,
        progress_total=len(chapters),
    )
    job.operation_id = operation.id
    for index, chapter in enumerate(chapters):
        db.add(CatalogingChapterRun(
            job_id=job.id,
            project_id=project_id,
            chapter_id=chapter.id,
            status="pending",
            chapter_order=index,
        ))
    db.commit()
    db.refresh(job)
    return job


async def stream_cataloging_job(project_id: str, job_id: str) -> AsyncGenerator[str, None]:
    from ..operation_runtime import finish_operation, heartbeat_loop, iterate_with_operation

    db = SessionLocal()
    heartbeat_task: asyncio.Task | None = None
    operation_id: str | None = None
    try:
        initial_job = _get_job(db, project_id, job_id)
        operation_id = initial_job.operation_id
        if operation_id:
            heartbeat_task = asyncio.create_task(heartbeat_loop(operation_id))
        yield sse_event({"type": "status", "message": "作品建档任务开始", "job_id": job_id})
        while True:
            job = _get_job(db, project_id, job_id)
            if job.status in {"completed", "failed", "cancelled", "paused", "paused_on_failure"}:
                yield sse_event({"type": "job", "job": job_to_dict(job)})
                yield "data: [DONE]\n\n"
                return

            run = _next_actionable_run(db, job)
            if not run:
                refresh_job_progress(db, job)
                job.status = "completed"
                job.completed_at = datetime.utcnow()
                job.updated_at = datetime.utcnow()
                db.commit()
                completed = int(job.completed_chapters or job.total_chapters or 0)
                finish_operation(
                    operation_id,
                    message=f"作品建档完成，共处理 {completed} 章",
                    outcome="completed_with_tools",
                    result={
                        "summary": f"作品建档完成，共处理 {completed} 章",
                        "completed": [f"{completed} 章已完成"],
                        "incomplete": [],
                    },
                    attention={},
                )
                yield sse_event({"type": "completed", "job": job_to_dict(job)})
                yield "data: [DONE]\n\n"
                return

            if run.status == "awaiting_confirmation":
                db.refresh(job)
                if job.execution_mode != "auto":
                    job.status = "waiting_confirmation"
                    job.blocked_chapter_id = run.chapter_id
                    refresh_job_progress(db, job)
                    db.commit()
                    yield sse_event({"type": "waiting_confirmation", "job": job_to_dict(job), "run": run_to_dict(run)})
                    yield "data: [DONE]\n\n"
                    return
                async for event in iterate_with_operation(operation_id, _apply_run(db, job, run)):
                    yield event
                continue

            if run.status == "failed":
                job.status = "paused_on_failure"
                job.blocked_chapter_id = run.chapter_id
                job.error = run.error
                refresh_job_progress(db, job)
                db.commit()
                yield sse_event({"type": "paused_on_failure", "job": job_to_dict(job), "run": run_to_dict(run), "error": run.error})
                yield "data: [DONE]\n\n"
                return

            async for event in iterate_with_operation(operation_id, _extract_run(db, job, run)):
                yield event

            db.refresh(job)
            db.refresh(run)
            if job.status in {"cancelled", "paused"}:
                yield sse_event({"type": job.status, "job": job_to_dict(job), "run": run_to_dict(run)})
                yield "data: [DONE]\n\n"
                return
            if run.status == "failed":
                continue
            if job.execution_mode == "manual":
                run.status = "awaiting_confirmation"
                job.status = "waiting_confirmation"
                job.blocked_chapter_id = run.chapter_id
                refresh_job_progress(db, job)
                db.commit()
                yield sse_event({"type": "waiting_confirmation", "job": job_to_dict(job), "run": run_to_dict(run)})
                yield "data: [DONE]\n\n"
                return

            async for event in iterate_with_operation(operation_id, _apply_run(db, job, run)):
                yield event
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        db.close()


async def _extract_run_merged(db: Session, job: CatalogingJob, run: CatalogingChapterRun) -> AsyncGenerator[str, None]:
    chapter = db.query(Chapter).filter(Chapter.id == run.chapter_id, Chapter.project_id == job.project_id).first()
    if not chapter:
        run.status = "failed"
        run.error = "章节不存在"
        db.commit()
        yield sse_event({"type": "chapter_failed", "run": run_to_dict(run), "error": run.error})
        return

    project = db.query(Project).filter(Project.id == job.project_id).first()
    project_folder = ""
    chapter_file = ""
    if project:
        folder = ensure_project_folder(db, project)
        path = folder / chapter.content_file_path if chapter.content_file_path else None
        if not path or not path.exists():
            sync_chapter_to_file(db, project, chapter, index=run.chapter_order + 1)
            path = folder / chapter.content_file_path if chapter.content_file_path else None
        project_folder = str(folder)
        if path and path.exists():
            chapter_file = str(path.resolve())
        db.commit()

    chapter_text = chapter.content or ""
    if not chapter_text and chapter_file:
        try:
            chapter_text = Path(chapter_file).read_text(encoding="utf-8")
        except Exception:
            chapter_text = ""

    run.status = "extracting"
    run.started_at = run.started_at or datetime.utcnow()
    job.status = "running"
    job.current_chapter_id = chapter.id
    job.blocked_chapter_id = None
    db.commit()
    yield sse_event({"type": "chapter_started", "job": job_to_dict(job), "run": run_to_dict(run)})

    clear_candidates_for_run(db, run)
    clear_facts_for_run(db, run)
    db.commit()

    provider = _model_provider(job.model)
    use_file_references = bool(chapter_file and is_local_cli_provider(provider))
    context_json = ""
    if not use_file_references:
        context_json = json.dumps(
            build_full_cataloging_context(db, job.project_id, chapter),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    raw_parts: list[str] = []
    bad_lines: list[str] = []
    candidate_count = 0
    has_summary = False
    yield sse_event({
        "type": "cataloging_stage",
        "message": "单阶段建档：读取章节与已有角色/世界观/大纲，直接生成候选卡片",
        "run": run_to_dict(run),
    })

    try:
        for attempt in range(1, CATALOGING_STAGE_MAX_ATTEMPTS + 1):
            candidate_buffer = ""
            bad_lines = []
            if attempt > 1:
                raw_parts.append(f"\n\n=== MERGED CATALOGING RETRY {attempt} ===\n")
            try:
                stream = LLMGateway.stream_chat_completion(
                    messages=[
                        {"role": "system", "content": CATALOGING_MERGED_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": build_merged_cataloging_prompt(
                                chapter_title=chapter.title,
                                chapter_content=_chapter_prompt_content(
                                    chapter_text,
                                    local_runtime=_is_local_runtime_provider(provider),
                                ),
                                context_json=context_json,
                                chapter_file=chapter_file,
                                project_folder=project_folder,
                                use_file_references=use_file_references,
                            ),
                        },
                    ],
                    model=job.model,
                    temperature=0.1,
                    max_tokens=CATALOGING_MAX_TOKENS,
                    timeout=CATALOGING_TIMEOUT_SECONDS,
                    retry=1,
                    extra_body=cataloging_extra_body(
                        job.model,
                        cwd=project_folder or None,
                        attachments=[chapter_file] if use_file_references else None,
                    ),
                )
                async for chunk in stream:
                    raw_parts.append(chunk)
                    candidate_buffer += chunk
                    lines = candidate_buffer.splitlines(keepends=True)
                    if lines and not lines[-1].endswith(("\n", "\r")):
                        candidate_buffer = lines.pop()
                    else:
                        candidate_buffer = ""
                    for line in lines:
                        created = try_create_candidate(db, job, run, line, candidate_count)
                        if created.get("bad_line"):
                            bad_lines.append(created["bad_line"])
                            yield sse_event({"type": "parse_warning", "run": run_to_dict(run), "line": created["bad_line"][:500], "error": created["error"]})
                        if created.get("skipped"):
                            reason = created.get("reason") or "候选缺少有效内容，已跳过"
                            yield sse_event({
                                "type": "candidate_skipped",
                                "run": run_to_dict(run),
                                "message": reason,
                                "reason": reason,
                            })
                        candidate = created.get("candidate")
                        if candidate:
                            candidate_count += 1
                            has_summary = has_summary or candidate.item_type == "chapter_summary"
                            db.commit()
                            yield sse_event({"type": "candidate_created", "candidate": candidate_to_dict(candidate), "run": run_to_dict(run)})
                tail = clean_jsonl_text(candidate_buffer)
                if tail:
                    created = try_create_candidate(db, job, run, tail, candidate_count)
                    if created.get("bad_line"):
                        bad_lines.append(created["bad_line"])
                    if created.get("skipped"):
                        reason = created.get("reason") or "候选缺少有效内容，已跳过"
                        yield sse_event({
                            "type": "candidate_skipped",
                            "run": run_to_dict(run),
                            "message": reason,
                            "reason": reason,
                        })
                    if created.get("candidate"):
                        candidate = created["candidate"]
                        candidate_count += 1
                        has_summary = has_summary or candidate.item_type == "chapter_summary"
                        db.commit()
                        yield sse_event({"type": "candidate_created", "candidate": candidate_to_dict(candidate), "run": run_to_dict(run)})

                retry_reason = ""
                if bad_lines:
                    retry_reason = f"{len(bad_lines)} 行 JSONL 解析失败"
                elif not has_summary:
                    retry_reason = "模型未输出 chapter_summary"
                if not retry_reason:
                    break
                if attempt >= CATALOGING_STAGE_MAX_ATTEMPTS:
                    break
                clear_candidates_for_run(db, run)
                db.commit()
                candidate_count = 0
                has_summary = False
                yield sse_event({
                    "type": "cataloging_retry",
                    "stage": "merged_candidate_generation",
                    "message": f"单阶段建档失败，正在自动重试 {attempt + 1}/{CATALOGING_STAGE_MAX_ATTEMPTS}",
                    "attempt": attempt + 1,
                    "max_attempts": CATALOGING_STAGE_MAX_ATTEMPTS,
                    "error": retry_reason,
                    "run": run_to_dict(run),
                })
            except Exception as exc:
                if attempt >= CATALOGING_STAGE_MAX_ATTEMPTS:
                    raise
                clear_candidates_for_run(db, run)
                db.commit()
                candidate_count = 0
                has_summary = False
                candidate_buffer = ""
                bad_lines = []
                raw_parts.append(f"\n[MERGED CATALOGING FAILED: {exc}]\n")
                yield sse_event({
                    "type": "cataloging_retry",
                    "stage": "merged_candidate_generation",
                    "message": f"单阶段建档失败，正在自动重试 {attempt + 1}/{CATALOGING_STAGE_MAX_ATTEMPTS}",
                    "attempt": attempt + 1,
                    "max_attempts": CATALOGING_STAGE_MAX_ATTEMPTS,
                    "error": str(exc),
                    "run": run_to_dict(run),
                })
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)
        run.raw_output = _merged_raw_output(raw_parts)
        job.status = "paused_on_failure"
        job.blocked_chapter_id = run.chapter_id
        job.error = run.error
        db.commit()
        yield sse_event({"type": "chapter_failed", "run": run_to_dict(run), "error": run.error})
        return

    run.raw_output = _merged_raw_output(raw_parts)
    if bad_lines:
        run.status = "failed"
        run.error = f"{len(bad_lines)} 行 JSONL 解析失败，已暂停在当前章节"
        job.status = "paused_on_failure"
        job.blocked_chapter_id = run.chapter_id
        job.error = run.error
        db.commit()
        yield sse_event({"type": "chapter_failed", "run": run_to_dict(run), "error": run.error, "bad_lines": bad_lines[:5]})
        return
    if not has_summary:
        run.status = "failed"
        run.error = "模型未输出 chapter_summary，已暂停在当前章节"
        job.status = "paused_on_failure"
        job.blocked_chapter_id = run.chapter_id
        job.error = run.error
        db.commit()
        yield sse_event({"type": "chapter_failed", "run": run_to_dict(run), "error": run.error})
        return

    run.status = "awaiting_confirmation"
    run.completed_at = datetime.utcnow()
    run.error = None
    db.commit()
    yield sse_event({"type": "chapter_extracted", "run": run_to_dict(run), "candidate_count": candidate_count})


async def _extract_run(db: Session, job: CatalogingJob, run: CatalogingChapterRun) -> AsyncGenerator[str, None]:
    provider = _model_provider(job.model)
    if _cataloging_pipeline_mode(provider) == "merged" and run.status != "facts_saved":
        async for event in _extract_run_merged(db, job, run):
            yield event
        return

    chapter = db.query(Chapter).filter(Chapter.id == run.chapter_id, Chapter.project_id == job.project_id).first()
    if not chapter:
        run.status = "failed"
        run.error = "章节不存在"
        db.commit()
        yield sse_event({"type": "chapter_failed", "run": run_to_dict(run), "error": run.error})
        return
    project = db.query(Project).filter(Project.id == job.project_id).first()
    project_folder = ""
    chapter_file = ""
    if project:
        folder = ensure_project_folder(db, project)
        path = folder / chapter.content_file_path if chapter.content_file_path else None
        if not path or not path.exists():
            sync_chapter_to_file(db, project, chapter, index=run.chapter_order + 1)
            path = folder / chapter.content_file_path if chapter.content_file_path else None
        project_folder = str(folder)
        if path and path.exists():
            chapter_file = str(path.resolve())
        db.commit()
    chapter_text = chapter.content or ""
    if not chapter_text and chapter_file:
        try:
            chapter_text = Path(chapter_file).read_text(encoding="utf-8")
        except Exception:
            chapter_text = ""

    run.status = "extracting"
    run.started_at = run.started_at or datetime.utcnow()
    job.status = "running"
    job.current_chapter_id = chapter.id
    job.blocked_chapter_id = None
    db.commit()
    yield sse_event({"type": "chapter_started", "job": job_to_dict(job), "run": run_to_dict(run)})

    raw_fact_parts: list[str] = []
    raw_candidate_parts: list[str] = []
    facts: list[dict[str, Any]] = []
    fact_buffer = ""
    fact_bad_lines: list[str] = []
    candidate_count = db.query(CatalogingCandidate).filter(CatalogingCandidate.chapter_run_id == run.id).count()
    has_summary = db.query(CatalogingCandidate).filter(
        CatalogingCandidate.chapter_run_id == run.id,
        CatalogingCandidate.item_type == "chapter_summary",
    ).first() is not None
    local_runtime = _is_local_runtime_provider(provider)

    try:
        facts = load_facts_for_run(db, run)
        if facts:
            yield sse_event({
                "type": "cataloging_stage",
                "message": f"第一阶段：复用已保存事实 {len(facts)} 条",
                "run": run_to_dict(run),
            })
        else:
            yield sse_event({"type": "cataloging_stage", "message": "第一阶段：裸读章节，抽取事实线索", "run": run_to_dict(run)})
            for attempt in range(1, CATALOGING_STAGE_MAX_ATTEMPTS + 1):
                fact_buffer = ""
                fact_bad_lines = []
                attempt_fact_parts: list[str] = []
                if attempt > 1:
                    facts = []
                    raw_fact_parts.append(f"\n\n=== FACT EXTRACTION RETRY {attempt} ===\n")
                try:
                    fact_stream = LLMGateway.stream_chat_completion(
                        messages=_fact_prompt_messages(
                            chapter_title=chapter.title,
                            chapter_content=chapter_text,
                            chapter_file=chapter_file,
                            model=job.model,
                        ),
                        model=job.model,
                        temperature=0.1,
                        max_tokens=1600 if local_runtime else min(CATALOGING_MAX_TOKENS, 12000),
                        timeout=CATALOGING_TIMEOUT_SECONDS,
                        retry=1,
                        extra_body=cataloging_extra_body(
                            job.model,
                            cwd=project_folder or None,
                            attachments=[chapter_file] if chapter_file and is_local_cli_provider(provider) else None,
                        ),
                    )
                    async for chunk in fact_stream:
                        raw_fact_parts.append(chunk)
                        attempt_fact_parts.append(chunk)
                        fact_buffer += chunk
                        lines = fact_buffer.splitlines(keepends=True)
                        if lines and not lines[-1].endswith(("\n", "\r")):
                            fact_buffer = lines.pop()
                        else:
                            fact_buffer = ""
                        for line in lines:
                            parsed = try_parse_fact_line(line)
                            if parsed.get("bad_line"):
                                fact_bad_lines.append(parsed["bad_line"])
                                yield sse_event({"type": "fact_parse_warning", "run": run_to_dict(run), "line": parsed["bad_line"][:500], "error": parsed["error"]})
                            fact = parsed.get("fact")
                            if fact:
                                facts.append(fact)
                                create_fact(db, job, run, fact, len(facts) - 1)
                                db.commit()
                                yield sse_event({
                                    "type": "fact_extracted",
                                    "message": f"已抽取事实: {fact.get('fact_type')}",
                                    "fact": fact,
                                    "run": run_to_dict(run),
                                })
                    tail = clean_jsonl_text(fact_buffer)
                    if tail:
                        parsed = try_parse_fact_line(tail)
                        if parsed.get("bad_line"):
                            fact_bad_lines.append(parsed["bad_line"])
                        if parsed.get("fact"):
                            facts.append(parsed["fact"])
                            create_fact(db, job, run, parsed["fact"], len(facts) - 1)
                            db.commit()
                    if not facts:
                        salvaged = _salvage_facts_from_text("".join(attempt_fact_parts))
                        for fact in salvaged:
                            facts.append(fact)
                            create_fact(db, job, run, fact, len(facts) - 1)
                            db.commit()
                            yield sse_event({
                                "type": "fact_extracted",
                                "message": f"已修复抽取事实: {fact.get('fact_type')}",
                                "fact": fact,
                                "run": run_to_dict(run),
                            })
                    if not facts and local_runtime and attempt >= CATALOGING_STAGE_MAX_ATTEMPTS:
                        fallback_facts = _fallback_facts_from_chapter(chapter.title, chapter_text)
                        for fact in fallback_facts:
                            facts.append(fact)
                            create_fact(db, job, run, fact, len(facts) - 1)
                            db.commit()
                            yield sse_event({
                                "type": "fact_extracted",
                                "message": f"已生成兜底事实: {fact.get('fact_type')}",
                                "fact": fact,
                                "run": run_to_dict(run),
                            })
                        yield sse_event({
                            "type": "cataloging_warning",
                            "stage": "fact_extraction",
                            "message": "本地模型未输出可解析事实，已生成最低可用事实继续建档；建议完成后人工复核。",
                            "run": run_to_dict(run),
                        })
                    if not facts:
                        raise ValueError("模型未输出可用事实")
                    break
                except Exception as exc:
                    if attempt >= CATALOGING_STAGE_MAX_ATTEMPTS and local_runtime:
                        clear_facts_for_run(db, run)
                        db.commit()
                        facts = []
                        fallback_facts = _fallback_facts_from_chapter(chapter.title, chapter_text)
                        for fact in fallback_facts:
                            facts.append(fact)
                            create_fact(db, job, run, fact, len(facts) - 1)
                            db.commit()
                            yield sse_event({
                                "type": "fact_extracted",
                                "message": f"已生成兜底事实: {fact.get('fact_type')}",
                                "fact": fact,
                                "run": run_to_dict(run),
                            })
                        yield sse_event({
                            "type": "cataloging_warning",
                            "stage": "fact_extraction",
                            "message": f"第一阶段本地模型失败（{exc}），已用最低可用事实继续建档；建议完成后人工复核。",
                            "run": run_to_dict(run),
                        })
                        break
                    if attempt >= CATALOGING_STAGE_MAX_ATTEMPTS:
                        raise
                    clear_facts_for_run(db, run)
                    db.commit()
                    facts = []
                    fact_buffer = ""
                    fact_bad_lines = []
                    raw_fact_parts.append(f"\n[FACT EXTRACTION FAILED: {exc}]\n")
                    yield sse_event({
                        "type": "cataloging_retry",
                        "stage": "fact_extraction",
                        "message": f"第一阶段失败，正在自动重试 {attempt + 1}/{CATALOGING_STAGE_MAX_ATTEMPTS}",
                        "attempt": attempt + 1,
                        "max_attempts": CATALOGING_STAGE_MAX_ATTEMPTS,
                        "error": str(exc),
                        "run": run_to_dict(run),
                    })

        if not facts:
            raise ValueError("模型未输出可用事实，已暂停在当前章节")

        targeted_context = build_targeted_context(db, job.project_id, chapter, facts)
        if local_runtime:
            targeted_context = _compact_local_runtime_context(targeted_context)
        yield sse_event({
            "type": "cataloging_stage",
            "message": (
                "第二阶段：已按事实检索相关卡片，"
                f"角色 {len(targeted_context['relevant_characters'])} 个，"
                f"世界观 {len(targeted_context['relevant_worldbuilding'])} 条"
            ),
            "run": run_to_dict(run),
        })

        bad_lines: list[str] = []
        for attempt in range(1, CATALOGING_STAGE_MAX_ATTEMPTS + 1):
            candidate_buffer = ""
            bad_lines = []
            if attempt > 1:
                raw_candidate_parts.append(f"\n\n=== CANDIDATE RESOLUTION RETRY {attempt} ===\n")
            try:
                candidate_stream = LLMGateway.stream_chat_completion(
                    messages=[
                        {"role": "system", "content": CATALOGING_RESOLUTION_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": build_resolution_prompt(
                                facts_text(facts, limit=CATALOGING_FACTS_PROMPT_LIMIT),
                                json.dumps(targeted_context, ensure_ascii=False, separators=(",", ":")),
                                chapter.title,
                            ),
                        },
                    ],
                    model=job.model,
                    temperature=0.1,
                    max_tokens=4096 if local_runtime else CATALOGING_MAX_TOKENS,
                    timeout=CATALOGING_TIMEOUT_SECONDS,
                    retry=1,
                    extra_body=cataloging_extra_body(
                        job.model,
                        cwd=project_folder or None,
                    ),
                )
                async for chunk in candidate_stream:
                    raw_candidate_parts.append(chunk)
                    candidate_buffer += chunk
                    lines = candidate_buffer.splitlines(keepends=True)
                    if lines and not lines[-1].endswith(("\n", "\r")):
                        candidate_buffer = lines.pop()
                    else:
                        candidate_buffer = ""
                    for line in lines:
                        created = try_create_candidate(db, job, run, line, candidate_count)
                        if created.get("bad_line"):
                            bad_lines.append(created["bad_line"])
                            yield sse_event({"type": "parse_warning", "run": run_to_dict(run), "line": created["bad_line"][:500], "error": created["error"]})
                        if created.get("skipped"):
                            reason = created.get("reason") or "候选缺少有效内容，已跳过"
                            yield sse_event({
                                "type": "candidate_skipped",
                                "run": run_to_dict(run),
                                "message": reason,
                                "reason": reason,
                            })
                        candidate = created.get("candidate")
                        if candidate:
                            candidate_count += 1
                            has_summary = has_summary or candidate.item_type == "chapter_summary"
                            db.commit()
                            yield sse_event({"type": "candidate_created", "candidate": candidate_to_dict(candidate), "run": run_to_dict(run)})
                tail = clean_jsonl_text(candidate_buffer)
                if tail:
                    created = try_create_candidate(db, job, run, tail, candidate_count)
                    if created.get("bad_line"):
                        bad_lines.append(created["bad_line"])
                    if created.get("skipped"):
                        reason = created.get("reason") or "候选缺少有效内容，已跳过"
                        yield sse_event({
                            "type": "candidate_skipped",
                            "run": run_to_dict(run),
                            "message": reason,
                            "reason": reason,
                        })
                    if created.get("candidate"):
                        candidate = created["candidate"]
                        candidate_count += 1
                        has_summary = has_summary or candidate.item_type == "chapter_summary"
                        db.commit()
                        yield sse_event({"type": "candidate_created", "candidate": candidate_to_dict(candidate), "run": run_to_dict(run)})
                retry_reason = ""
                if bad_lines:
                    retry_reason = f"{len(bad_lines)} 行 JSONL 解析失败"
                elif not has_summary:
                    retry_reason = "模型未输出 chapter_summary"
                if not retry_reason:
                    break
                if attempt >= CATALOGING_STAGE_MAX_ATTEMPTS:
                    if local_runtime:
                        yield sse_event({
                            "type": "cataloging_warning",
                            "stage": "candidate_resolution",
                            "message": f"第二阶段本地模型未输出完整候选（{retry_reason}），已暂停当前章节；不会用模板生成候选。请换更强模型、外部 CLI，或重跑第二阶段。",
                            "run": run_to_dict(run),
                        })
                    break
                clear_candidates_for_run(db, run)
                db.commit()
                candidate_count = 0
                has_summary = False
                yield sse_event({
                    "type": "cataloging_retry",
                    "stage": "candidate_resolution",
                    "message": f"第二阶段失败，正在自动重试 {attempt + 1}/{CATALOGING_STAGE_MAX_ATTEMPTS}",
                    "attempt": attempt + 1,
                    "max_attempts": CATALOGING_STAGE_MAX_ATTEMPTS,
                    "error": retry_reason,
                    "run": run_to_dict(run),
                })
            except Exception as exc:
                if attempt >= CATALOGING_STAGE_MAX_ATTEMPTS and local_runtime:
                    yield sse_event({
                        "type": "cataloging_warning",
                        "stage": "candidate_resolution",
                        "message": f"第二阶段本地模型失败（{exc}），已暂停当前章节；不会用模板生成候选。请换更强模型、外部 CLI，或重跑第二阶段。",
                        "run": run_to_dict(run),
                    })
                    raise ValueError(f"第二阶段本地模型失败（{exc}），已暂停当前章节")
                if attempt >= CATALOGING_STAGE_MAX_ATTEMPTS:
                    raise
                clear_candidates_for_run(db, run)
                db.commit()
                candidate_count = 0
                has_summary = False
                candidate_buffer = ""
                bad_lines = []
                raw_candidate_parts.append(f"\n[CANDIDATE RESOLUTION FAILED: {exc}]\n")
                yield sse_event({
                    "type": "cataloging_retry",
                    "stage": "candidate_resolution",
                    "message": f"第二阶段失败，正在自动重试 {attempt + 1}/{CATALOGING_STAGE_MAX_ATTEMPTS}",
                    "attempt": attempt + 1,
                    "max_attempts": CATALOGING_STAGE_MAX_ATTEMPTS,
                    "error": str(exc),
                    "run": run_to_dict(run),
                })
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)
        run.raw_output = _combined_raw_output(raw_fact_parts, raw_candidate_parts)
        job.status = "paused_on_failure"
        job.blocked_chapter_id = run.chapter_id
        job.error = run.error
        db.commit()
        yield sse_event({"type": "chapter_failed", "run": run_to_dict(run), "error": run.error})
        return

    run.raw_output = _combined_raw_output(raw_fact_parts, raw_candidate_parts)
    if fact_bad_lines:
        yield sse_event({
            "type": "parse_warning",
            "run": run_to_dict(run),
            "error": f"第一阶段有 {len(fact_bad_lines)} 行事实未能解析，已用其余事实继续",
        })
    if bad_lines:
        run.status = "failed"
        run.error = f"{len(bad_lines)} 行 JSONL 解析失败，已暂停在当前章节"
        job.status = "paused_on_failure"
        job.blocked_chapter_id = run.chapter_id
        job.error = run.error
        db.commit()
        yield sse_event({"type": "chapter_failed", "run": run_to_dict(run), "error": run.error, "bad_lines": bad_lines[:5]})
        return
    if not has_summary:
        run.status = "failed"
        run.error = "模型未输出 chapter_summary，已暂停在当前章节"
        job.status = "paused_on_failure"
        job.blocked_chapter_id = run.chapter_id
        job.error = run.error
        db.commit()
        yield sse_event({"type": "chapter_failed", "run": run_to_dict(run), "error": run.error})
        return

    run.status = "awaiting_confirmation"
    run.completed_at = datetime.utcnow()
    run.error = None
    db.commit()
    yield sse_event({"type": "chapter_extracted", "run": run_to_dict(run), "candidate_count": candidate_count})


def _combined_raw_output(raw_fact_parts: list[str], raw_candidate_parts: list[str]) -> str:
    value = (
        "=== FACT EXTRACTION ===\n"
        + "".join(raw_fact_parts)
        + "\n\n=== CANDIDATE RESOLUTION ===\n"
        + "".join(raw_candidate_parts)
    )
    return value[-60000:]


def _merged_raw_output(raw_parts: list[str]) -> str:
    value = "=== MERGED CATALOGING ===\n" + "".join(raw_parts)
    return value[-60000:]


def _compact_local_runtime_context(context: dict[str, Any]) -> dict[str, Any]:
    """Keep staged resolution prompts small enough for managed local models."""
    def clip(value: Any, limit: int) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        return text[:limit]

    def aliases(value: Any, limit: int = 6) -> list[Any]:
        if not isinstance(value, list):
            return []
        compacted = []
        for item in value[:limit]:
            if isinstance(item, dict):
                compacted.append({
                    "alias": clip(item.get("alias"), 80),
                    "alias_type": clip(item.get("alias_type"), 40),
                })
            else:
                compacted.append(clip(item, 80))
        return [item for item in compacted if item]

    def character(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "aliases": aliases(item.get("aliases")),
            "role_type": item.get("role_type"),
            "age": item.get("age"),
            "appearance": clip(item.get("appearance"), 180),
            "personality": clip(item.get("personality"), 180),
            "background": clip(item.get("background"), 260),
            "abilities": (item.get("abilities") or [])[:8] if isinstance(item.get("abilities"), list) else [],
            "life_status": item.get("life_status"),
            "current_location": clip(item.get("current_location"), 120),
            "realm_or_level": clip(item.get("realm_or_level"), 120),
            "physical_state": clip(item.get("physical_state"), 140),
            "mental_state": clip(item.get("mental_state"), 140),
            "current_goal": clip(item.get("current_goal"), 160),
            "active_conflict": clip(item.get("active_conflict"), 160),
            "abilities_state": clip(item.get("abilities_state"), 160),
            "items_or_assets": clip(item.get("items_or_assets"), 160),
            "recent_timeline": [
                {
                    "event_type": event.get("event_type"),
                    "event_description": clip(event.get("event_description"), 140),
                }
                for event in (item.get("recent_timeline") or [])[:2]
                if isinstance(event, dict)
            ],
        }

    def worldbuilding(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("id"),
            "dimension": item.get("dimension"),
            "title": item.get("title"),
            "status": item.get("status"),
            "content": clip(item.get("content"), 320),
            "recent_timeline": [
                {
                    "event_type": event.get("event_type"),
                    "event_description": clip(event.get("event_description"), 140),
                }
                for event in (item.get("recent_timeline") or [])[:2]
                if isinstance(event, dict)
            ],
        }

    lookup_terms = context.get("lookup_terms") or {}
    return {
        "current_chapter": context.get("current_chapter"),
        "recent_chapter_summaries": [
            {
                "title": item.get("title"),
                "summary": clip(item.get("summary"), 280),
                "key_events": (item.get("key_events") or [])[:4] if isinstance(item.get("key_events"), list) else [],
            }
            for item in (context.get("recent_chapter_summaries") or [])[-4:]
            if isinstance(item, dict)
        ],
        "character_name_index": [
            {
                "name": item.get("name"),
                "age": item.get("age"),
                "role_type": item.get("role_type"),
                "life_status": item.get("life_status"),
                "aliases": aliases(item.get("aliases"), 4),
            }
            for item in (context.get("character_name_index") or [])[:80]
            if isinstance(item, dict)
        ],
        "relevant_characters": [
            character(item)
            for item in (context.get("relevant_characters") or [])[:6]
            if isinstance(item, dict)
        ],
        "relevant_relationships": [
            {
                "source_name": item.get("source_name"),
                "target_name": item.get("target_name"),
                "relationship_type": item.get("relationship_type"),
                "description": clip(item.get("description"), 140),
            }
            for item in (context.get("relevant_relationships") or [])[:16]
            if isinstance(item, dict)
        ],
        "worldbuilding_title_index": [
            {
                "dimension": item.get("dimension"),
                "title": item.get("title"),
            }
            for item in (context.get("worldbuilding_title_index") or [])[:100]
            if isinstance(item, dict)
        ],
        "relevant_worldbuilding": [
            worldbuilding(item)
            for item in (context.get("relevant_worldbuilding") or [])[:6]
            if isinstance(item, dict)
        ],
        "nearby_outline_nodes": [
            {
                "title": item.get("title"),
                "node_type": item.get("node_type"),
                "summary": clip(item.get("summary"), 180),
                "actual_summary": clip(item.get("actual_summary"), 180),
                "planned_summary": clip(item.get("planned_summary"), 180),
            }
            for item in (context.get("nearby_outline_nodes") or [])[:18]
            if isinstance(item, dict)
        ],
        "lookup_terms": {
            "names": lookup_terms.get("names", [])[:40],
            "titles": lookup_terms.get("titles", [])[:40],
            "keywords": lookup_terms.get("keywords", [])[:40],
        },
    }


async def _apply_run(db: Session, job: CatalogingJob, run: CatalogingChapterRun) -> AsyncGenerator[str, None]:
    run.status = "applying"
    job.status = "running"
    db.commit()
    yield sse_event({"type": "chapter_applying", "job": job_to_dict(job), "run": run_to_dict(run)})
    events = apply_candidates_for_run(db, job, run)
    has_failed = any(event["type"] == "candidate_apply_failed" for event in events)
    for event in events:
        db.commit()
        yield sse_event(event)
    run.status = "completed_with_warnings" if has_failed else "completed"
    run.completed_at = datetime.utcnow()
    job.last_completed_chapter_id = run.chapter_id
    job.current_chapter_id = None
    job.blocked_chapter_id = None
    job.error = None
    refresh_job_progress(db, job)
    sync_project_to_files(db, job.project_id)
    db.commit()
    yield sse_event({"type": "chapter_completed", "job": job_to_dict(job), "run": run_to_dict(run), "warnings": has_failed})


def _next_actionable_run(db: Session, job: CatalogingJob) -> CatalogingChapterRun | None:
    return (
        db.query(CatalogingChapterRun)
        .filter(CatalogingChapterRun.job_id == job.id)
        .filter(CatalogingChapterRun.status.notin_(["completed", "completed_with_warnings", "skipped_by_user"]))
        .order_by(CatalogingChapterRun.chapter_order.asc())
        .first()
    )


def _get_job(db: Session, project_id: str, job_id: str) -> CatalogingJob:
    job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id, CatalogingJob.project_id == project_id).first()
    if not job:
        raise ValueError("作品建档任务不存在")
    return job
