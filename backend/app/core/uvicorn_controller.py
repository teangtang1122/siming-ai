"""Explicit lifecycle control for the embedded Uvicorn server."""

from __future__ import annotations

import threading
from typing import Any

import uvicorn


class UvicornServerController:
    """Start Uvicorn in a thread and stop it through its supported flags."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        log_level: str = "info",
        access_log: bool = False,
    ) -> None:
        self._config_options = {
            "host": host,
            "port": port,
            "log_level": log_level,
            "access_log": access_log,
        }
        self._state_lock = threading.Lock()
        self._stop_requested = False
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self, app: Any) -> bool:
        """Start the server unless shutdown was requested while the app imported."""

        with self._state_lock:
            if self._stop_requested:
                return False
            if self._thread and self._thread.is_alive():
                return True
            config = uvicorn.Config(app, log_config=None, **self._config_options)
            server = uvicorn.Server(config)
            thread = threading.Thread(
                target=server.run,
                name="siming-uvicorn",
                daemon=True,
            )
            self._server = server
            self._thread = thread
            thread.start()
        return True

    def stop(self, *, timeout: float = 20.0) -> bool:
        """Request graceful shutdown and wait for FastAPI lifespan cleanup."""

        with self._state_lock:
            self._stop_requested = True
            server = self._server
            thread = self._thread
            if server is not None:
                server.should_exit = True

        if thread is None:
            return True
        if threading.current_thread() is thread:
            return False

        thread.join(timeout=max(timeout, 0.0))
        if thread.is_alive() and server is not None:
            server.force_exit = True
            thread.join(timeout=3.0)
        return not thread.is_alive()


__all__ = ["UvicornServerController"]
