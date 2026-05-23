"""Deconstruct / book analysis — Map-Reduce pipeline for novel text analysis."""
import json

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import ValidationError
from ..core.response import ApiResponse
from ..database.models import Chapter, DeconstructionReport
from ..database.session import get_db
from ..schemas.deconstruct import DeconstructImportRequest, DeconstructRequest
from ..services.deconstruct.import_service import import_deconstruct_report
from ..services.deconstruct.model_selection import (
    map_concurrency_from_payload,
    models_from_payload,
    module_options_from_payload,
)
from ..services.deconstruct.orchestrator import (
    run_deconstruct_job,
    stream_deconstruct,
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

router = APIRouter(tags=["deconstruct"])


@router.get("/projects/{project_id}/deconstruct/preview")
def deconstruct_preview(project_id: str, db: Session = Depends(get_db)):
    """Get available source material for deconstruct analysis."""
    get_project_or_404(db, project_id)

    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.created_at.asc())
        .all()
    )
    chapter_opts = [
        {
            "id": c.id,
            "title": c.title,
            "word_count": c.word_count or 0,
            "preview": (c.content or "")[:200],
        }
        for c in chapters
    ]

    total_words = sum(c.word_count or 0 for c in chapters)
    combined_text = "\n\n".join(
        f"{'=' * 40}\n{c.title}\n{'=' * 40}\n\n{c.content or ''}"
        for c in chapters
    )

    return ApiResponse.success(data={
        "chapters": chapter_opts,
        "total_chapters": len(chapters),
        "total_words": total_words,
        "can_deconstruct": total_words > 500,
        "combined_text": combined_text if total_words <= 80000 else combined_text[:80000],
    })


@router.get("/projects/{project_id}/deconstruct/reports")
def list_deconstruct_reports(project_id: str, db: Session = Depends(get_db)):
    """List persisted deconstruct reports for this project."""
    get_project_or_404(db, project_id)
    reports = (
        db.query(DeconstructionReport)
        .filter(DeconstructionReport.project_id == project_id)
        .order_by(DeconstructionReport.created_at.desc())
        .limit(20)
        .all()
    )
    items = []
    for report in reports:
        payload = report_payload(report)
        items.append({
            "id": report.id,
            "title": payload.get("title") or report.source_filename,
            "status": report.status,
            "phase": payload.get("phase", report.status),
            "total_chunks": payload.get("total_chunks", 0),
            "completed_chunks": payload.get("completed_chunks", 0),
            "failed_chunks": payload.get("failed_chunks", 0),
            "total_words": payload.get("total_words", 0),
            "created_at": report.created_at.isoformat() if report.created_at else None,
            "completed_at": payload.get("completed_at"),
        })
    return ApiResponse.success(data={"items": items, "total": len(items)})


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
    background_tasks: BackgroundTasks,
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
    background_tasks.add_task(
        run_deconstruct_job,
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

    return StreamingResponse(
        stream_deconstruct(
            project_id=project_id,
            title=title,
            chunks=chunks,
            total_words=total_words,
            map_model=map_model,
            reduce_model=reduce_model,
            options=options,
            include_rhythm=payload.include_rhythm,
            include_patterns=payload.include_patterns,
            selected_chapter_ids=selected_chapter_ids,
            map_concurrency=map_concurrency,
            golden_text=golden_text,
            golden_chapter_ids=golden_chapter_ids,
        ),
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
