"""Tests for the centralized backend logging configuration.

These tests exercise ``app.core.logging_setup`` in isolation.  Inside a pytest
process ``configure_logging`` never installs handlers or writes files; tests
that want the real console/file layout opt out of the pytest guard with a
localized monkeypatch.
"""

from __future__ import annotations

import logging
import sys

import pytest

from app.core import logging_setup
from app.core.logging_setup import configure_logging


@pytest.fixture()
def clean_logging():
    """Reset module state and remove any siming-owned handlers between tests."""
    root = logging.getLogger()
    initial_level = root.level
    logging_setup._configured = False
    yield
    for handler in list(root.handlers):
        if str(getattr(handler, "name", "") or "").startswith("siming-"):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(initial_level)
    logging_setup._configured = False


def _owned_handler_names() -> set[str]:
    return {
        str(getattr(handler, "name", "") or "")
        for handler in logging.getLogger().handlers
        if str(getattr(handler, "name", "") or "").startswith("siming-")
    }


def test_pytest_process_never_installs_handlers(clean_logging):
    configure_logging()
    assert _owned_handler_names() == set()


def test_console_and_file_handlers_are_installed(clean_logging, tmp_path, monkeypatch):
    monkeypatch.setattr(logging_setup, "_in_pytest_mode", lambda: False)
    monkeypatch.setenv("SIMING_HOME", str(tmp_path))
    monkeypatch.setenv("SIMING_LOG_FILE", "1")

    configure_logging()
    assert _owned_handler_names() == {"siming-console", "siming-file"}

    # Idempotent: repeated calls (e.g. app factory per entry point) do not
    # duplicate handlers.
    configure_logging()
    matches = [
        handler
        for handler in logging.getLogger().handlers
        if str(getattr(handler, "name", "") or "") == "siming-file"
    ]
    assert len(matches) == 1

    logging.getLogger("app.siming.test").info("hello-siming-log")
    matches[0].flush()
    content = (tmp_path / "logs" / "siming.log").read_text(encoding="utf-8")
    assert "hello-siming-log" in content
    assert "siming-console" in _owned_handler_names()


def test_file_logging_auto_is_disabled_without_data_home(clean_logging, monkeypatch):
    monkeypatch.setattr(logging_setup, "_in_pytest_mode", lambda: False)
    monkeypatch.delenv("SIMING_HOME", raising=False)
    monkeypatch.delenv("MOSHU_HOME", raising=False)
    monkeypatch.delenv("NOVEL_AGENT_HOME", raising=False)
    monkeypatch.setenv("SIMING_LOG_FILE", "auto")

    configure_logging()
    assert _owned_handler_names() == {"siming-console"}


def test_file_logging_auto_is_enabled_when_home_is_configured(clean_logging, tmp_path, monkeypatch):
    monkeypatch.setattr(logging_setup, "_in_pytest_mode", lambda: False)
    monkeypatch.setenv("SIMING_HOME", str(tmp_path))
    monkeypatch.setenv("SIMING_LOG_FILE", "auto")

    configure_logging()
    assert _owned_handler_names() == {"siming-console", "siming-file"}


def test_settings_log_defaults(monkeypatch):
    from app.core.config import Settings

    for key in (
        "SIMING_LOG_LEVEL",
        "SIMING_LOG_FILE",
        "SIMING_LOG_HTTP_ACCESS",
        "LOG_LEVEL",
        "LOG_FILE",
        "LOG_HTTP_ACCESS",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.log_level == "info"
    assert settings.log_file == "auto"
    assert settings.log_http_access is False


def test_settings_log_env_overrides(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("SIMING_LOG_LEVEL", "debug")
    monkeypatch.setenv("SIMING_LOG_FILE", "1")
    monkeypatch.setenv("SIMING_LOG_HTTP_ACCESS", "true")
    settings = Settings(_env_file=None)
    assert settings.log_level == "debug"
    assert settings.log_file == "1"
    assert settings.log_http_access is True


def test_general_exception_handler_logs_traceback(caplog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.exceptions import general_exception_handler

    app = FastAPI()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom-detail")

    app.add_exception_handler(Exception, general_exception_handler)
    with caplog.at_level(logging.ERROR, logger="app.core.exceptions"):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/boom")
    assert response.status_code == 500
    matched = [
        record
        for record in caplog.records
        if record.name == "app.core.exceptions"
        and record.levelno >= logging.ERROR
        and "Unhandled exception" in record.getMessage()
    ]
    assert matched


def test_system_log_resolution_prefers_runtime_log(tmp_path, monkeypatch):
    from app.routers.config_storage import _system_log_path

    monkeypatch.setenv("SIMING_HOME", str(tmp_path))
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    launcher_log = log_dir / "launcher.log"
    launcher_log.write_text("launcher\n", encoding="utf-8")
    assert _system_log_path() == launcher_log

    siming_log = log_dir / "siming.log"
    siming_log.write_text("runtime\n", encoding="utf-8")
    assert _system_log_path() == siming_log


def test_console_skipped_when_stderr_unavailable(clean_logging, tmp_path, monkeypatch):
    monkeypatch.setattr(logging_setup, "_in_pytest_mode", lambda: False)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setenv("SIMING_HOME", str(tmp_path))
    monkeypatch.setenv("SIMING_LOG_FILE", "1")

    configure_logging()
    # Windowed GUI builds have no stderr: only the file handler is installed and
    # logging never falls into StreamHandler exception paths.
    assert _owned_handler_names() == {"siming-file"}


def test_force_configuration_owns_root_handlers(clean_logging, tmp_path, monkeypatch):
    from logging import StreamHandler

    monkeypatch.setattr(logging_setup, "_in_pytest_mode", lambda: False)
    monkeypatch.setenv("SIMING_HOME", str(tmp_path))
    monkeypatch.setenv("SIMING_LOG_FILE", "1")

    # Simulate a foreign handler left on the root logger (Uvicorn's default
    # dictConfig on the Docker CLI path does exactly this).
    root = logging.getLogger()
    foreign = StreamHandler()
    foreign.name = "foreign-default"
    root.addHandler(foreign)

    configure_logging(force=True)
    handler_names = {
        str(getattr(handler, "name", "") or "")
        for handler in root.handlers
    }
    assert "foreign-default" not in handler_names
    assert handler_names == {"siming-console", "siming-file"}

