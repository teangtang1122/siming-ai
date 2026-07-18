"""Deconstruct / book analysis — Map-Reduce pipeline for novel text analysis."""
import asyncio
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import ValidationError
from ..core.response import ApiResponse
from ..database.session import SessionLocal, get_db
from ..modules.story.application.deconstruct import DeconstructionReader
from ..modules.story.interfaces.deconstruct_dependencies import (
    get_deconstruction_reader,
)
from ..schemas.deconstruct import DeconstructImportRequest, DeconstructRequest
from ..services.deconstruct.import_service import import_deconstruct_report
from ..services.deconstruct.model_selection import (
    map_concurrency_from_payload,
    models_from_payload,
    module_options_from_payload,
)
from ..services.deconstruct.orchestrator import (
    run_deconstruct_job,
    sse_event,
    stream_rerun_failed_chunks,
)
from ..services.deconstruct.pipeline import (
    build_golden_three_source,
    build_source_from_payload,
    chapter_aware_chunks,
    split_text,
)
from ..services.deconstruct.report_store import (
    create_deconstruct_report,
    get_report_or_404,
    report_payload,
)
from ..services.operation_runtime import register_operation_actions, unregister_operation_actions

router = APIRouter(tags=["deconstruct"])
_DECONSTRUCT_TASKS: dict[str, asyncio.Task] = {}


async def _cancel_deconstruct_task(report_id: str) -> None:
    task = _DECONSTRUCT_TASKS.get(report_id)
    if task and not task.done():
        task.cancel()


def _launch_deconstruct_task(report: Any, coroutine) -> None:
    task = asyncio.create_task(coroutine, name=f"deconstruct-{report.id}")
    _DECONSTRUCT_TASKS[report.id] = task
    if report.operation_id:
        register_operation_actions(
            report.operation_id,
            cancel=lambda: _cancel_deconstruct_task(report.id),
        )

    def _cleanup(_task: asyncio.Task) -> None:
        _DECONSTRUCT_TASKS.pop(report.id, None)
        if report.operation_id:
            unregister_operation_actions(report.operation_id)

    task.add_done_callback(_cleanup)


async def _stream_report_progress(project_id: str, report_id: str):
    emitted_chunks: set[int] = set()
    last_progress: tuple | None = None
    last_phase: str | None = None
    heartbeat_tick = 0
    while True:
        db = SessionLocal()
        try:
            report = get_report_or_404(db, project_id, report_id)
            data = report_payload(report)
            status = str(data.get("status") or report.status)
            phase = str(data.get("phase") or status)
        finally:
            db.close()

        if last_phase is None:
            yield sse_event("init", {
                "report_id": report_id,
                "operation_id": data.get("operation_id"),
                "total_chunks": data.get("total_chunks", 0),
                "total_words": data.get("total_words", 0),
                "title": data.get("title"),
                "map_concurrency": data.get("map_concurrency", 1),
                "map_model": data.get("map_model"),
                "reduce_model": data.get("reduce_model"),
                "analysis_mode": data.get("analysis_mode", "fast"),
            })
        if phase != last_phase:
            if phase == "map":
                yield sse_event("map_start", {
                    "total_chunks": data.get("total_chunks", 0),
                    "map_concurrency": data.get("map_concurrency", 1),
                })
            elif phase == "reduce":
                yield sse_event("map_complete", {
                    "completed": data.get("completed_chunks", 0),
                    "failed": data.get("failed_chunks", 0),
                    "elapsed_seconds": data.get("elapsed_seconds", 0),
                })
                yield sse_event("reduce_start", {})
            last_phase = phase

        for item in data.get("chunk_results") or []:
            index = int(item.get("index", 0))
            if index not in emitted_chunks and item.get("status") not in {None, "pending", "streaming"}:
                emitted_chunks.add(index)
                yield sse_event("map_chunk", item)

        progress = (
            data.get("completed_chunks", 0),
            data.get("failed_chunks", 0),
            data.get("elapsed_seconds", 0),
        )
        if progress != last_progress:
            yield sse_event("map_progress", {
                "completed": progress[0],
                "failed": progress[1],
                "total": data.get("total_chunks", 0),
                "elapsed_seconds": progress[2],
                "avg_seconds_per_chunk": data.get("avg_seconds_per_chunk", 0),
                "estimated_remaining_seconds": data.get("estimated_remaining_seconds", 0),
            })
            last_progress = progress

        if status == "completed":
            yield sse_event("reduce_complete", data)
            yield sse_event("done", {})
            yield "data: [DONE]\n\n"
            return
        if status in {"failed", "cancelled"}:
            message = data.get("error") or ("拆书任务已取消" if status == "cancelled" else "拆书任务失败")
            yield sse_event("error", {"message": message, "operation_id": data.get("operation_id")})
            yield sse_event("done", {})
            yield "data: [DONE]\n\n"
            return

        heartbeat_tick += 1
        if heartbeat_tick % 10 == 0:
            yield sse_event("heartbeat", {"report_id": report_id, "phase": phase})
        await asyncio.sleep(1)


