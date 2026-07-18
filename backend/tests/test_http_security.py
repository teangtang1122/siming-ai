"""Security regressions for the loopback desktop HTTP boundary."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.bootstrap.app_factory import (
    _register_frontend,
    create_app,
    resolve_frontend_file,
)


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
