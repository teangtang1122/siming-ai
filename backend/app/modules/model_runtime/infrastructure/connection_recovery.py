"""One gateway-owned retry, exclusively for failed TCP/proxy/TLS establishment."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import suppress
from typing import TypeVar

from app.core.exceptions import LLMConnectionError, LLMTimeoutError

logger = logging.getLogger(__name__)
_CONNECT_ERRORS = (LLMConnectionError, LLMTimeoutError)
T = TypeVar("T")


def _may_recover(
    error: LLMConnectionError | LLMTimeoutError, attempt: int, produced: bool = False
) -> bool:
    if error.phase != "connect" or not error.request_not_sent:
        return False
    if attempt or produced:
        error.connection_recovery_exhausted = True
        return False
    logger.info("Model connection failed before request delivery; retrying once")
    return True


async def call_with_connection_recovery(factory: Callable[[], Awaitable[T]]) -> T:
    for attempt in range(2):
        try:
            return await factory()
        except _CONNECT_ERRORS as error:
            if not _may_recover(error, attempt):
                raise
        # Leave the exception handler before retrying so SDK causes stay accurate.
        await asyncio.sleep(0.7)


async def stream_with_connection_recovery(
    factory: Callable[[], AsyncGenerator[T, None]],
) -> AsyncGenerator[T, None]:
    for attempt in range(2):
        stream = factory()
        produced = False
        try:
            async for item in stream:
                produced = True
                yield item
            return
        except _CONNECT_ERRORS as error:
            if not _may_recover(error, attempt, produced):
                raise
        finally:
            with suppress(Exception):
                await stream.aclose()
        await asyncio.sleep(0.7)
