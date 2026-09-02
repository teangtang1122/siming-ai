"""Centralized Python logging configuration for the Siming backend.

Every backend entry point that builds the FastAPI application
(``app.bootstrap.app_factory.create_app``) applies this layout, and the
application lifespan re-applies it once Uvicorn has finished its own default
``dictConfig`` so log lines land in the same console + file handlers no matter
which launcher (desktop ``launcher.py``, Docker ``uvicorn`` CLI, CLI scripts)
started the process.

Rules kept intentionally simple:

* Console (stderr) handler is always installed for non-pytest processes.
* A rotating file handler is added when file logging is enabled (see below).
* Only the ``app`` logger tree follows ``SIMING_LOG_LEVEL`` (default ``info``);
  third-party loggers stay at ``warning`` so HTTP/LLM client chatter does not
  drown the records that matter.
* Pytest processes never install handlers or write files; the test framework
  owns log capture.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import get_settings

_configured = False

_MAX_FILE_BYTES = 5 * 1024 * 1024
_FILE_BACKUPS = 3

_DATEFMT = "%Y-%m-%d %H:%M:%S"
_CONSOLE_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_FILE_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(threadName)s [%(name)s] %(message)s"

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _normalize_level(raw: str | None) -> int:
    name = (raw or "info").strip().upper()
    return getattr(logging, name) if name in _VALID_LEVELS else logging.INFO


def _in_pytest_mode() -> bool:
    return "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _siming_home() -> Path | None:
    """Return SIMING_HOME when the launcher/Docker explicitly configured it."""
    for key in ("SIMING_HOME", "MOSHU_HOME", "NOVEL_AGENT_HOME"):
        value = os.environ.get(key, "").strip()
        if value:
            try:
                return Path(value).expanduser().resolve()
            except OSError:
                return None
    return None


def _resolve_file_enabled(
    configured: str,
    *,
    home: Path | None,
    gateway_enabled: bool,
) -> bool:
    raw = (configured or "auto").strip().lower()
    if raw in _FALSE_VALUES:
        return False
    if raw in _TRUE_VALUES:
        return True
    # ``auto``: packaged desktop build, headless Gateway and desktop runs that
    # already prepared a data home write a file; bare source/CI servers only log
    # to stderr unless the operator explicitly enables file logging.
    if getattr(sys, "frozen", False):
        return True
    if gateway_enabled:
        return True
    return home is not None


def _log_file_path(home: Path | None) -> Path | None:
    if home is None:
        return None
    try:
        path = home / "logs" / "siming.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return None


def _remove_owned_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if str(getattr(handler, "name", "") or "").startswith("siming-"):
            root.removeHandler(handler)
            with suppress(Exception):
                handler.close()


def _console_available() -> bool:
    """Return True when a real stderr stream exists (windowed GUI builds do not)."""
    return getattr(sys, "stderr", None) is not None


def _clear_root_handlers() -> None:
    """Remove every root handler; non-test processes own the logging layout."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        with suppress(Exception):
            handler.close()


def _install_console() -> None:
    if not _console_available():
        return
    handler = logging.StreamHandler()
    handler.name = "siming-console"
    handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT, datefmt=_DATEFMT))
    logging.getLogger().addHandler(handler)


def _install_file(home: Path | None) -> None:
    path = _log_file_path(home)
    if path is None:
        return
    try:
        handler = RotatingFileHandler(
            path,
            maxBytes=_MAX_FILE_BYTES,
            backupCount=_FILE_BACKUPS,
            encoding="utf-8",
        )
    except OSError:
        return
    handler.name = "siming-file"
    handler.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_DATEFMT))
    logging.getLogger().addHandler(handler)


def _apply_levels(*, debug: bool, access_log_enabled: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    # The application tree follows SIMING_LOG_LEVEL.
    logging.getLogger("app").setLevel(logging.DEBUG if debug else logging.INFO)

    # Uvicorn installs its own handlers through dictConfig at startup; replace
    # that layout so everything flows through our console/file handlers and the
    # access log can be enabled without a second stderr copy.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(
        logging.INFO if access_log_enabled else logging.WARNING
    )

    # Migration progress is useful; ORM/HTTP client internals are not.
    logging.getLogger("alembic").setLevel(logging.DEBUG if debug else logging.INFO)
    for name in (
        "sqlalchemy.engine",
        "sqlalchemy.pool",
        "httpx",
        "httpcore",
        "openai",
        "anthropic",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def configure_logging(*, force: bool = False) -> None:
    """Apply the backend logging layout once per process (or on demand).

    ``force=True`` is used by the application lifespan to recover from
    Uvicorn's own default ``dictConfig`` without duplicating handlers.
    """
    global _configured
    if _configured and not force:
        return
    _configured = True

    settings = get_settings()
    level = _normalize_level(settings.log_level)
    home = _siming_home()

    _remove_owned_handlers()
    if _in_pytest_mode():
        # Tests own capture; keep thresholds meaningful for caplog but never
        # write files or claim the console.
        logging.getLogger("app").setLevel(level)
        return

    # Fully own the root logger layout: Uvicorn's CLI dictConfig may already
    # have attached its own handlers before the lifespan starts, and leaving
    # them in place would duplicate records on stderr.
    _clear_root_handlers()

    _install_console()
    if _resolve_file_enabled(
        settings.log_file,
        home=home,
        gateway_enabled=bool(settings.gateway_enabled),
    ):
        _install_file(home)

    _apply_levels(
        debug=level == logging.DEBUG,
        access_log_enabled=bool(settings.log_http_access),
    )


__all__ = ["configure_logging"]
