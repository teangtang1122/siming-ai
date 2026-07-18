"""RunRecoveryService — retry failed steps, resume from step, resume run.

Provides three layers:
1. Retry history: preserve original failed step, create new retry step
2. Resume: retry a step and continue with downstream failed steps
3. Idempotency: prevent duplicate writes on retry
"""
from __future__ import annotations

from app.architecture.uow import commit_session

import json
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import AssistantRun, AssistantRunStep, Chapter, Character, OutlineNode, WorldbuildingEntry, CharacterRelationship
from .executor import execute_workspace_action
from .idempotency import check_idempotency, generate_idempotency_key
from .run_log import finish_run_step, mark_assistant_run, start_run_step, step_payload


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------

async def retry_step(
    db: Session,
    run_id: str,
    step_id: str,
) -> dict:
    """Retry a failed step. Preserves the original; creates a new retry step."""
    original = (
        db.query(AssistantRunStep)
        .filter(AssistantRunStep.id == step_id, AssistantRunStep.run_id == run_id)
        .first()
    )
    if not original:
        raise ValueError("步骤不存在")

    # Concurrent retry guard: if already resolved, return the resolution
    if original.resolved_step_id:
        resolved = db.query(AssistantRunStep).filter(AssistantRunStep.id == original.resolved_step_id).first()
        if resolved:
            return _enriched_step_payload(resolved)

    if original.status != "error":
        raise ValueError("只能重试失败的步骤")

    run = db.query(AssistantRun).filter(AssistantRun.id == run_id).first()
    if not run:
        raise ValueError("任务不存在")

    args = {}
    if original.request_json:
        try:
            args = json.loads(original.request_json)
        except Exception:
            raise ValueError("步骤请求参数解析失败")

    if not original.tool:
        raise ValueError("步骤缺少工具名称")

    # Count existing retries
    attempt_no = (
        db.query(AssistantRunStep)
        .filter(AssistantRunStep.retry_of_step_id == original.id)
        .count()
    ) + 1

    idem_key = generate_idempotency_key(db, original.tool, original.project_id, args)

    # Execute the tool
    action = {"tool": original.tool, "arguments": args}
    try:
        result = await execute_workspace_action(db, original.project_id, action)
    except Exception as exc:
        result = {"tool": original.tool, "status": "error", "detail": str(exc)}

    # Create a new retry step (preserves original)
    new_step = start_run_step(
        db,
        run,
        step_type=original.step_type or "tool",
        tool=original.tool,
        iteration=original.iteration,
        request=args,
        detail=f"重试 #{attempt_no}",
    )
    if new_step:
        new_step.retry_of_step_id = original.id
        new_step.attempt_no = attempt_no
        if idem_key:
            new_step.idempotency_key = idem_key
        commit_session(db)

    finish_run_step(
        db,
        new_step,
        status=str(result.get("status") or "ok"),
        result=result,
        detail=str(result.get("detail") or ""),
        error=str(result.get("detail") or "") if result.get("status") == "error" else None,
    )

    # Mark original as resolved if retry succeeded
    if result.get("status") != "error" and new_step:
        original.resolved_step_id = new_step.id
        commit_session(db)

    # Promote run status if all errors resolved
    if result.get("status") != "error" and run.status == "error":
        remaining = (
            db.query(AssistantRunStep)
            .filter(
                AssistantRunStep.run_id == run.id,
                AssistantRunStep.status == "error",
                AssistantRunStep.resolved_step_id.is_(None),
            )
            .count()
        )
        if remaining == 0:
            mark_assistant_run(db, run, status="completed", phase="completed")

    if new_step:
        db.refresh(new_step)
    return _enriched_step_payload(new_step) if new_step else result


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def resolve_downstream_steps(
    db: Session,
    run: AssistantRun,
    step: AssistantRunStep,
) -> list[AssistantRunStep]:
    """Find failed steps that come after the given step in execution order."""
    all_steps = (
        db.query(AssistantRunStep)
        .filter(AssistantRunStep.run_id == run.id)
        .order_by(AssistantRunStep.iteration.asc(), AssistantRunStep.created_at.asc())
        .all()
    )

    # Find position of the given step
    target_idx = None
    for i, s in enumerate(all_steps):
        if s.id == step.id:
            target_idx = i
            break

    if target_idx is None:
        return []

    # Return unresolved error steps after this one
    downstream = []
    for s in all_steps[target_idx + 1:]:
        if s.status == "error" and not s.resolved_step_id:
            downstream.append(s)

    return downstream


async def resume_from_step(
    db: Session,
    run_id: str,
    step_id: str,
) -> list[dict]:
    """Retry a step, then continue with downstream failed steps."""
    results = []

    # Retry the target step first
    retry_result = await retry_step(db, run_id, step_id)
    results.append(retry_result)

    # If retry failed, stop here
    if retry_result.get("status") == "error":
        return results

    # Find and retry downstream failed steps
    original = db.query(AssistantRunStep).filter(AssistantRunStep.id == step_id).first()
    if not original:
        return results

    run = db.query(AssistantRun).filter(AssistantRun.id == run_id).first()
    if not run:
        return results

    downstream = resolve_downstream_steps(db, run, original)
    for ds_step in downstream:
        ds_result = await retry_step(db, run_id, ds_step.id)
        results.append(ds_result)
        # Stop if a downstream step fails
        if ds_result.get("status") == "error":
            break

    return results


async def resume_run(
    db: Session,
    run_id: str,
) -> list[dict]:
    """Retry all unresolved error steps in a run, in execution order."""
    run = db.query(AssistantRun).filter(AssistantRun.id == run_id).first()
    if not run:
        raise ValueError("任务不存在")

    error_steps = (
        db.query(AssistantRunStep)
        .filter(
            AssistantRunStep.run_id == run_id,
            AssistantRunStep.status == "error",
            AssistantRunStep.resolved_step_id.is_(None),
        )
        .order_by(AssistantRunStep.iteration.asc(), AssistantRunStep.created_at.asc())
        .all()
    )

    results = []
    for step in error_steps:
        r = await retry_step(db, run_id, step.id)
        results.append(r)

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enriched_step_payload(step: AssistantRunStep) -> dict:
    """step_payload + request/result parsed from JSON."""
    from .run_log import step_payload as _sp
    payload = _sp(step)
    if step.request_json:
        try:
            payload["request"] = json.loads(step.request_json)
        except Exception:
            payload["request"] = step.request_json
    if step.result_json:
        try:
            payload["result"] = json.loads(step.result_json)
        except Exception:
            payload["result"] = step.result_json
    return payload
