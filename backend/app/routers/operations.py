"""HTTP and SSE adapters for the unified operation center."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..core.response import ApiResponse
from ..modules.operations.interfaces.dependencies import get_operation_service


router = APIRouter(tags=["operations"])


@router.get("/operations")
def list_operations(
    active_only: bool = False,
    limit: int = Query(30, ge=1, le=100),
):
    items = get_operation_service().list(active_only=active_only, limit=limit)
    return ApiResponse.success(data={"items": items})


@router.get("/operations/{operation_id}")
def get_operation(operation_id: str):
    operation = get_operation_service().get(operation_id, include_events=True)
    if not operation:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ApiResponse.success(data=operation)


@router.get("/operations/{operation_id}/stream")
async def stream_operation(operation_id: str, after: int = 0):
    async def events():
        async for event_name, payload in get_operation_service().stream(
            operation_id,
            after=after,
        ):
            yield f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _action(operation_id: str, action: str) -> ApiResponse:
    status, payload = await get_operation_service().action(operation_id, action)
    if status == "not_found":
        raise HTTPException(status_code=404, detail="任务不存在")
    if status != "ok":
        raise HTTPException(status_code=409, detail="该任务当前不支持此操作，请返回原页面处理")
    return ApiResponse.success(data=payload)


@router.post("/operations/{operation_id}/pause")
async def pause_operation(operation_id: str):
    return await _action(operation_id, "pause")


@router.post("/operations/{operation_id}/continue")
async def continue_operation(operation_id: str):
    return await _action(operation_id, "continue")


@router.post("/operations/{operation_id}/cancel")
async def cancel_operation(operation_id: str):
    return await _action(operation_id, "cancel")


@router.post("/operations/{operation_id}/retry-current-unit")
async def retry_operation_unit(operation_id: str):
    return await _action(operation_id, "retry_current_unit")
