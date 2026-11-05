"""One transport timeout policy for model execution and connection checks."""
from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.core.exceptions import LLMConnectionError, LLMTimeoutError
from .request_delivery import request_not_sent


def model_http_timeout(default: httpx.Timeout) -> httpx.Timeout:
    return httpx.Timeout(**{**default.as_dict(), "connect": get_settings().api_connect_timeout_seconds})


def model_timeout_error(error: BaseException) -> LLMTimeoutError:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.TimeoutException):
            phase = next((name for kind, name in (
                (httpx.ConnectTimeout, "connect"), (httpx.ReadTimeout, "read"),
                (httpx.WriteTimeout, "write"), (httpx.PoolTimeout, "pool"),
            ) if isinstance(current, kind)), "response")
            try:
                seconds = current.request.extensions.get("timeout", {}).get(phase)
            except RuntimeError:  # Synthetic or third-party errors may omit the request.
                seconds = None
            return LLMTimeoutError(phase, seconds if isinstance(seconds, (int, float)) else None, request_not_sent=request_not_sent(current))
        current = current.__cause__ or current.__context__
    return LLMTimeoutError("response")


def model_connection_error(error: BaseException) -> LLMConnectionError:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for kind, phase in ((httpx.ConnectError, "connect"), (httpx.ReadError, "read"), (httpx.WriteError, "write")):
            if isinstance(current, kind):
                return LLMConnectionError(phase, request_not_sent=request_not_sent(current))
        current = current.__cause__ or current.__context__
    return LLMConnectionError("response")
