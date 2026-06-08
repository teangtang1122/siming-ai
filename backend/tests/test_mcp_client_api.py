"""Tests for MCP client management API router."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.routers.mcp import _config_to_read, router
from app.database.models import McpServerConfig
from app.schemas.mcp import McpServerConfigRead


class ConfigToReadTest(unittest.TestCase):
    """Verify _config_to_read conversion."""

    def test_converts_model_to_schema(self):
        config = McpServerConfig()
        config.id = "test-id"
        config.project_id = "p1"
        config.name = "test-server"
        config.transport = "stdio"
        config.command = "python mcp.py"
        config.url = None
        config.enabled = True
        config.status = "disconnected"
        config.last_error = None

        from datetime import datetime
        config.created_at = datetime(2026, 6, 7)
        config.updated_at = None

        result = _config_to_read(config)
        self.assertIsInstance(result, McpServerConfigRead)
        self.assertEqual(result.id, "test-id")
        self.assertEqual(result.name, "test-server")
        self.assertEqual(result.transport, "stdio")
        self.assertTrue(result.enabled)


class RouterTest(unittest.TestCase):
    """Verify router configuration."""

    def test_router_has_prefix(self):
        self.assertIn("mcp-servers", router.prefix)

    def test_router_has_routes(self):
        routes = [r.path for r in router.routes]
        self.assertGreater(len(routes), 0)


if __name__ == "__main__":
    unittest.main()
