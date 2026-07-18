"""SSE streaming orchestrators for the deconstruct map-reduce pipeline."""

from app.architecture.uow import commit_session
import asyncio
import json
import traceback
from datetime import datetime
from typing import AsyncGenerator, Optional

from ...core.db_helpers import get_project_or_404
from ...core.exceptions import ValidationError
from ...database.models import DeconstructionReport
from ...database.session import SessionLocal
from ...prompts.deconstruct import REDUCE_SECTION_LABELS
from ...schemas.deconstruct import DeconstructRequest
from .constants import MAX_MAP_CONCURRENCY, MAP_TIMEOUT_SECONDS
from .map_reduce import reduce_section, stream_map_chunk
from .model_selection import (
    analysis_mode_from_payload,
    limits_info_for,
    map_concurrency_from_payload,
    models_from_payload,
    module_options_from_payload,
)
from .pipeline import (
    build_golden_three_source,
    chapter_aware_chunks,
    default_reduce_result,
    merge_reduce_section,
    reduce_section_keys,
    resolve_report_chunks,
    split_text,
    summarize_chunk_result,
)
from .report_store import append_log, get_report_or_404, load_report_data, report_payload
from ..operation_runtime import (
    activate_operation,
    fail_operation,
    finish_operation,
    heartbeat_loop,
    record_operation_signal,
)


def sse_event(event_type: str, data: dict | list) -> str:
    """Format a single SSE event with type field embedded in the JSON."""
    if isinstance(data, dict):
        payload = json.dumps({"type": event_type, **data}, ensure_ascii=False, separators=(",", ":"))
    else:
        payload = json.dumps({"type": event_type, "items": data}, ensure_ascii=False, separators=(",", ":"))
    return f"data: {payload}\n\n"


