"""Agent plan orchestration endpoints."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..services.agent.bridge import detect_and_stream_plan
from ..services.agent.orchestrator import PlanOrchestrator, _serialize_step
from ..services.agent.planner import (
    build_plan_from_intent,
    detect_intent,
    plan_cataloging_init,
    plan_fast_chapter,
    plan_quality_chapter,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class CreatePlanRequest(BaseModel):
    mode: str  # "fast" or "quality"
    outline_node_id: str | None = None
    chapter_number: int | None = None
    requirements: str = ""
    involved_characters: list[str] | None = None
    conversation_id: str | None = None
    assistant_run_id: str | None = None
    assistant_message_id: str | None = None
    model: str | None = None


def _plan_payload(plan: Any) -> dict[str, Any]:
    steps = []
    for s in sorted(plan.steps, key=lambda x: x.created_at):
        steps.append(_serialize_step(s))
    return {
        "id": plan.id,
        "project_id": plan.project_id,
        "conversation_id": plan.conversation_id,
        "assistant_run_id": plan.assistant_run_id,
        "assistant_message_id": plan.assistant_message_id,
        "name": plan.name,
        "status": plan.status,
        "model": plan.model,
        "error": plan.error,
        "steps": steps,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
        "completed_at": plan.completed_at.isoformat() if plan.completed_at else None,
    }


def _sse_event(payload: Any) -> str:
    if payload == "[DONE]":
        return "data: [DONE]\n\n"
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {data}\n\n"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/projects/{project_id}/ai/agent/plans")
async def create_plan(
    project_id: str,
    body: CreatePlanRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Create an execution plan without running it."""
    get_project_or_404(db, project_id)

    if body.mode == "fast":
        graph = plan_fast_chapter(
            outline_node_id=body.outline_node_id or "",
            requirements=body.requirements,
            involved_characters=body.involved_characters,
        )
    elif body.mode == "quality":
        graph = plan_quality_chapter(
            outline_node_id=body.outline_node_id or "",
            requirements=body.requirements,
            involved_characters=body.involved_characters,
        )
    else:
        raise ValidationError(f"不支持的计划模式: {body.mode}，可选 fast / quality")

    orchestrator = PlanOrchestrator(db, project_id)
    plan = orchestrator.create_plan(
        graph,
        conversation_id=body.conversation_id,
        assistant_run_id=body.assistant_run_id,
        assistant_message_id=body.assistant_message_id,
        model=body.model,
    )

    return ApiResponse.success(data=_plan_payload(plan))


@router.get("/projects/{project_id}/ai/agent/plans/{plan_id}")
async def get_plan(
    project_id: str,
    plan_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Get plan status and step details."""
    get_project_or_404(db, project_id)

    try:
        plan = PlanOrchestrator(db, project_id).get_plan(plan_id)
    except ValueError:
        raise NotFoundError("计划不存在")

    return ApiResponse.success(data=_plan_payload(plan))


@router.post("/projects/{project_id}/ai/agent/plans/{plan_id}/execute/stream")
async def execute_plan_stream(
    project_id: str,
    plan_id: str,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Execute a plan with SSE progress events."""
    get_project_or_404(db, project_id)

    async def event_generator():
        orchestrator = PlanOrchestrator(db, project_id)
        try:
            async for event in orchestrator.execute_plan(plan_id):
                yield _sse_event(event)
        except ValueError as exc:
            yield _sse_event({"type": "error", "detail": str(exc)})
        except Exception as exc:
            yield _sse_event({"type": "error", "detail": f"执行异常: {exc}"})
        finally:
            yield _sse_event("[DONE]")

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/projects/{project_id}/ai/agent/plans/{plan_id}/resume/stream")
async def resume_plan_stream(
    project_id: str,
    plan_id: str,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Resume all failed/blocked steps with SSE progress events."""
    get_project_or_404(db, project_id)

    async def event_generator():
        orchestrator = PlanOrchestrator(db, project_id)
        try:
            async for event in orchestrator.resume_plan(plan_id):
                yield _sse_event(event)
        except ValueError as exc:
            yield _sse_event({"type": "error", "detail": str(exc)})
        except Exception as exc:
            yield _sse_event({"type": "error", "detail": f"恢复异常: {exc}"})
        finally:
            yield _sse_event("[DONE]")

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/projects/{project_id}/ai/agent/plans/{plan_id}/steps/{step_key}/retry")
async def retry_step(
    project_id: str,
    plan_id: str,
    step_key: str,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Retry a single failed step."""
    get_project_or_404(db, project_id)

    orchestrator = PlanOrchestrator(db, project_id)
    try:
        result = await orchestrator.retry_step(plan_id, step_key)
    except ValueError as exc:
        raise ValidationError(str(exc))

    return ApiResponse.success(data=result)


@router.post("/projects/{project_id}/ai/agent/plans/{plan_id}/steps/{step_key}/resume-from/stream")
async def resume_from_step_stream(
    project_id: str,
    plan_id: str,
    step_key: str,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Resume execution from a specific step with SSE progress events."""
    get_project_or_404(db, project_id)

    async def event_generator():
        orchestrator = PlanOrchestrator(db, project_id)
        try:
            async for event in orchestrator.resume_from_step(plan_id, step_key):
                yield _sse_event(event)
        except ValueError as exc:
            yield _sse_event({"type": "error", "detail": str(exc)})
        except Exception as exc:
            yield _sse_event({"type": "error", "detail": f"恢复异常: {exc}"})
        finally:
            yield _sse_event("[DONE]")

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Unified plan-stream: detect intent → create plan → execute (SSE)
# ---------------------------------------------------------------------------

class PlanStreamRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    scope: str = "project"
    model: str | None = None
    assistant_mode: str = "fast"


@router.post("/projects/{project_id}/ai/agent/plan-stream")
async def plan_stream(
    project_id: str,
    body: PlanStreamRequest,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Detect intent from user message, create a plan, and execute it with SSE events.

    Falls back to returning an error event if no plan intent is detected.
    """
    get_project_or_404(db, project_id)

    async def event_generator():
        gen = await detect_and_stream_plan(
            db, project_id,
            message=body.message,
            conversation_id=body.conversation_id,
            scope=body.scope,
            model=body.model,
            assistant_mode=body.assistant_mode,
        )
        if gen is None:
            yield _sse_event({
                "type": "no_plan",
                "detail": "未检测到可执行计划的意图，请使用普通助手对话。",
            })
            yield _sse_event("[DONE]")
            return

        try:
            async for event in gen:
                yield event
        except Exception as exc:
            yield _sse_event({"type": "error", "detail": f"执行异常: {exc}"})
            yield _sse_event("[DONE]")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
