"""Security boundary for the loopback-only desktop HTTP server."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (
        b"content-security-policy",
        (
            b"default-src 'self'; script-src 'self'; "
            b"style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            b"font-src 'self' data:; connect-src 'self' "
            b"http://127.0.0.1:* http://localhost:* "
            b"ws://127.0.0.1:* ws://localhost:*; "
            b"frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        ),
    ),
)


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", ()):
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


def _is_loopback_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not hostname:
            return False
        if hostname == "localhost":
            return True
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


class LocalOriginGuardMiddleware:
    """Reject browser writes originating outside the local desktop boundary."""

    def __init__(self, app: ASGIApp, *, allowed_origins: Iterable[str] = ()) -> None:
        self.app = app
        self.allowed_origins = {origin.rstrip("/") for origin in allowed_origins if origin}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method", "GET").upper() in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        origin = _header(scope, b"origin").rstrip("/")
        if origin and origin not in self.allowed_origins and not _is_loopback_origin(origin):
            response = JSONResponse(
                status_code=403,
                content={
                    "code": 403,
                    "message": "Blocked a browser write request from outside this computer.",
                },
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """Attach stable browser hardening headers to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", ()))
                existing = {key.lower() for key, _ in headers}
                headers.extend(
                    (key, value) for key, value in SECURITY_HEADERS if key not in existing
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = ["LocalOriginGuardMiddleware", "SecurityHeadersMiddleware"]