async def stream_deconstruct(
    project_id: str,
    title: str,
    chunks: list[str],
    total_words: int,
    map_model: Optional[str],
    reduce_model: Optional[str],
    options: dict,
    include_rhythm: bool,
    include_patterns: bool,
    selected_chapter_ids: list[str],
    map_concurrency: int,
    golden_text: str = "",
    golden_chapter_ids: Optional[list[str]] = None,
) -> AsyncGenerator[str, None]:
    """Run the full Map-Reduce pipeline and yield SSE events."""
    db = SessionLocal()
    report = None
    try:
        chunk_results_init = [
            {"index": i, "status": "pending", "summary": "", "characters": [], "events": [], "highlights": []}
            for i in range(len(chunks))
        ]
        report_data_init = {
            "title": title,
            "status": "queued",
            "phase": "queued",
            "total_chunks": len(chunks),
            "completed_chunks": 0,
            "failed_chunks": 0,
            "elapsed_seconds": 0,
            "avg_seconds_per_chunk": 0,
            "estimated_remaining_seconds": 0,
            "total_words": total_words,
            "selected_chapter_ids": selected_chapter_ids,
            "model": reduce_model or map_model,
            "map_model": map_model,
            "reduce_model": reduce_model,
            "model_limits": limits_info_for(reduce_model or map_model),
            "analysis_mode": options.get("analysis_mode", "fast"),
            "options": options,
            "map_concurrency": map_concurrency,
            "golden_chapter_ids": golden_chapter_ids or [],
            "source_chunks": chunks,
            "chunk_results": chunk_results_init,
            "created_at": datetime.utcnow().isoformat(),
            "logs": [],
        }
        append_log(report_data_init, f"已创建拆书任务，共 {len(chunks)} 个分析块")
        report = DeconstructionReport(
            project_id=project_id,
            source_filename=title,
            status="processing",
            report_data=json.dumps(report_data_init, ensure_ascii=False),
        )
        db.add(report)
        commit_session(db)
        db.refresh(report)

        yield sse_event("init", {
            "report_id": report.id,
            "total_chunks": len(chunks),
            "total_words": total_words,
            "title": title,
            "map_concurrency": map_concurrency,
            "map_model": map_model,
            "reduce_model": reduce_model,
            "model_limits": limits_info_for(reduce_model or map_model),
            "analysis_mode": options.get("analysis_mode", "fast"),
        })

        # ── Map Phase ──────────────────────────────────────────────
        yield sse_event("map_start", {"total_chunks": len(chunks), "map_concurrency": map_concurrency})

        semaphore = asyncio.Semaphore(map_concurrency)
        started_at = datetime.utcnow()
        map_results: list[dict | None] = [None] * len(chunks)
        completed = 0
        failed = 0
        event_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()

        async def _process_chunk(index: int, chunk: str) -> None:
            async with semaphore:
                await event_queue.put(("map_chunk_start", {"index": index}))
                try:
                    async for event in stream_map_chunk(chunk, index, map_model, options):
                        if event["type"] == "token":
                            await event_queue.put(("map_token", {
                                "index": index,
                                "content": event.get("content", ""),
                            }))
                        elif event["type"] == "retry":
                            await event_queue.put(("map_retry", {
                                "index": index,
                                "attempt": event.get("attempt"),
                                "error": event.get("error"),
                            }))
                        elif event["type"] == "repair_start":
                            await event_queue.put(("map_repair_start", {
                                "index": index,
                                "error": event.get("error"),
                            }))
                        elif event["type"] == "repair_done":
                            await event_queue.put(("map_repair_done", {
                                "index": index,
                                "status": event.get("status"),
                                "error": event.get("error"),
                            }))
                        elif event["type"] == "result":
                            await event_queue.put(("map_result", {
                                "index": index,
                                "result": event.get("result") or {"_error": "missing_result"},
                            }))
                            return
                except Exception as exc:
                    await event_queue.put(("map_result", {
                        "index": index,
                        "result": {"_raw": str(exc), "_error": "llm_failed"},
                    }))

        tasks = [asyncio.create_task(_process_chunk(i, c)) for i, c in enumerate(chunks)]

        while completed < len(chunks):
            event_type, event_data = await event_queue.get()

            if event_type in {"map_chunk_start", "map_token", "map_retry", "map_repair_start", "map_repair_done"}:
                yield sse_event(event_type, event_data)
                continue

            index = int(event_data.get("index", 0))
            result = event_data.get("result") if isinstance(event_data.get("result"), dict) else {"_error": "missing_result"}
            map_results[index] = result
            completed += 1
            if result.get("_error"):
                failed += 1

            elapsed = max((datetime.utcnow() - started_at).total_seconds(), 0.1)
            avg = elapsed / completed
            remaining = avg * (len(chunks) - completed)

            summary = summarize_chunk_result(result, index)
            yield sse_event("map_chunk", summary)
            yield sse_event("map_progress", {
                "completed": completed,
                "failed": failed,
                "total": len(chunks),
                "elapsed_seconds": round(elapsed, 1),
                "avg_seconds_per_chunk": round(avg, 1),
                "estimated_remaining_seconds": round(remaining, 1),
            })

            progress_data = json.loads(report.report_data or "{}")
            c_results = progress_data.get("chunk_results") or []
            while len(c_results) < len(chunks):
                c_results.append({"index": len(c_results), "status": "pending"})
            c_results[index] = summary
            progress_data.update({
                "status": "processing",
                "phase": "map",
                "chunk_results": c_results,
                "completed_chunks": completed,
                "failed_chunks": failed,
                "elapsed_seconds": round(elapsed, 1),
                "avg_seconds_per_chunk": round(avg, 1),
                "estimated_remaining_seconds": round(remaining, 1),
            })
            level = "warning" if result.get("_error") else "info"
            msg = f"第 {index + 1}/{len(chunks)} 块分析完成"
            if result.get("_error"):
                msg = f"{msg}：{result.get('_error')}"
            append_log(progress_data, msg, level)
            report.report_data = json.dumps(progress_data, ensure_ascii=False)
            commit_session(db)

        await asyncio.gather(*tasks, return_exceptions=True)

        final_map = [item or {"_error": "missing_result"} for item in map_results]
        yield sse_event("map_complete", {
            "completed": completed,
            "failed": failed,
            "elapsed_seconds": round((datetime.utcnow() - started_at).total_seconds(), 1),
        })

        # ── Reduce Phase ───────────────────────────────────────────
        yield sse_event("reduce_start", {})
        parsed = default_reduce_result(options)
        for section_key in reduce_section_keys(options):
            label = REDUCE_SECTION_LABELS.get(section_key, section_key)
            yield sse_event("reduce_section_start", {"section": section_key, "label": label})
            section_data = await reduce_section(section_key, final_map, title, total_words, reduce_model, options, golden_text)
            if section_data.get("_error"):
                parsed["reduce_errors"][section_key] = section_data.get("_error")
                yield sse_event("reduce_section_error", {
                    "section": section_key,
                    "label": label,
                    "error": section_data.get("_error"),
                })
            else:
                merge_reduce_section(parsed, section_key, section_data, options)
                parsed["reduce_sections"].append(section_key)
                yield sse_event("reduce_section_complete", {"section": section_key, "label": label})

        elapsed_total = round((datetime.utcnow() - started_at).total_seconds(), 1)
        chunk_summaries = [
            summarize_chunk_result(r, i) if r else {"index": i, "status": "missing"}
            for i, r in enumerate(final_map)
        ]

        result = {
            "id": report.id,
            "title": title,
            "status": "completed",
            "phase": "completed",
            "golden_three": parsed.get("golden_three") if options.get("golden_three") else None,
            "structure": parsed.get("structure", {}),
            "plot_nodes": parsed.get("plot_nodes", []),
            "characters": parsed.get("characters", []) if options.get("characters") else [],
            "worldbuilding_entries": parsed.get("worldbuilding_entries", []) if options.get("worldbuilding") else [],
            "highlights": parsed.get("highlights", []),
            "rhythm_curve": parsed.get("rhythm_curve") if include_rhythm else None,
            "patterns": parsed.get("patterns") if include_patterns else None,
            "reduce_sections": parsed.get("reduce_sections", []),
            "reduce_errors": parsed.get("reduce_errors", {}),
            "raw_map_results": final_map,
            "chunk_results": chunk_summaries,
            "logs": progress_data.get("logs", []),
            "total_chunks": len(chunks),
            "completed_chunks": completed,
            "failed_chunks": failed,
            "elapsed_seconds": elapsed_total,
            "avg_seconds_per_chunk": round(elapsed_total / max(completed, 1), 1),
            "estimated_remaining_seconds": 0,
            "total_words": total_words,
            "options": options,
            "map_model": map_model,
            "reduce_model": reduce_model,
            "model_limits": limits_info_for(reduce_model or map_model),
            "analysis_mode": options.get("analysis_mode", "fast"),
            "map_concurrency": map_concurrency,
            "selected_chapter_ids": selected_chapter_ids,
            "golden_chapter_ids": golden_chapter_ids or [],
            "source_chunks": chunks,
            "created_at": report.created_at.isoformat() if report.created_at else datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
        }
        append_log(result, "自动合并完成，拆书报告已生成")
        yield sse_event("reduce_complete", result)
        yield sse_event("done", {})

        report.status = "completed"
        report.report_data = json.dumps(result, ensure_ascii=False)
        commit_session(db)

    except asyncio.CancelledError:
        if report:
            try:
                progress_data = json.loads(report.report_data or "{}")
                progress_data["status"] = "processing"
                progress_data["phase"] = "cancelled"
                append_log(progress_data, "客户端断开连接，任务中断")
                report.report_data = json.dumps(progress_data, ensure_ascii=False)
                commit_session(db)
            except Exception:
                pass
    except Exception as exc:
        traceback.print_exc()
        yield sse_event("error", {"message": str(exc)})
        if report:
            try:
                progress_data = json.loads(report.report_data or "{}")
                progress_data.update({"status": "failed", "phase": "failed", "error": str(exc)})
                append_log(progress_data, f"拆书任务失败：{exc}", "error")
                report.status = "failed"
                report.report_data = json.dumps(progress_data, ensure_ascii=False)
                commit_session(db)
            except Exception:
                pass
    finally:
        db.close()


