"""Security boundary for the loopback-only desktop HTTP server."""

from __future__ import annotations

import ipaddress
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterable
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.routing import compile_path
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


def _cookie(scope: Scope, name: str) -> str:
    raw = _header(scope, b"cookie")
    if not raw:
        return ""
    parsed = SimpleCookie()
    try:
        parsed.load(raw)
    except Exception:
        return ""
    morsel = parsed.get(name)
    return morsel.value if morsel is not None else ""


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


def _is_same_host_origin(scope: Scope, origin: str) -> bool:
    """Accept a browser Origin only when it exactly matches the request Host."""

    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        request_host = _header(scope, b"host").strip().lower()
        return bool(request_host) and hmac_compare(parsed.netloc.lower(), request_host)
    except ValueError:
        return False


def hmac_compare(left: str, right: str) -> bool:
    # Keeping the comparison helper local avoids accidental partial/substring
    # checks if origin handling evolves later.
    import hmac

    return hmac.compare_digest(left, right)


def is_loopback_client(scope: Scope) -> bool:
    client = scope.get("client")
    host = str(client[0]).split("%", 1)[0] if client else ""
    if host == "testclient":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# Paired Android clients use the same authoring endpoints as the desktop UI.
# Keep this list in FastAPI/OpenAPI template form so contract tests can compare
# it directly with the published PC API instead of maintaining a second dialect.
REMOTE_ANDROID_AUTHORING_PATHS: dict[str, frozenset[str]] = {
    # Novel creation is a canonical PC workspace. These routes let a paired
    # phone resume that same session instead of maintaining an Android-only
    # wizard or a second persistence shape.
    "/api/v1/novel-creation/presets": frozenset({"GET"}),
    "/api/v1/novel-creation/sessions": frozenset({"GET"}),
    "/api/v1/novel-creation/start": frozenset({"POST"}),
    "/api/v1/novel-creation/apply": frozenset({"POST"}),
    "/api/v1/novel-creation/sessions/{session_id}": frozenset({"GET", "PATCH", "DELETE"}),
    "/api/v1/novel-creation/agent-turn": frozenset({"POST"}),
    "/api/v1/novel-creation/sessions/{session_id}/runs": frozenset({"POST"}),
    "/api/v1/novel-creation/runs/{run_id}": frozenset({"GET"}),
    "/api/v1/novel-creation/runs/{run_id}/stream": frozenset({"GET"}),
    "/api/v1/novel-creation/sessions/{session_id}/stages/{stage}/confirm": frozenset({"POST"}),
    "/api/v1/novel-creation/sessions/{session_id}/stages/{stage}": frozenset({"PATCH"}),
    "/api/v1/projects": frozenset({"GET", "POST"}),
    "/api/v1/projects/{project_id}": frozenset({"GET", "PUT"}),
    "/api/v1/projects/{project_id}/chapters": frozenset({"GET", "POST"}),
    "/api/v1/projects/{project_id}/chapters/{chapter_id}": frozenset(
        {"GET", "PUT", "DELETE"}
    ),
    "/api/v1/projects/{project_id}/chapters/{chapter_id}/de-ai-preview": frozenset(
        {"POST"}
    ),
    "/api/v1/projects/{project_id}/chapters/{chapter_id}/quality-score-preview": frozenset(
        {"POST"}
    ),
    "/api/v1/projects/{project_id}/chapters/{chapter_id}/snapshots": frozenset({"GET"}),
    "/api/v1/projects/{project_id}/chapters/{chapter_id}/snapshots/diff": frozenset(
        {"GET"}
    ),
    "/api/v1/projects/{project_id}/chapters/{chapter_id}/snapshots/{snapshot_id}": frozenset(
        {"GET"}
    ),
    "/api/v1/projects/{project_id}/chapters/{chapter_id}/restore/{snapshot_id}": frozenset(
        {"POST"}
    ),
    "/api/v1/projects/{project_id}/outline": frozenset({"GET", "POST"}),
    "/api/v1/projects/{project_id}/outline/reorder": frozenset({"PUT"}),
    "/api/v1/projects/{project_id}/outline/{node_id}": frozenset({"PUT", "DELETE"}),
    "/api/v1/projects/{project_id}/characters": frozenset({"GET", "POST"}),
    "/api/v1/projects/{project_id}/characters/relationships": frozenset({"GET"}),
    "/api/v1/projects/{project_id}/characters/{character_id}": frozenset(
        {"GET", "PUT", "DELETE"}
    ),
    "/api/v1/projects/{project_id}/characters/{character_id}/relationships": frozenset(
        {"PUT"}
    ),
    "/api/v1/projects/{project_id}/characters/{character_id}/versions": frozenset({"GET"}),
    "/api/v1/projects/{project_id}/characters/{character_id}/versions/{version_id}": frozenset(
        {"GET"}
    ),
    "/api/v1/projects/{project_id}/worldbuilding": frozenset({"GET", "POST"}),
    "/api/v1/projects/{project_id}/worldbuilding/{entry_id}": frozenset(
        {"PUT", "DELETE"}
    ),
    "/api/v1/projects/{project_id}/worldbuilding/{entry_id}/versions": frozenset({"GET"}),
    "/api/v1/projects/{project_id}/worldbuilding/{entry_id}/timeline": frozenset({"GET"}),
    "/api/v1/projects/{project_id}/creation-brief": frozenset({"GET", "PATCH"}),
    "/api/v1/projects/{project_id}/creation-brief/ensure": frozenset({"POST"}),
    "/api/v1/projects/{project_id}/stats/today": frozenset({"GET"}),
    "/api/v1/projects/{project_id}/stats/history": frozenset({"GET"}),
    "/api/v1/projects/{project_id}/stats/goal": frozenset({"PUT"}),
    "/api/v1/projects/{project_id}/narrative-governance/items": frozenset({"POST"}),
    "/api/v1/projects/{project_id}/ai/workspace-assistant/stream": frozenset(
        {"POST", "HEAD"}
    ),
}
REMOTE_ANDROID_AUTHORING_ROUTES = tuple(
    (template, compile_path(template)[0], methods)
    for template, methods in REMOTE_ANDROID_AUTHORING_PATHS.items()
)


