"""SQLAlchemy query and command adapter for the operation center."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.architecture.uow import SqlAlchemyUnitOfWork
from app.database.session import SessionLocal
from app.modules.operations.application.ports import OperationServicePort
from app.modules.operations.domain.state import ACTIVE_STATUSES, TERMINAL_STATUSES

from .models import OperationRun
from .runtime import invoke_operation_action, serialize_operation, update_operation


class SqlAlchemyOperationService(OperationServicePort):
    def list(self, *, active_only: bool, limit: int) -> list[dict]:
        with SessionLocal() as db:
            query = db.query(OperationRun)
            if active_only:
                query = query.filter(OperationRun.status.in_(list(ACTIVE_STATUSES)))
            rows = (
                query.order_by(
                    OperationRun.updated_at.desc(),
                    OperationRun.created_at.desc(),
                )
                .limit(limit)
                .all()
            )
            return [serialize_operation(row) for row in rows]

    def get(self, operation_id: str, *, include_events: bool = True) -> dict | None:
        with SessionLocal() as db:
            operation = db.query(OperationRun).filter(OperationRun.id == operation_id).first()
            return (
                serialize_operation(operation, include_events=include_events) if operation else None
            )

    async def stream(
        self,
        operation_id: str,
        *,
        after: int = 0,
    ) -> AsyncIterator[tuple[str, dict]]:
        sent = max(0, after)
        while True:
            with SessionLocal() as db:
                operation = db.query(OperationRun).filter(OperationRun.id == operation_id).first()
                if not operation:
                    yield "error", {"message": "任务不存在"}
                    return
                for event in operation.events or []:
                    if event.sequence <= sent:
                        continue
                    sent = event.sequence
                    yield (
                        "operation_event",
                        {
                            "sequence": event.sequence,
                            "event_type": event.event_type,
                            "status": event.status,
                            "message": event.message,
                            "payload": event.payload_json,
                        },
                    )
                snapshot = serialize_operation(operation)
                yield "heartbeat", snapshot
                if operation.status in TERMINAL_STATUSES:
                    yield "done", snapshot
                    return
            await asyncio.sleep(2)

    async def action(self, operation_id: str, action: str) -> tuple[str, dict | None]:
        with SessionLocal() as db:
            operation = db.query(OperationRun).filter(OperationRun.id == operation_id).first()
            if not operation:
                return "not_found", None
            allowed = {
                "pause": operation.can_pause,
                "continue": operation.can_pause,
                "cancel": operation.can_cancel,
                "retry_current_unit": operation.can_retry,
            }.get(action, False)
            if not allowed:
                return "unsupported", None

        if not await invoke_operation_action(operation_id, action):
            return "unsupported", None

        with SqlAlchemyUnitOfWork(SessionLocal) as uow:
            operation = (
                uow.session.query(OperationRun).filter(OperationRun.id == operation_id).first()
            )
            if not operation:
                return "not_found", None
            self._project_action(uow.session, operation, action)
            uow.commit()
            return "ok", serialize_operation(operation, include_events=True)

    @staticmethod
    def _project_action(db, operation: OperationRun, action: str) -> None:
        if action == "cancel":
            update_operation(
                db,
                operation,
                status="cancelled",
                message="任务已取消",
                event_type="cancelled",
                attention={},
                result={"summary": "任务已取消", "completed": [], "incomplete": ["任务未完成"]},
                outcome="cancelled",
            )
        elif action == "pause":
            update_operation(
                db, operation, status="paused", message="任务已暂停", event_type="paused"
            )
        elif action == "continue":
            update_operation(
                db,
                operation,
                status="running",
                health_status="active",
                message="任务继续运行",
                event_type="continued",
                attention={},
                result={},
            )
        else:
            update_operation(
                db,
                operation,
                status="running",
                health_status="active",
                message="正在重试当前单元",
                event_type="retrying",
                attention={},
                result={},
            )