async def stream_rerun_failed_chunks(
    project_id: str,
    report_id: str,
    payload: DeconstructRequest,
) -> AsyncGenerator[str, None]:
    """Rerun only failed map chunks, then merge with successful existing chunks."""
    db = SessionLocal()
    try:
        project = get_project_or_404(db, project_id)
        report = get_report_or_404(db, project_id, report_id)
        data = report_payload(report)
        chunks = resolve_report_chunks(project, data, db)
        total_words = int(data.get("total_words") or sum(len(chunk) for chunk in chunks))
        title = data.get("title") or report.source_filename or project.title
        options = data.get("options") or module_options_from_payload(payload)
        options["characters"] = False
        options["outline"] = False
        options["worldbuilding"] = False
        options["analysis_mode"] = analysis_mode_from_payload(payload)
        if payload.map_model or payload.reduce_model or payload.model:
            map_model, reduce_model = models_from_payload(payload)
        else:
            map_model = data.get("map_model") or data.get("model")
            reduce_model = data.get("reduce_model") or data.get("model") or map_model
        map_concurrency = map_concurrency_from_payload(payload)
        if not payload.map_concurrency and data.get("map_concurrency"):
            map_concurrency = max(1, min(int(data.get("map_concurrency")), MAX_MAP_CONCURRENCY))

        map_results = data.get("raw_map_results")
        if not isinstance(map_results, list):
            map_results = []
        while len(map_results) < len(chunks):
            map_results.append({"_error": "missing_result"})
        map_results = map_results[:len(chunks)]

        chunk_results = data.get("chunk_results")
        if not isinstance(chunk_results, list):
            chunk_results = [summarize_chunk_result(result, index) for index, result in enumerate(map_results)]
        while len(chunk_results) < len(chunks):
            chunk_results.append({"index": len(chunk_results), "status": "pending"})

        failed_indexes = [
            index for index, result in enumerate(map_results)
            if not isinstance(result, dict) or result.get("_error")
        ]
        if not failed_indexes:
            yield sse_event("error", {"message": "没有需要重跑的失败分块"})
            yield sse_event("done", {})
            return

        golden_text = ""
        golden_chapter_ids: list[str] = data.get("golden_chapter_ids") or []
        if options.get("golden_three"):
            golden_text, golden_chapter_ids = build_golden_three_source(project, payload, db)

        data.update({
            "status": "processing",
            "phase": "map",
            "map_model": map_model,
            "reduce_model": reduce_model,
            "model_limits": limits_info_for(reduce_model or map_model),
            "analysis_mode": options.get("analysis_mode", "fast"),
            "map_concurrency": map_concurrency,
            "source_chunks": chunks,
        })
        append_log(data, f"开始重跑 {len(failed_indexes)} 个失败分块，并发 {map_concurrency}")
        report.status = "processing"
        report.report_data = json.dumps(data, ensure_ascii=False)
        commit_session(db)

        yield sse_event("init", {
            "report_id": report.id,
            "total_chunks": len(chunks),
            "total_words": total_words,
            "title": title,
            "map_concurrency": map_concurrency,
            "map_model": map_model,
            "reduce_model": reduce_model,
            "model_limits": limits_info_for(reduce_model or map_model),
            "analysis_mode": options.get("analysis_mode", "fast"),
            "rerun_failed": True,
            "failed_indexes": failed_indexes,
        })
        yield sse_event("map_start", {
            "total_chunks": len(chunks),
            "map_concurrency": map_concurrency,
            "rerun_failed": True,
            "failed_count": len(failed_indexes),
        })

        semaphore = asyncio.Semaphore(map_concurrency)
        event_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        started_at = datetime.utcnow()

        async def _process_chunk(index: int) -> None:
            async with semaphore:
                await event_queue.put(("map_chunk_start", {"index": index, "rerun": True}))
                try:
                    async for event in stream_map_chunk(chunks[index], index, map_model, options):
                        if event["type"] == "token":
                            await event_queue.put(("map_token", {"index": index, "content": event.get("content", "")}))
                        elif event["type"] == "retry":
                            await event_queue.put(("map_retry", {
                                "index": index,
                                "attempt": event.get("attempt"),
                                "error": event.get("error"),
                            }))
                        elif event["type"] == "repair_start":
                            await event_queue.put(("map_repair_start", {"index": index, "error": event.get("error")}))
                        elif event["type"] == "repair_done":
                            await event_queue.put(("map_repair_done", {
                                "index": index,
                                "status": event.get("status"),
                                "error": event.get("error"),
                            }))
                        elif event["type"] == "result":
                            await event_queue.put(("map_result", {
                                "index": index,
                                "result": event.get("result") or {"_error": "missing_result"},
                            }))
                            return
                except Exception as exc:
                    await event_queue.put(("map_result", {
                        "index": index,
                        "result": {"_raw": str(exc), "_error": "llm_failed"},
                    }))

        tasks = [asyncio.create_task(_process_chunk(index)) for index in failed_indexes]
        rerun_done = 0

        while rerun_done < len(failed_indexes):
            event_type, event_data = await event_queue.get()
            if event_type in {"map_chunk_start", "map_token", "map_retry", "map_repair_start", "map_repair_done"}:
                yield sse_event(event_type, event_data)
                continue

            index = int(event_data.get("index", 0))
            result = event_data.get("result") if isinstance(event_data.get("result"), dict) else {"_error": "missing_result"}
            map_results[index] = result
            chunk_results[index] = summarize_chunk_result(result, index)
            rerun_done += 1

            failed = sum(1 for result in map_results if isinstance(result, dict) and result.get("_error"))
            completed = len(chunks) - failed
            elapsed = max((datetime.utcnow() - started_at).total_seconds(), 0.1)
            avg = elapsed / max(rerun_done, 1)
            remaining = avg * (len(failed_indexes) - rerun_done)

            yield sse_event("map_chunk", chunk_results[index])
            yield sse_event("map_progress", {
                "completed": completed,
                "failed": failed,
                "total": len(chunks),
                "elapsed_seconds": round(elapsed, 1),
                "avg_seconds_per_chunk": round(avg, 1),
                "estimated_remaining_seconds": round(remaining, 1),
                "rerun_completed": rerun_done,
                "rerun_total": len(failed_indexes),
            })

            data.update({
                "status": "processing",
                "phase": "map",
                "chunk_results": chunk_results,
                "raw_map_results": map_results,
                "completed_chunks": completed,
                "failed_chunks": failed,
                "elapsed_seconds": round(elapsed, 1),
                "avg_seconds_per_chunk": round(avg, 1),
                "estimated_remaining_seconds": round(remaining, 1),
            })
            level = "warning" if result.get("_error") else "info"
            append_log(data, f"重跑第 {index + 1}/{len(chunks)} 块完成", level)
            report.report_data = json.dumps(data, ensure_ascii=False)
            commit_session(db)

        await asyncio.gather(*tasks, return_exceptions=True)
        failed = sum(1 for result in map_results if isinstance(result, dict) and result.get("_error"))
        completed = len(chunks) - failed
        yield sse_event("map_complete", {
            "completed": completed,
            "failed": failed,
            "elapsed_seconds": round((datetime.utcnow() - started_at).total_seconds(), 1),
            "rerun_failed": True,
        })

        yield sse_event("reduce_start", {"rerun_failed": True})
        parsed = default_reduce_result(options)
        for section_key in reduce_section_keys(options):
            label = REDUCE_SECTION_LABELS.get(section_key, section_key)
            yield sse_event("reduce_section_start", {"section": section_key, "label": label, "rerun_failed": True})
            section_data = await reduce_section(section_key, map_results, title, total_words, reduce_model, options, golden_text)
            if section_data.get("_error"):
                parsed["reduce_errors"][section_key] = section_data.get("_error")
                yield sse_event("reduce_section_error", {
                    "section": section_key,
                    "label": label,
                    "error": section_data.get("_error"),
                    "rerun_failed": True,
                })
            else:
                merge_reduce_section(parsed, section_key, section_data, options)
                parsed["reduce_sections"].append(section_key)
                yield sse_event("reduce_section_complete", {"section": section_key, "label": label, "rerun_failed": True})

        elapsed_total = round((datetime.utcnow() - started_at).total_seconds(), 1)
        result = {
            "id": report.id,
            "title": title,
            "status": "completed",
            "phase": "completed",
            "golden_three": parsed.get("golden_three") if options.get("golden_three") else None,
            "structure": parsed.get("structure", {}),
            "plot_nodes": parsed.get("plot_nodes", []),
            "characters": parsed.get("characters", []) if options.get("characters") else [],
            "worldbuilding_entries": parsed.get("worldbuilding_entries", []) if options.get("worldbuilding") else [],
            "highlights": parsed.get("highlights", []),
            "rhythm_curve": parsed.get("rhythm_curve") if options.get("rhythm") else None,
            "patterns": parsed.get("patterns") if options.get("patterns") else None,
            "reduce_sections": parsed.get("reduce_sections", []),
            "reduce_errors": parsed.get("reduce_errors", {}),
            "raw_map_results": map_results,
            "chunk_results": [summarize_chunk_result(r, i) for i, r in enumerate(map_results)],
            "logs": data.get("logs") or [],
            "total_chunks": len(chunks),
            "completed_chunks": completed,
            "failed_chunks": failed,
            "elapsed_seconds": elapsed_total,
            "avg_seconds_per_chunk": round(elapsed_total / max(len(failed_indexes), 1), 1),
            "estimated_remaining_seconds": 0,
            "total_words": total_words,
            "options": options,
            "map_model": map_model,
            "reduce_model": reduce_model,
            "model_limits": limits_info_for(reduce_model or map_model),
            "analysis_mode": options.get("analysis_mode", "fast"),
            "map_concurrency": map_concurrency,
            "selected_chapter_ids": data.get("selected_chapter_ids") or [],
            "golden_chapter_ids": golden_chapter_ids,
            "source_chunks": chunks,
            "created_at": data.get("created_at") or (report.created_at.isoformat() if report.created_at else datetime.utcnow().isoformat()),
            "completed_at": datetime.utcnow().isoformat(),
        }
        append_log(result, "失败分块重跑并自动合并完成")
        yield sse_event("reduce_complete", result)
        yield sse_event("done", {})
        report.status = "completed"
        report.report_data = json.dumps(result, ensure_ascii=False)
        commit_session(db)
    except Exception as exc:
        traceback.print_exc()
        yield sse_event("error", {"message": str(exc)})
        yield sse_event("done", {})
    finally:
        db.close()


