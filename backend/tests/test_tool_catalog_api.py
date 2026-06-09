"""Tests for tool catalog API router."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.routers.tools import router


class RouterTest(unittest.TestCase):
    """Verify router configuration."""

    def test_router_has_catalog_route(self):
        paths = [r.path for r in router.routes]
        self.assertTrue(any("catalog" in p for p in paths))

    def test_router_has_exposed_route(self):
        paths = [r.path for r in router.routes]
        self.assertTrue(any("exposed" in p for p in paths))

    def test_router_has_two_routes(self):
        self.assertEqual(len(router.routes), 2)


class ToolCatalogTest(unittest.TestCase):
    """Verify tool catalog returns correct data."""

    def test_catalog_returns_tools(self):
        from app.services.workspace.registry import registry
        tools = registry.list_for_frontend()
        self.assertGreater(len(tools), 10)

    def test_catalog_tool_has_required_fields(self):
        from app.services.workspace.registry import registry
        tools = registry.list_for_frontend()
        for tool in tools:
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("tool_type", tool)
            self.assertIn("mcp_permission_pack", tool)
            self.assertIn("expose_to_mcp", tool)


if __name__ == "__main__":
    unittest.main()
