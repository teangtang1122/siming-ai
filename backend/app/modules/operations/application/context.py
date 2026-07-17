"""Request-local operation binding shared by model executors."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TypeVar

T = TypeVar("T")
_CURRENT_OPERATION_ID: ContextVar[str | None] = ContextVar("siming_operation_id", default=None)


def current_operation_id() -> str | None:
    return _CURRENT_OPERATION_ID.get()


@contextmanager
def activate_operation(operation_id: str | None) -> Iterator[None]:
    token = _CURRENT_OPERATION_ID.set(operation_id or None)
    try:
        yield
    finally:
        _CURRENT_OPERATION_ID.reset(token)


async def iterate_with_operation(
    operation_id: str | None,
    source: AsyncIterator[T],
) -> AsyncIterator[T]:
    with activate_operation(operation_id):
        async for item in source:
            yield item
