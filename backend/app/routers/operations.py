"""Unified long-running operation APIs."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..core.response import ApiResponse
from ..database.models import OperationRun
from ..database.session import SessionLocal, get_db
from ..services.operation_runtime import (
    ACTIVE_STATUSES,
    add_operation_event,
    invoke_operation_action,
    serialize_operation,
    update_operation,
)


router = APIRouter(tags=["operations"])


@router.get("/operations")
def list_operations(
    active_only: bool = False,
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(OperationRun)
    if active_only:
        query = query.filter(OperationRun.status.in_(list(ACTIVE_STATUSES)))
    rows = query.order_by(OperationRun.updated_at.desc(), OperationRun.created_at.desc()).limit(limit).all()
    return ApiResponse.success(data={"items": [serialize_operation(item) for item in rows]})


@router.get("/operations/{operation_id}")
def get_operation(operation_id: str, db: Session = Depends(get_db)):
    operation = db.query(OperationRun).filter(OperationRun.id == operation_id).first()
    if not operation:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ApiResponse.success(data=serialize_operation(operation, include_events=True))


@router.get("/operations/{operation_id}/stream")
async def stream_operation(operation_id: str, after: int = 0):
    async def events():
        sent = max(0, after)
        while True:
            with SessionLocal() as db:
                operation = db.query(OperationRun).filter(OperationRun.id == operation_id).first()
                if not operation:
                    yield "event: error\ndata: " + json.dumps({"message": "任务不存在"}, ensure_ascii=False) + "\n\n"
                    return
                rows = list(operation.events or [])
                for event in rows:
                    if event.sequence <= sent:
                        continue
                    sent = event.sequence
                    payload = {
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "status": event.status,
                        "message": event.message,
                        "payload": event.payload_json,
                    }
                    yield f"event: operation_event\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                snapshot = serialize_operation(operation)
                yield f"event: heartbeat\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
                if operation.status in {"completed", "failed", "cancelled", "interrupted"}:
                    yield f"event: done\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
                    return
            await asyncio.sleep(2)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _action(operation_id: str, action: str, db: Session) -> ApiResponse:
    operation = db.query(OperationRun).filter(OperationRun.id == operation_id).first()
    if not operation:
        raise HTTPException(status_code=404, detail="任务不存在")
    allowed = {
        "pause": operation.can_pause,
        "continue": operation.can_pause,
        "cancel": operation.can_cancel,
        "retry_current_unit": operation.can_retry,
    }.get(action, False)
    if not allowed:
        raise HTTPException(status_code=409, detail="该任务当前不支持此操作，请返回原页面处理")
    handled = await invoke_operation_action(operation_id, action)
    if not handled:
        raise HTTPException(status_code=409, detail="该任务当前不支持此操作，请返回原页面处理")
    if action == "cancel":
        update_operation(db, operation, status="cancelled", message="任务已取消", event_type="cancelled")
    elif action == "pause":
        update_operation(db, operation, status="paused", message="任务已暂停", event_type="paused")
    elif action == "continue":
        update_operation(db, operation, status="running", health_status="active", message="任务继续运行", event_type="continued")
    else:
        update_operation(db, operation, status="running", health_status="active", message="正在重试当前单元", event_type="retrying")
    db.commit()
    return ApiResponse.success(data=serialize_operation(operation, include_events=True))


@router.post("/operations/{operation_id}/pause")
async def pause_operation(operation_id: str, db: Session = Depends(get_db)):
    return await _action(operation_id, "pause", db)


@router.post("/operations/{operation_id}/continue")
async def continue_operation(operation_id: str, db: Session = Depends(get_db)):
    return await _action(operation_id, "continue", db)


@router.post("/operations/{operation_id}/cancel")
async def cancel_operation(operation_id: str, db: Session = Depends(get_db)):
    return await _action(operation_id, "cancel", db)


@router.post("/operations/{operation_id}/retry-current-unit")
async def retry_operation_unit(operation_id: str, db: Session = Depends(get_db)):
    return await _action(operation_id, "retry_current_unit", db)
