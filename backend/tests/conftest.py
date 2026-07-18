"""Shared test configuration — mock heavy external deps before any app import."""
import importlib
import os
import sys
from unittest.mock import MagicMock

os.environ.setdefault("MOSHU_DISABLE_AUTO_MCP_SETUP", "1")


def _try_import(name: str) -> bool:
    """Return True if the module can be imported for real."""
    try:
        importlib.import_module(name)
        return True
    except (ImportError, ModuleNotFoundError):
        return False


def _make_mock_modules():
    """Build mock modules only for packages that are NOT installed."""
    mocks = {}

    # Only mock fastapi if not installed
    if not _try_import("fastapi"):
        _fastapi = MagicMock()
        _fastapi.__path__ = []
        _fastapi.__spec__ = MagicMock()
        _fastapi.exceptions = MagicMock()
        _fastapi.responses = MagicMock()
        _fastapi.routing = MagicMock()
        _fastapi.testclient = MagicMock()
        _fastapi.testclient.TestClient = MagicMock()
        _fastapi.staticfiles = MagicMock()
        _fastapi.middleware = MagicMock()
        _fastapi.middleware.cors = MagicMock()
        mocks.update({
            "fastapi": _fastapi,
            "fastapi.exceptions": _fastapi.exceptions,
            "fastapi.responses": _fastapi.responses,
            "fastapi.routing": _fastapi.routing,
            "fastapi.testclient": _fastapi.testclient,
            "fastapi.staticfiles": _fastapi.staticfiles,
            "fastapi.middleware": _fastapi.middleware,
            "fastapi.middleware.cors": _fastapi.middleware.cors,
        })

    # Mock packages that are typically missing in test env
    _optional = [
        "cryptography", "cryptography.fernet",
        "httpx", "openai", "anthropic",
        "google", "google.generativeai",
        "tiktoken", "uvicorn",
        "ddgs", "duckduckgo_search",
        "requests", "aiohttp", "bs4",
    ]
    for name in _optional:
        if not _try_import(name):
            mocks[name] = MagicMock()

    return mocks


_MOCK_MODULES = _make_mock_modules()

for name, mock in _MOCK_MODULES.items():
    if name not in sys.modules:
        sys.modules[name] = mock


def pytest_configure():
    """Compose the same application ports used by the production app factory."""

    from app.bootstrap.composition import configure_application_services

    configure_application_services()
