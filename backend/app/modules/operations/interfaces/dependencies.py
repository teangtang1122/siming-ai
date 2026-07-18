"""Composition-owned operation service dependency."""

from __future__ import annotations

from ..application.ports import OperationServicePort

_service: OperationServicePort | None = None


def configure_operation_service(service: OperationServicePort) -> None:
    global _service
    _service = service


def get_operation_service() -> OperationServicePort:
    if _service is None:
        raise RuntimeError("Operation service has not been configured")
    return _service
