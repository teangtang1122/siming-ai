"""SQLAlchemy implementation of cross-module operation reporting."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import OperationRun
from .runtime import update_operation


def report_checkpoint(db: Session, operation_id: str | None, payload: dict) -> None:
    if not operation_id:
        return
    operation = db.query(OperationRun).filter(OperationRun.id == operation_id).first()
    if operation:
        update_operation(
            db,
            operation,
            event_type="checkpoint",
            payload=payload,
            checkpoint=True,
        )
