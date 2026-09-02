"""Tests for graceful lifecycle ownership of the embedded API server."""

import threading
import time
import unittest
from unittest.mock import patch

from app.core.uvicorn_controller import UvicornServerController


class _FakeServer:
    instances = []

    def __init__(self, config):
        self.config = config
        self.should_exit = False
        self.force_exit = False
        self.started = threading.Event()
        self.__class__.instances.append(self)

    def run(self):
        self.started.set()
        while not self.should_exit and not self.force_exit:
            time.sleep(0.005)


class UvicornServerControllerTestCase(unittest.TestCase):
    def setUp(self):
        _FakeServer.instances.clear()

    def test_stop_requests_graceful_exit_and_joins_server_thread(self):
        app = object()
        with patch("app.core.uvicorn_controller.uvicorn.Config") as config, patch(
            "app.core.uvicorn_controller.uvicorn.Server", _FakeServer
        ):
            controller = UvicornServerController(host="127.0.0.1", port=9876)
            self.assertTrue(controller.start(app))
            server = _FakeServer.instances[0]
            self.assertTrue(server.started.wait(1.0))
            self.assertTrue(controller.is_running)

            self.assertTrue(controller.stop(timeout=1.0))

        self.assertTrue(server.should_exit)
        self.assertFalse(controller.is_running)
        config.assert_called_once_with(
            app,
            host="127.0.0.1",
            port=9876,
            log_level="info",
            access_log=False,
            log_config=None,
        )

    def test_close_during_app_import_prevents_late_server_start(self):
        with patch("app.core.uvicorn_controller.uvicorn.Config") as config:
            controller = UvicornServerController(host="127.0.0.1", port=9876)
            self.assertTrue(controller.stop(timeout=0.1))
            self.assertFalse(controller.start(object()))

        config.assert_not_called()


if __name__ == "__main__":
    unittest.main()
