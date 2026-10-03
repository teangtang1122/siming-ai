"""Complete-plan diagnostics shared by staging, recovery and final application."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ...database.models import CatalogingCandidate, CatalogingChapterRun
from .candidate_io import candidate_payload
from .plan_contract import validate_plan_references


def inspect_complete_plan(db: Session, run: CatalogingChapterRun,
                          *, rows: list[CatalogingCandidate] | None = None) -> dict[str, Any]:
    """One validation report for submission, recovery and transactional application."""
    from ...modules.continuity.domain.candidate_contract import CANDIDATE_FIELDS, validate_candidate_fields
    from .candidate_validation import inspect_candidate_coverage, candidate_coverage_error_message
    from .character_targets import validate_character_state_target
    from .scene_contract import validate_scene_candidate

    if rows is None:
        rows = db.query(CatalogingCandidate).filter_by(chapter_run_id=run.id).order_by(
            CatalogingCandidate.sort_order, CatalogingCandidate.id,
        ).all()
    rows = [row for row in rows if row.status != "rejected"]
    summaries = [row for row in rows if row.item_type == "chapter_summary"]
    plan = candidate_payload(summaries[0]) if len(summaries) == 1 else None
    errors, missing = [], []
    if plan is None:
        missing.append("每章必须且只能有一个当前 chapter_summary 建档计划")
    references_ready = False
    if plan is not None:
        try:
            validate_candidate_fields("chapter_summary", plan)
            validate_plan_references(db, run.project_id, run,
                                     {"item_type": "chapter_summary", "payload": plan})
            references_ready = True
        except ValueError:
            # Report the summary error once, against its candidate ID below.
            pass
    for row in rows:
        payload = candidate_payload(row)
        normalized = {"item_type": row.item_type, "payload": payload}
        try:
            if row.item_type not in CANDIDATE_FIELDS:
                raise ValueError(f"不支持的候选类型：{row.item_type}")
            validate_candidate_fields(row.item_type, payload)
            validate_scene_candidate(db, run, normalized)
            if row.status != "applied":
                validate_character_state_target(
                    db, run.project_id, row.item_type, payload,
                    chapter_content=(str(run.chapter.content or "")
                                     if run.chapter is not None else ""),
                    chapter_run_id=run.id,
                )
            if references_ready or row.item_type == "chapter_summary":
                validate_plan_references(db, run.project_id, run, normalized)
        except ValueError as exc:
            issue = {
                "kind": "retained_candidate_validation", "candidate_id": row.id,
                "item_type": row.item_type, "target": row.target_id or row.target_name,
                "message": str(exc), "path": list(getattr(exc, "path", ())),
                "requires_author_action": row.edited_payload is not None or row.status in {"edited", "approved", "applied"},
            }
            if hasattr(exc, "rule"):
                issue.update(rule=exc.rule, expected=exc.expected)
            errors.append(issue)
    if references_ready:
        for field, kind in (("character_bindings", "character_create"),
                            ("worldbuilding_bindings", "worldbuilding_create")):
            created = {value for row in rows if row.item_type == kind
                       if isinstance(value := candidate_payload(row).get("client_id"), str)}
            for binding in plan[field]:
                if binding["decision"] == "new" and binding["id"] not in created:
                    missing.append(f"新实体 {binding['name']} 缺少对应 {kind} 候选（client_id={binding['id']}）")
    coverage = inspect_candidate_coverage(rows, db=db, project_id=run.project_id)
    missing.extend(coverage.cli_parity_missing)
    if not coverage.is_complete and not coverage.cli_parity_missing:
        missing.append(candidate_coverage_error_message(coverage))
    missing.extend(f"候选 {issue['candidate_id']}（{issue['item_type']}）：{issue['message']}" for issue in errors)
    return {"candidate_errors": errors, "missing_required_items": list(dict.fromkeys(missing))}


def validate_complete_plan(db: Session, run: CatalogingChapterRun) -> None:
    report = inspect_complete_plan(db, run)
    if report["missing_required_items"]:
        raise ValueError("；".join(report["missing_required_items"]))
