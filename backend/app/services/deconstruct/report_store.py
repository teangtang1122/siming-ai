"""Report CRUD helpers for the deconstruct pipeline."""

from app.architecture.uow import commit_session
import json
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ...core.exceptions import NotFoundError
from ...database.models import DeconstructionReport
from .model_selection import limits_info_for


def get_report_or_404(db: Session, project_id: str, report_id: str) -> DeconstructionReport:
    report = (
        db.query(DeconstructionReport)
        .filter(DeconstructionReport.id == report_id, DeconstructionReport.project_id == project_id)
        .first()
    )
    if not report:
        raise NotFoundError("拆书报告不存在")
    return report


def load_report_data(report: DeconstructionReport) -> dict:
    try:
        data = json.loads(report.report_data or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def append_log(data: dict, message: str, level: str = "info") -> None:
    logs = data.setdefault("logs", [])
    logs.append({
        "time": datetime.utcnow().isoformat(),
        "level": level,
        "message": message,
    })
    if len(logs) > 200:
        del logs[:-200]


def report_payload(report: DeconstructionReport) -> dict:
    data = load_report_data(report)
    data.setdefault("id", report.id)
    data.setdefault("status", report.status)
    data.setdefault("created_at", report.created_at.isoformat() if report.created_at else None)
    data.setdefault("operation_id", report.operation_id)
    return data


def create_deconstruct_report(
    db: Session,
    project_id: str,
    title: str,
    chunks: list[str],
    total_words: int,
    selected_chapter_ids: list[str],
    map_model: Optional[str],
    reduce_model: Optional[str],
    options: dict,
    map_concurrency: int,
    golden_chapter_ids: Optional[list[str]] = None,
) -> DeconstructionReport:
    chunk_results = [
        {
            "index": index,
            "status": "pending",
            "summary": "",
            "characters": [],
            "events": [],
            "highlights": [],
        }
        for index in range(len(chunks))
    ]
    report_data = {
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
        "chunk_results": chunk_results,
        "created_at": datetime.utcnow().isoformat(),
        "logs": [],
    }
    append_log(report_data, f"已创建拆书任务，共 {len(chunks)} 个分析块")
    report = DeconstructionReport(
        project_id=project_id,
        source_filename=title,
        status="processing",
        report_data=json.dumps(report_data, ensure_ascii=False),
    )
    db.add(report)
    db.flush()
    from ..operation_runtime import ensure_operation

    operation = ensure_operation(
        db,
        source_kind="deconstruct",
        source_id=report.id,
        project_id=project_id,
        title=f"拆书分析：{title}",
        status="queued",
        phase="queued",
        message=f"准备分析 {len(chunks)} 个分块",
        model_source=reduce_model or map_model,
        tool_mode="map_reduce",
        resume_url=f"/project/{project_id}?view=deconstruct",
        can_pause=False,
        can_cancel=True,
        can_retry=False,
        progress_mode="determinate",
        progress_current=0,
        progress_total=len(chunks),
    )
    report.operation_id = operation.id
    report_data["operation_id"] = operation.id
    report.report_data = json.dumps(report_data, ensure_ascii=False)
    commit_session(db)
    db.refresh(report)
    return report
