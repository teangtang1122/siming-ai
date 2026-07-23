"""Small runtime helpers for novel-creation stage orchestration."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session
from app.services.novel_creation_workspace import (
    STAGE_LABELS,
    add_run_event,
    serialize_run,
    serialize_session,
)
from app.services.observability.run_events import classify_failure


async def stage_data_with_fallback(
    db: Session,
    run: Any,
    session: Any,
    *,
    stage: str,
    baseline: dict[str, Any],
    model: str,
    use_model: bool,
    quick_run: bool,
    manifest: Any,
    working_draft: dict[str, Any],
    enhance: Any,
) -> tuple[dict[str, Any], str]:
    if not use_model or not model or stage == "final_review":
        return baseline, "contract"
    try:
        data = await enhance(
            session,
            stage,
            baseline,
            model,
            context_manifest=manifest,
            input_snapshot=working_draft,
        )
        return data, "model"
    except Exception as exc:
        failure_class = classify_failure(str(exc))
        if not quick_run or failure_class not in {"empty_response", "invalid_response"}:
            raise
        add_run_event(
            db,
            run,
            "stage_repaired",
            "warning",
            f"{STAGE_LABELS.get(stage, stage)}的模型回复不可用，已采用安全结构继续",
            {
                "stage": stage,
                "failure_class": failure_class,
                "storage_target": "session_draft",
                "next_action": "可在最终审阅前检查并编辑本阶段内容",
            },
        )
        commit_session(db)
        return baseline, "contract_fallback"


def stage_tool_result(status: str, detail: str, run: Any, session: Any) -> dict[str, Any]:
    return {
        "tool": "generate_novel_creation_stage",
        "status": status,
        "detail": detail,
        "data": {"run": serialize_run(run), "session": serialize_session(session)},
    }
