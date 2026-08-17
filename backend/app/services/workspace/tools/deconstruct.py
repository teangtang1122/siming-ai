"""Deconstruct workspace tools."""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import Chapter, DeconstructionReport
from ....schemas.deconstruct import DeconstructImportRequest, DeconstructRequest
from ....services.deconstruct.import_service import import_deconstruct_report
from ....services.deconstruct.model_selection import map_concurrency_from_payload, models_from_payload, module_options_from_payload
from ....services.deconstruct.orchestrator import run_deconstruct_job, stream_rerun_failed_chunks
from ....services.deconstruct.pipeline import build_golden_three_source, build_source_from_payload, chapter_aware_chunks, split_text
from ....services.deconstruct.report_store import create_deconstruct_report, get_report_or_404, report_payload
from ....core.utils import count_words


async def _consume_rerun_stream(project_id: str, report_id: str, request: DeconstructRequest) -> None:
    async for _event in stream_rerun_failed_chunks(project_id, report_id, request):
        pass


async def preview_deconstruct_source(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())
        .all()
    )
    items = [{"id": c.id, "title": c.title, "word_count": c.word_count or 0, "preview": (c.content or "")[:200]} for c in chapters]
    total_words = sum(item["word_count"] for item in items)
    return {
        "tool": "preview_deconstruct_source",
        "status": "ok",
        "detail": f"当前作品 {len(items)} 章，{total_words} 字",
        "data": {"chapters": items, "total_chapters": len(items), "total_words": total_words, "can_deconstruct": total_words > 500},
    }


async def list_deconstruct_reports(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    limit = max(1, min(50, int(args.get("limit") or 20)))
    reports = (
        db.query(DeconstructionReport)
        .filter(DeconstructionReport.project_id == project_id)
        .order_by(DeconstructionReport.created_at.desc())
        .limit(limit)
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
    return {"tool": "list_deconstruct_reports", "status": "ok", "detail": f"共 {len(items)} 个拆书报告", "data": {"items": items, "total": len(items)}}


async def get_deconstruct_report(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    report_id = str(args.get("report_id") or args.get("id") or "").strip()
    if not report_id:
        return {"tool": "get_deconstruct_report", "status": "skipped", "detail": "缺少拆书报告 ID"}
    report = get_report_or_404(db, project_id, report_id)
    return {"tool": "get_deconstruct_report", "status": "ok", "detail": "已读取拆书报告", "data": report_payload(report)}


async def start_deconstruct_job(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    request = DeconstructRequest(**{
        "text": args.get("text"),
        "chapter_ids": args.get("chapter_ids") if isinstance(args.get("chapter_ids"), list) else [],
        "title": args.get("title"),
        "model": args.get("model"),
        "map_model": args.get("map_model"),
        "reduce_model": args.get("reduce_model"),
        "analysis_mode": args.get("analysis_mode") or "fast",
        "include_golden_three": bool(args.get("include_golden_three", False)),
        "include_rhythm": bool(args.get("include_rhythm", True)),
        "include_patterns": bool(args.get("include_patterns", True)),
        "map_concurrency": int(args.get("map_concurrency") or 4),
    })
    from ....database.models import Project

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "start_deconstruct_job", "status": "skipped", "detail": "作品不存在"}
    text, title, selected_chapter_ids, source_chapters = build_source_from_payload(project, request, db)
    if len(text) < 100:
        return {"tool": "start_deconstruct_job", "status": "skipped", "detail": "文本太短，至少需要 100 个字符"}
    chunks = chapter_aware_chunks(source_chapters) if source_chapters else split_text(text)
    if not chunks:
        return {"tool": "start_deconstruct_job", "status": "skipped", "detail": "文本分块失败"}
    options = module_options_from_payload(request)
    map_concurrency = map_concurrency_from_payload(request)
    map_model, reduce_model = models_from_payload(request)
    golden_text, golden_chapter_ids = build_golden_three_source(project, request, db) if request.include_golden_three else ("", [])
    report = create_deconstruct_report(
        db=db,
        project_id=project_id,
        title=title,
        chunks=chunks,
        total_words=count_words(text),
        selected_chapter_ids=selected_chapter_ids,
        map_model=map_model,
        reduce_model=reduce_model,
        options=options,
        map_concurrency=map_concurrency,
        golden_chapter_ids=golden_chapter_ids,
    )
    if bool(args.get("run_now", True)):
        asyncio.create_task(run_deconstruct_job(
            project_id,
            report.id,
            title,
            chunks,
            len(text),
            map_model,
            reduce_model,
            options,
            request.include_rhythm,
            request.include_patterns,
            map_concurrency,
            golden_text,
        ))
    return {"tool": "start_deconstruct_job", "status": "ok", "detail": f"已创建拆书任务，共 {len(chunks)} 块", "data": report_payload(report)}


async def rerun_failed_deconstruct_chunks(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    report_id = str(args.get("report_id") or args.get("id") or "").strip()
    if not report_id:
        return {"tool": "rerun_failed_deconstruct_chunks", "status": "skipped", "detail": "缺少拆书报告 ID"}
    request = DeconstructRequest(**{k: v for k, v in args.items() if k in DeconstructRequest.model_fields})
    asyncio.create_task(_consume_rerun_stream(project_id, report_id, request))
    return {"tool": "rerun_failed_deconstruct_chunks", "status": "ok", "detail": "已开始重跑失败拆书块", "data": {"report_id": report_id}}


async def import_deconstruct_report_tool(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    report_id = str(args.get("report_id") or args.get("id") or "").strip()
    if not report_id:
        return {"tool": "import_deconstruct_report", "status": "skipped", "detail": "缺少拆书报告 ID"}
    request = DeconstructImportRequest(
        import_outline=bool(args.get("import_outline", False)),
        import_characters=bool(args.get("import_characters", False)),
        import_worldbuilding=bool(args.get("import_worldbuilding", False)),
    )
    data = import_deconstruct_report(db, project_id, report_id, request)
    return {"tool": "import_deconstruct_report", "status": "ok", "detail": "拆书报告已导入", "data": data}
