"""Application ports for operation queries and controls."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class OperationServicePort(Protocol):
    def list(self, *, active_only: bool, limit: int) -> list[dict]: ...

    def get(self, operation_id: str, *, include_events: bool = True) -> dict | None: ...

    def stream(self, operation_id: str, *, after: int = 0) -> AsyncIterator[tuple[str, dict]]: ...

    async def action(self, operation_id: str, action: str) -> tuple[str, dict | None]: ...
