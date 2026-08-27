"""Security regressions for the loopback desktop HTTP boundary."""

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.bootstrap.app_factory import (
    _register_frontend,
    create_app,
    resolve_frontend_file,
)
from app.bootstrap.http_security import GatewayRequestLimitMiddleware


def test_limited_streaming_post_delegates_disconnect_after_replaying_body() -> None:
    observed: list[dict] = []

    async def downstream(scope, receive, send) -> None:
        observed.append(await receive())
        observed.append(await receive())

    incoming = iter([
        {"type": "http.request", "body": b"{}", "more_body": False},
        {"type": "http.disconnect"},
    ])

    async def receive() -> dict:
        return next(incoming)

    async def send(message: dict) -> None:
        return None

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/projects/project-1/ai/workspace-assistant/stream",
        "headers": [(b"content-length", b"2")],
        "client": ("127.0.0.1", 12345),
    }
    middleware = GatewayRequestLimitMiddleware(downstream, enabled=True)
    asyncio.run(middleware(scope, receive, send))

    assert observed == [
        {"type": "http.request", "body": b"{}", "more_body": False},
        {"type": "http.disconnect"},
    ]


def test_project_package_stream_is_bounded_without_content_length(monkeypatch) -> None:
    sent: list[dict] = []

    async def downstream(scope, receive, send) -> None:
        while True:
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body", False):
                return

    incoming = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]
    )

    async def receive() -> dict:
        return next(incoming)

    async def send(message: dict) -> None:
        sent.append(message)

    path = "/api/v1/projects/project-package/import"
    limits = dict(GatewayRequestLimitMiddleware.BODY_LIMITS)
    limits[path] = 4
    monkeypatch.setattr(GatewayRequestLimitMiddleware, "BODY_LIMITS", limits)
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"transfer-encoding", b"chunked")],
        "client": ("127.0.0.1", 12345),
    }

    asyncio.run(GatewayRequestLimitMiddleware(downstream, enabled=True)(scope, receive, send))

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413


def test_foreign_host_is_rejected() -> None:
    with TestClient(create_app(run_startup=False)) as client:
        response = client.get("/health", headers={"host": "malicious.example"})
    assert response.status_code == 400


def test_foreign_browser_origin_cannot_write_to_local_api() -> None:
    with TestClient(create_app(run_startup=False)) as client:
        blocked = client.post(
            "/api/v1/not-a-real-route",
            headers={"origin": "https://malicious.example"},
        )
        local = client.post(
            "/api/v1/not-a-real-route",
            headers={"origin": "http://127.0.0.1:8765"},
        )
    assert blocked.status_code == 403
    assert local.status_code != 403


def test_security_headers_are_present() -> None:
    with TestClient(create_app(run_startup=False)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_frontend_file_resolution_stays_inside_distribution(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("index", encoding="utf-8")
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")

    assert resolve_frontend_file(frontend, "index.html") == frontend / "index.html"
    assert resolve_frontend_file(frontend, "../private.txt") is None

    app = FastAPI()
    _register_frontend(app, frontend)
    with TestClient(app) as client:
        response = client.get("/%2e%2e/private.txt")
    assert response.status_code == 404
    assert response.text != "private"