@router.get("/projects/{project_id}/deconstruct/preview")
def deconstruct_preview(
    project_id: str,
    reader: Annotated[DeconstructionReader, Depends(get_deconstruction_reader)],
):
    """Get available source material for deconstruct analysis."""
    return ApiResponse.success(data=reader.preview(project_id))


@router.get("/projects/{project_id}/deconstruct/reports")
def list_deconstruct_reports(
    project_id: str,
    reader: Annotated[DeconstructionReader, Depends(get_deconstruction_reader)],
):
    """List persisted deconstruct reports for this project."""
    return ApiResponse.success(data=reader.reports(project_id))


@router.get("/projects/{project_id}/deconstruct/{report_id}")
def get_deconstruct_report(project_id: str, report_id: str, db: Session = Depends(get_db)):
    """Get a persisted deconstruct report."""
    get_project_or_404(db, project_id)
    report = get_report_or_404(db, project_id, report_id)
    return ApiResponse.success(data=report_payload(report))


@router.get("/projects/{project_id}/deconstruct/{report_id}/status")
def deconstruct_status(project_id: str, report_id: str, db: Session = Depends(get_db)):
    """Stream the current status for a deconstruct report."""
    get_project_or_404(db, project_id)
    report = get_report_or_404(db, project_id, report_id)
    payload = report_payload(report)
    status_data = {
        "id": report.id,
        "status": report.status,
        "phase": payload.get("phase", report.status),
        "total_chunks": payload.get("total_chunks", 0),
        "completed_chunks": payload.get("completed_chunks", 0),
        "failed_chunks": payload.get("failed_chunks", 0),
        "error": payload.get("error"),
    }

    def event_generator():
        yield f"data: {json.dumps(status_data, ensure_ascii=False, separators=(',', ':'))}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/projects/{project_id}/deconstruct")
async def deconstruct_text(project_id: str, payload: DeconstructRequest, db: Session = Depends(get_db)):
    """Run Map-Reduce deconstruct analysis on submitted text and wait for completion."""
    project = get_project_or_404(db, project_id)
    text, title, selected_chapter_ids, source_chapters = build_source_from_payload(project, payload, db)
    if len(text) < 100:
        raise ValidationError("文本太短，至少需要100个字符进行分析")

    total_words = len(text)
    chunks = chapter_aware_chunks(source_chapters) if source_chapters else split_text(text)
    if len(chunks) == 0:
        raise ValidationError("文本分块失败")
    options = module_options_from_payload(payload)
    map_concurrency = map_concurrency_from_payload(payload)
    map_model, reduce_model = models_from_payload(payload)
    golden_text, golden_chapter_ids = build_golden_three_source(project, payload, db) if payload.include_golden_three else ("", [])

    report = create_deconstruct_report(
        db=db,
        project_id=project_id,
        title=title,
        chunks=chunks,
        total_words=total_words,
        selected_chapter_ids=selected_chapter_ids,
        map_model=map_model,
        reduce_model=reduce_model,
        options=options,
        map_concurrency=map_concurrency,
        golden_chapter_ids=golden_chapter_ids,
    )
    await run_deconstruct_job(
        project_id=project_id,
        report_id=report.id,
        title=title,
        chunks=chunks,
        total_words=total_words,
        map_model=map_model,
        reduce_model=reduce_model,
        options=options,
        include_rhythm=payload.include_rhythm,
        include_patterns=payload.include_patterns,
        map_concurrency=map_concurrency,
        golden_text=golden_text,
    )
    db.refresh(report)
    result = report_payload(report)
    if result.get("status") == "failed":
        raise ValidationError(result.get("error") or "拆书分析失败")
    return ApiResponse.success(data=result, message="拆书分析完成")


