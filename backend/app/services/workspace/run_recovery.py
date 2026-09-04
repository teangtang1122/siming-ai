"""RunRecoveryService — retry failed steps, resume from step, resume run.

Provides three layers:
1. Retry history: preserve original failed step, create new retry step
2. Resume: retry a step and continue with downstream failed steps
3. Idempotency: prevent duplicate writes on retry
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session

from ...database.models import AssistantRun, AssistantRunStep
from .assistant_public_errors import safe_tool_execution_failure
from .executor import execute_workspace_action
from .idempotency import generate_idempotency_key
from .run_log import finish_run_step, mark_assistant_run, start_run_step
from .run_step_payloads import DIRECT_MCP_RETRY_BLOCK_REASON, deserialize_step_request

logger = logging.getLogger(__name__)

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
    if original.direct_mcp_call_key:
        raise ValueError(DIRECT_MCP_RETRY_BLOCK_REASON)

    # Concurrent retry guard: if already resolved, return the resolution
    if original.resolved_step_id:
        resolved = (
            db.query(AssistantRunStep)
            .filter(AssistantRunStep.id == original.resolved_step_id)
            .first()
        )
        if resolved:
            return _enriched_step_payload(resolved)

    if original.status not in {"error", "interrupted"}:
        raise ValueError("只能重试失败或已中断的步骤")

    run = db.query(AssistantRun).filter(AssistantRun.id == run_id).first()
    if not run:
        raise ValueError("任务不存在")

    # Tool execution is only safe when the exact argument object is available.
    # Historical values that were hard-truncated are rejected before
    # idempotency calculation or any business tool can run.
    args = deserialize_step_request(original.request_json)

    if not original.tool:
        raise ValueError("步骤缺少工具名称")

    # Count existing retries
    attempt_no = (
        db.query(AssistantRunStep).filter(AssistantRunStep.retry_of_step_id == original.id).count()
    ) + 1

    idem_key = generate_idempotency_key(db, original.tool, original.project_id, args)

    # Execute the tool
    action = {"tool": original.tool, "arguments": args}
    try:
        result = await execute_workspace_action(db, original.project_id, action)
    except Exception as exc:
        error_id = uuid.uuid4().hex
        logger.exception(
            "Workspace retry tool failed error_id=%s run=%s tool=%s type=%s",
            error_id,
            run_id,
            original.tool,
            type(exc).__name__,
        )
        result = {"tool": original.tool, **safe_tool_execution_failure(error_id)}

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
    if result.get("status") != "error" and run.status in {"error", "interrupted"}:
        remaining = (
            db.query(AssistantRunStep)
            .filter(
                AssistantRunStep.run_id == run.id,
                AssistantRunStep.status.in_(["error", "interrupted"]),
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

    # Return unresolved failed/interrupted steps after this one
    downstream = []
    for s in all_steps[target_idx + 1 :]:
        if s.status in {"error", "interrupted"} and not s.resolved_step_id:
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
    """Retry all unresolved failed/interrupted steps in execution order."""
    run = db.query(AssistantRun).filter(AssistantRun.id == run_id).first()
    if not run:
        raise ValueError("任务不存在")

    recoverable_steps = (
        db.query(AssistantRunStep)
        .filter(
            AssistantRunStep.run_id == run_id,
            AssistantRunStep.status.in_(["error", "interrupted"]),
            AssistantRunStep.resolved_step_id.is_(None),
        )
        .order_by(AssistantRunStep.iteration.asc(), AssistantRunStep.created_at.asc())
        .all()
    )

    results = []
    for step in recoverable_steps:
        r = await retry_step(db, run_id, step.id)
        results.append(r)

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enriched_step_payload(step: AssistantRunStep) -> dict:
    """Return the public step projection; exact replay data stays server-side."""
    from .run_log import step_payload as _sp

    return _sp(step)