class LocalOriginGuardMiddleware:
    """Reject browser writes originating outside the local desktop boundary."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_origins: Iterable[str] = (),
        allow_same_host: bool = False,
    ) -> None:
        self.app = app
        self.allowed_origins = {origin.rstrip("/") for origin in allowed_origins if origin}
        self.allow_same_host = allow_same_host

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method", "GET").upper() in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        origin = _header(scope, b"origin").rstrip("/")
        allowed_same_host = self.allow_same_host and _is_same_host_origin(scope, origin)
        if (
            origin
            and origin not in self.allowed_origins
            and not _is_loopback_origin(origin)
            and not allowed_same_host
        ):
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


class GatewayAuthenticationMiddleware:
    """Default-deny remote Gateway APIs while preserving local desktop access."""

    REMOTE_PROJECT_ASSISTANT = re.compile(
        r"^/api/v1/projects/(?P<project_id>[A-Za-z0-9._:-]{1,64})/"
        r"ai/workspace-assistant/stream$"
    )
    REMOTE_ANDROID_AUTHORING_PATHS = REMOTE_ANDROID_AUTHORING_PATHS
    REMOTE_ANDROID_AUTHORING_ROUTES = REMOTE_ANDROID_AUTHORING_ROUTES

    PUBLIC_API_PATHS = frozenset(
        {
            "/api/v1/runtime/capabilities",
            "/api/v1/pairing/complete",
            "/api/v1/auth/refresh",
            "/api/v1/auth/admin/login",
        }
    )
    PUBLIC_READ_PATHS = frozenset(
        {
            "/api/v1/config/launcher",
            "/api/v1/auth/admin/session",
        }
    )
    ADMIN_SESSION_COOKIE = "siming_gateway_session"

    def __init__(self, app: ASGIApp, *, enabled: bool) -> None:
        self.app = app
        self.enabled = enabled

    @classmethod
    def is_remote_android_authoring_path(cls, path: str) -> bool:
        return any(
            pattern.fullmatch(path) is not None
            for _template, pattern, _methods in cls.REMOTE_ANDROID_AUTHORING_ROUTES
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path") or "")
        method = scope.get("method", "GET").upper()
        is_api_path = path == "/api/v1" or path.startswith("/api/v1/")
        if (
            not self.enabled
            or scope["type"] != "http"
            or not is_api_path
            or path in self.PUBLIC_API_PATHS
            or (method in {"GET", "HEAD"} and path in self.PUBLIC_READ_PATHS)
            or is_loopback_client(scope)
            or method == "OPTIONS"
        ):
            await self.app(scope, receive, send)
            return

        authorization = _header(scope, b"authorization")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            token = _cookie(scope, self.ADMIN_SESSION_COOKIE)
        if not token:
            await self._reject(scope, receive, send)
            return

        from ..database.session import SessionLocal
        from ..modules.gateway.infrastructure.service import GatewayService

        db = SessionLocal()
        try:
            context = GatewayService(db).authenticate(token)
        except Exception as exc:
            from ..core.exceptions import UnauthorizedError

            if isinstance(exc, UnauthorizedError):
                await self._reject(scope, receive, send)
            else:
                db.rollback()
                await self._unavailable(scope, receive, send)
            return
        finally:
            db.close()

        state = scope.setdefault("state", {})
        state["gateway_device_id"] = context.device_id
        state["gateway_device_role"] = context.role
        state["gateway_device_platform"] = context.platform
        if not self._authorize_remote_path(scope, context):
            await self._hide_unpublished_api(scope, receive, send)
            return
        await self.app(scope, receive, send)

    @classmethod
    def _authorize_remote_path(cls, scope: Scope, context: Any) -> bool:
        """Expose Gateway plus canonical PC authoring APIs to paired phones.

        Android authoring routes are deliberately matched against explicit
        FastAPI path templates. Project-scoped calls additionally require that
        the owner has enabled that project for synchronization. Local-only
        configuration, filesystem, updater, CLI, MCP, and training endpoints
        remain hidden from bearer-token clients.
        """

        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "GET").upper()
        if context.role == "owner" and context.platform == "web":
            return not path.startswith("/api/v1/config/update/")
        if (
            path == "/api/v1/runtime/capabilities"
            or path == "/api/v1/devices"
            or path.startswith("/api/v1/devices/")
            or path.startswith("/api/v1/pairing/")
            or path == "/api/v1/auth/refresh"
            or path.startswith("/api/v1/sync/")
        ):
            return True

        if context.platform != "android":
            return False

        route_values: dict[str, str] | None = None
        matched_template = ""
        for template, pattern, allowed_methods in cls.REMOTE_ANDROID_AUTHORING_ROUTES:
            match = pattern.fullmatch(path)
            if match is None:
                continue
            effective_method = "GET" if method == "HEAD" and "GET" in allowed_methods else method
            if effective_method not in allowed_methods:
                return False
            route_values = match.groupdict()
            matched_template = template
            break
        if route_values is None:
            return False

        project_id = route_values.get("project_id")
        if project_id is None:
            return matched_template == "/api/v1/projects" or matched_template.startswith(
                "/api/v1/novel-creation/"
            )
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", project_id):
            return False

        from ..database.session import SessionLocal
        from ..modules.gateway.infrastructure.models import SyncProject

        db = SessionLocal()
        try:
            enabled = (
                db.query(SyncProject.project_id)
                .filter(
                    SyncProject.project_id == project_id,
                    SyncProject.status == "enabled",
                )
                .first()
            )
            return enabled is not None
        finally:
            db.close()

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
            content={
                "code": 401,
                "message": "设备授权无效或已过期，请重新连接 Gateway。",
                "data": None,
            },
        )
        await response(scope, receive, send)

    @staticmethod
    async def _unavailable(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=503,
            content={
                "code": 503,
                "message": "Gateway 暂时无法验证设备授权，请稍后重试。",
                "data": None,
            },
        )
        await response(scope, receive, send)

    @staticmethod
    async def _hide_unpublished_api(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=404,
            content={
                "code": 404,
                "message": "此接口未向远程设备开放，或作品尚未显式加入同步。",
                "data": None,
            },
        )
        await response(scope, receive, send)


class GatewayRequestLimitMiddleware:
    """Bound sensitive Gateway requests before JSON parsing or authentication."""

    BODY_LIMITS = {
        "/api/v1/pairing/start": 16 * 1024,
        "/api/v1/pairing/complete": 64 * 1024,
        "/api/v1/pairing/approve": 16 * 1024,
        "/api/v1/auth/refresh": 16 * 1024,
        "/api/v1/auth/admin/login": 16 * 1024,
        "/api/v1/sync/bootstrap": 128 * 1024,
        "/api/v1/sync/push": 8 * 1024 * 1024,
    }
    RATE_LIMITS = {
        "/api/v1/pairing/start": (12, 60.0),
        "/api/v1/pairing/complete": (20, 60.0),
        "/api/v1/pairing/approve": (20, 60.0),
        "/api/v1/auth/refresh": (30, 60.0),
        "/api/v1/auth/admin/login": (8, 60.0),
        "/api/v1/sync/push": (120, 60.0),
    }

    @classmethod
    def _body_limit(cls, path: str) -> int | None:
        exact = cls.BODY_LIMITS.get(path)
        if exact is not None:
            return exact
        if GatewayAuthenticationMiddleware.REMOTE_PROJECT_ASSISTANT.fullmatch(path):
            return 256 * 1024
        if GatewayAuthenticationMiddleware.is_remote_android_authoring_path(path):
            return 2 * 1024 * 1024
        return None

    @classmethod
    def _rate_limit(cls, path: str) -> tuple[int, float] | None:
        exact = cls.RATE_LIMITS.get(path)
        if exact is not None:
            return exact
        if GatewayAuthenticationMiddleware.REMOTE_PROJECT_ASSISTANT.fullmatch(path):
            return (12, 60.0)
        if path.startswith("/api/v1/novel-creation/") and (
            path.endswith("/agent-turn") or path.endswith("/runs")
        ):
            return (12, 60.0)
        return None

    def __init__(self, app: ASGIApp, *, enabled: bool) -> None:
        self.app = app
        self.enabled = enabled
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _rate_limited(self, scope: Scope, path: str) -> tuple[bool, int]:
        limit = self._rate_limit(path)
        if limit is None:
            return False, 0
        maximum, window = limit
        client = scope.get("client")
        host = str(client[0]).split("%", 1)[0] if client else "unknown"
        now = time.monotonic()
        with self._lock:
            events = self._events[(host, path)]
            while events and now - events[0] >= window:
                events.popleft()
            if len(events) >= maximum:
                retry_after = max(1, int(window - (now - events[0])))
                return True, retry_after
            events.append(now)
        return False, 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path") or "")
        maximum = self._body_limit(path)
        if not self.enabled or scope["type"] != "http" or maximum is None:
            await self.app(scope, receive, send)
            return

        limited, retry_after = self._rate_limited(scope, path)
        if limited:
            response = JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={
                    "code": 429,
                    "message": "请求过于频繁，请稍后再试。",
                    "data": None,
                },
            )
            await response(scope, receive, send)
            return

        content_length = _header(scope, b"content-length")
        if content_length.isdigit() and int(content_length) > maximum:
            await self._reject_large(scope, receive, send, maximum)
            return

        messages: list[Message] = []
        received = 0
        more_body = True
        while more_body:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                break
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > maximum:
                    await self._reject_large(scope, receive, send, maximum)
                    return
                more_body = bool(message.get("more_body", False))

        index = 0

        async def replay_receive() -> Message:
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            # StreamingResponse continuously waits for ``http.disconnect``.
            # Repeating an already-completed request here creates a zero-wait
            # loop that monopolizes the event loop for every limited SSE POST.
            # Once the buffered body has been replayed, resume the real ASGI
            # receive channel so disconnect listening blocks normally.
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject_large(
        scope: Scope,
        receive: Receive,
        send: Send,
        maximum: int,
    ) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "code": 413,
                "message": f"请求内容过大，当前接口上限为 {maximum // 1024} KiB。",
                "data": None,
            },
        )
        await response(scope, receive, send)


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
                if scope.get("scheme") == "https" and b"strict-transport-security" not in existing:
                    headers.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
                if str(scope.get("path") or "") in {
                    "/api/v1/pairing/start",
                    "/api/v1/pairing/complete",
                    "/api/v1/auth/refresh",
                    "/api/v1/auth/admin/login",
                    "/api/v1/auth/admin/session",
                    "/api/v1/auth/admin/logout",
                } and b"cache-control" not in existing:
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = [
    "GatewayAuthenticationMiddleware",
    "GatewayRequestLimitMiddleware",
    "LocalOriginGuardMiddleware",
    "SecurityHeadersMiddleware",
    "is_loopback_client",
]