@router.post("/projects/{project_id}/deconstruct/start")
async def start_deconstruct_job(
    project_id: str,
    payload: DeconstructRequest,
    db: Session = Depends(get_db),
):
    """Create a deconstruct job and process it in the background."""
    project = get_project_or_404(db, project_id)
    text, title, selected_chapter_ids, source_chapters = build_source_from_payload(project, payload, db)
    if len(text) < 100:
        raise ValidationError("文本太短，至少需要100个字符进行分析")

    chunks = chapter_aware_chunks(source_chapters) if source_chapters else split_text(text)
    if len(chunks) == 0:
        raise ValidationError("文本分块失败")
    options = module_options_from_payload(payload)
    map_concurrency = map_concurrency_from_payload(payload)
    map_model, reduce_model = models_from_payload(payload)
    golden_text, golden_chapter_ids = build_golden_three_source(project, payload, db) if payload.include_golden_three else ("", [])

    report = create_deconstruct_report(
        db=db,
        project_id=project_id,
        title=title,
        chunks=chunks,
        total_words=len(text),
        selected_chapter_ids=selected_chapter_ids,
        map_model=map_model,
        reduce_model=reduce_model,
        options=options,
        map_concurrency=map_concurrency,
        golden_chapter_ids=golden_chapter_ids,
    )
    _launch_deconstruct_task(
        report,
        run_deconstruct_job(
            project_id,
            report.id,
            title,
            chunks,
            len(text),
            map_model,
            reduce_model,
            options,
            payload.include_rhythm,
            payload.include_patterns,
            map_concurrency,
            golden_text,
        ),
    )
    return ApiResponse.success(data=report_payload(report), message="拆书任务已开始")


@router.post("/projects/{project_id}/deconstruct/stream")
async def deconstruct_stream(
    project_id: str,
    payload: DeconstructRequest,
    db: Session = Depends(get_db),
):
    """Run Map-Reduce deconstruct with real-time SSE streaming."""
    project = get_project_or_404(db, project_id)
    text, title, selected_chapter_ids, source_chapters = build_source_from_payload(project, payload, db)
    if len(text) < 100:
        raise ValidationError("文本太短，至少需要100个字符进行分析")

    total_words = len(text)
    chunks = chapter_aware_chunks(source_chapters) if source_chapters else split_text(text)
    if not chunks:
        raise ValidationError("文本分块失败")
    options = module_options_from_payload(payload)
    map_concurrency = map_concurrency_from_payload(payload)
    map_model, reduce_model = models_from_payload(payload)
    golden_text, golden_chapter_ids = build_golden_three_source(project, payload, db) if payload.include_golden_three else ("", [])

    report = create_deconstruct_report(
        db=db,
        project_id=project_id,
        title=title,
        chunks=chunks,
        total_words=total_words,
        selected_chapter_ids=selected_chapter_ids,
        map_model=map_model,
        reduce_model=reduce_model,
        options=options,
        map_concurrency=map_concurrency,
        golden_chapter_ids=golden_chapter_ids,
    )
    _launch_deconstruct_task(
        report,
        run_deconstruct_job(
            project_id=project_id,
            report_id=report.id,
            title=title,
            chunks=chunks,
            total_words=total_words,
            map_model=map_model,
            reduce_model=reduce_model,
            options=options,
            include_rhythm=payload.include_rhythm,
            include_patterns=payload.include_patterns,
            map_concurrency=map_concurrency,
            golden_text=golden_text,
        ),
    )

    return StreamingResponse(
        _stream_report_progress(project_id, report.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/projects/{project_id}/deconstruct/{report_id}/rerun-failed/stream")
async def rerun_failed_deconstruct_chunks(
    project_id: str,
    report_id: str,
    payload: DeconstructRequest,
):
    """Rerun only failed map chunks from an existing report, then merge again."""
    return StreamingResponse(
        stream_rerun_failed_chunks(project_id, report_id, payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/projects/{project_id}/deconstruct/{report_id}/import")
def import_report(
    project_id: str,
    report_id: str,
    payload: DeconstructImportRequest,
    db: Session = Depends(get_db),
):
    """Import outline nodes and/or characters extracted from a deconstruct report."""
    get_project_or_404(db, project_id)
    data = import_deconstruct_report(db, project_id, report_id, payload)
    return ApiResponse.success(data=data, message="拆书结果导入完成")