async def run_deconstruct_job(
    project_id: str,
    report_id: str,
    title: str,
    chunks: list[str],
    total_words: int,
    map_model: Optional[str],
    reduce_model: Optional[str],
    options: dict,
    include_rhythm: bool,
    include_patterns: bool,
    map_concurrency: int,
    golden_text: str = "",
) -> None:
    """Run the full deconstruct pipeline as a background task (non-streaming)."""
    from .map_reduce import map_chunk, reduce_analysis
    from .model_selection import limits_info_for

    db = SessionLocal()
    heartbeat_task: asyncio.Task | None = None
    operation_id: str | None = None
    try:
        report = get_report_or_404(db, project_id, report_id)
        operation_id = report.operation_id
        if operation_id:
            heartbeat_task = asyncio.create_task(heartbeat_loop(operation_id))
            record_operation_signal(
                operation_id,
                "phase",
                {"phase": "map", "total_chunks": len(chunks)},
                message=f"开始分析 {len(chunks)} 个分块",
            )
        progress_data = load_report_data(report)
        started_at = datetime.utcnow()
        progress_data.update({
            "status": "processing",
            "phase": "map",
            "started_at": started_at.isoformat(),
        })
        append_log(progress_data, f"开始分块拆书分析，并发 {map_concurrency}；按真实活动监测，不设总时限")
        report.status = "processing"
        report.report_data = json.dumps(progress_data, ensure_ascii=False)
        commit_session(db)

        semaphore = asyncio.Semaphore(map_concurrency)

        async def limited_map(index: int, chunk: str) -> tuple[int, dict]:
            async with semaphore:
                try:
                    if operation_id:
                        record_operation_signal(
                            operation_id,
                            "phase",
                            {"phase": "map", "chunk_index": index, "total_chunks": len(chunks)},
                            message=f"正在分析第 {index + 1}/{len(chunks)} 个分块",
                        )
                    result: dict | None = None
                    with activate_operation(operation_id):
                        async for event in stream_map_chunk(chunk, index, map_model, options):
                            event_type = event.get("type")
                            if event_type == "token" and operation_id:
                                token = str(event.get("content") or "")
                                record_operation_signal(
                                    operation_id,
                                    "output",
                                    {"chunk_index": index, "output_chars": len(token)},
                                    message=f"第 {index + 1}/{len(chunks)} 个分块正在生成",
                                )
                            elif event_type in {"retry", "repair_start", "repair_done"} and operation_id:
                                record_operation_signal(
                                    operation_id,
                                    "tool",
                                    {"chunk_index": index, "activity": event_type},
                                    message=f"第 {index + 1}/{len(chunks)} 个分块正在校验输出",
                                )
                            elif event_type == "result":
                                candidate = event.get("result")
                                result = candidate if isinstance(candidate, dict) else {"_error": "missing_result"}
                    return index, result or {"_error": "missing_result"}
                except Exception as exc:
                    return index, {"_raw": str(exc), "_error": "llm_failed"}

        tasks = [asyncio.create_task(limited_map(index, chunk)) for index, chunk in enumerate(chunks)]
        map_results: list[dict | None] = [None] * len(chunks)
        completed_chunks = 0
        failed_chunks = 0

        for task in asyncio.as_completed(tasks):
            index, result = await task
            map_results[index] = result
            completed_chunks += 1
            if result.get("_error"):
                failed_chunks += 1

            elapsed_seconds = max((datetime.utcnow() - started_at).total_seconds(), 0.1)
            avg_seconds_per_chunk = elapsed_seconds / completed_chunks
            remaining_chunks = max(len(chunks) - completed_chunks, 0)
            progress_data = load_report_data(report)
            chunk_results = progress_data.get("chunk_results") or []
            while len(chunk_results) < len(chunks):
                chunk_results.append({"index": len(chunk_results), "status": "pending"})
            chunk_results[index] = summarize_chunk_result(result, index)
            progress_data.update({
                "status": "processing",
                "phase": "map",
                "completed_chunks": completed_chunks,
                "failed_chunks": failed_chunks,
                "chunk_results": chunk_results,
                "elapsed_seconds": round(elapsed_seconds, 1),
                "avg_seconds_per_chunk": round(avg_seconds_per_chunk, 1),
                "estimated_remaining_seconds": round(avg_seconds_per_chunk * remaining_chunks, 1),
            })
            level = "warning" if result.get("_error") else "info"
            message = f"第 {index + 1}/{len(chunks)} 块分析完成"
            if result.get("_error"):
                message = f"{message}：{result.get('_error')}"
            append_log(progress_data, message, level)
            report.report_data = json.dumps(progress_data, ensure_ascii=False)
            commit_session(db)
            db.refresh(report)
            if operation_id:
                record_operation_signal(
                    operation_id,
                    "checkpoint",
                    {
                        "phase": "map",
                        "chunk_index": index,
                        "completed": completed_chunks,
                        "failed": failed_chunks,
                        "total": len(chunks),
                        "progress_mode": "determinate",
                        "progress_current": completed_chunks,
                        "progress_total": len(chunks),
                    },
                    message=f"拆书分块 {completed_chunks}/{len(chunks)} 已保存",
                )

        final_map_results = [item or {"_error": "missing_result"} for item in map_results]
        progress_data = load_report_data(report)
        progress_data.update({
            "status": "processing",
            "phase": "reduce",
            "completed_chunks": completed_chunks,
            "failed_chunks": failed_chunks,
            "estimated_remaining_seconds": 0,
            "raw_map_results": final_map_results,
        })
        append_log(progress_data, "分块分析完成，开始自动合并拆书结果")
        report.report_data = json.dumps(progress_data, ensure_ascii=False)
        commit_session(db)
        db.refresh(report)

        if operation_id:
            record_operation_signal(
                operation_id,
                "phase",
                {
                    "phase": "reduce",
                    "completed": completed_chunks,
                    "total": len(chunks),
                    "progress_mode": "indeterminate",
                },
                message="分块分析完成，正在合并拆书结果",
            )
        with activate_operation(operation_id):
            reduce_data = await reduce_analysis(final_map_results, title, total_words, reduce_model, options, golden_text)
        if reduce_data.get("_error"):
            progress_data = load_report_data(report)
            append_log(progress_data, f"自动合并输出异常：{reduce_data.get('_error')}", "warning")
            report.report_data = json.dumps(progress_data, ensure_ascii=False)
            commit_session(db)
            db.refresh(report)

        result = {
            "id": report.id,
            "title": title,
            "status": "completed",
            "phase": "completed",
            "golden_three": reduce_data.get("golden_three") if options.get("golden_three") else None,
            "structure": reduce_data.get("structure", {}),
            "plot_nodes": reduce_data.get("plot_nodes", []),
            "characters": reduce_data.get("characters", []) if options.get("characters") else [],
            "worldbuilding_entries": reduce_data.get("worldbuilding_entries", []) if options.get("worldbuilding") else [],
            "highlights": reduce_data.get("highlights", []),
            "rhythm_curve": reduce_data.get("rhythm_curve") if include_rhythm else None,
            "patterns": reduce_data.get("patterns") if include_patterns else None,
            "reduce_error": reduce_data.get("_error"),
            "reduce_sections": reduce_data.get("reduce_sections", []),
            "reduce_errors": reduce_data.get("reduce_errors", {}),
            "raw_map_results": final_map_results,
            "chunk_results": progress_data.get("chunk_results") or [],
            "logs": progress_data.get("logs") or [],
            "total_chunks": len(chunks),
            "completed_chunks": completed_chunks,
            "failed_chunks": failed_chunks,
            "elapsed_seconds": round((datetime.utcnow() - started_at).total_seconds(), 1),
            "avg_seconds_per_chunk": progress_data.get("avg_seconds_per_chunk", 0),
            "estimated_remaining_seconds": 0,
            "total_words": total_words,
            "options": options,
            "map_model": map_model,
            "reduce_model": reduce_model,
            "model_limits": limits_info_for(reduce_model or map_model),
            "analysis_mode": options.get("analysis_mode", "fast"),
            "created_at": report.created_at.isoformat() if report.created_at else datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
        }
        append_log(result, "自动合并完成，拆书报告已生成")
        report.status = "completed"
        report.report_data = json.dumps(result, ensure_ascii=False)
        commit_session(db)
        finish_operation(operation_id, message=f"拆书分析完成，共处理 {completed_chunks} 个分块")
    except asyncio.CancelledError:
        if report_id:
            try:
                report = get_report_or_404(db, project_id, report_id)
                cancelled = load_report_data(report)
                cancelled.update({"status": "cancelled", "phase": "cancelled"})
                append_log(cancelled, "任务已由用户取消，已完成的分块仍保留", "warning")
                report.status = "cancelled"
                report.report_data = json.dumps(cancelled, ensure_ascii=False)
                commit_session(db)
            except Exception:
                pass
        finish_operation(operation_id, message="拆书任务已取消，已完成分块已保留", status="cancelled")
    except Exception as exc:
        try:
            report = get_report_or_404(db, project_id, report_id)
            failed = load_report_data(report)
            failed.update({
                "id": report.id,
                "status": "failed",
                "phase": "failed",
                "error": str(exc),
            })
            append_log(failed, f"拆书任务失败：{exc}", "error")
            report.status = "failed"
            report.report_data = json.dumps(failed, ensure_ascii=False)
            commit_session(db)
        except Exception:
            pass
        fail_operation(operation_id, exc, next_action="可从拆书页面重新开始，或重跑失败分块")
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        db.close()
