"""Tests for external MCP tool registration in workspace registry."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.mcp_client.registry import (
    external_tool_name,
    is_external_mcp_tool,
    parse_external_tool_name,
    register_external_tool,
    unregister_server_tools,
)
from app.services.workspace.registry import registry


class ExternalToolNameTest(unittest.TestCase):
    """Verify external tool name construction."""

    def test_basic_name(self):
        self.assertEqual(
            external_tool_name("myserver", "search"),
            "mcp.myserver.search",
        )

    def test_name_with_dots(self):
        self.assertEqual(
            external_tool_name("my.server", "tool.name"),
            "mcp.my.server.tool.name",
        )


class IsExternalMcpToolTest(unittest.TestCase):
    """Verify external MCP tool detection."""

    def test_external_tool_detected(self):
        self.assertTrue(is_external_mcp_tool("mcp.server.tool"))

    def test_internal_tool_not_detected(self):
        self.assertFalse(is_external_mcp_tool("list_projects"))
        self.assertFalse(is_external_mcp_tool("search_chapters"))


class ParseExternalToolNameTest(unittest.TestCase):
    """Verify external tool name parsing."""

    def test_parse_valid(self):
        result = parse_external_tool_name("mcp.myserver.search")
        self.assertEqual(result, ("myserver", "search"))

    def test_parse_with_dots(self):
        result = parse_external_tool_name("mcp.server.tool.name")
        self.assertEqual(result, ("server", "tool.name"))

    def test_parse_non_external_returns_none(self):
        self.assertIsNone(parse_external_tool_name("list_projects"))

    def test_parse_malformed_returns_none(self):
        self.assertIsNone(parse_external_tool_name("mcp.nosuffix"))


class RegisterExternalToolTest(unittest.TestCase):
    """Verify external tool registration."""

    def tearDown(self):
        # Clean up any registered external tools
        unregister_server_tools("test_server")

    def test_register_adds_to_registry(self):
        name = register_external_tool(
            "test_server", "search",
            description="Search external data",
            input_schema={"query": {"type": "string"}},
            required=["query"],
        )
        self.assertEqual(name, "mcp.test_server.search")
        td = registry.get(name)
        self.assertIsNotNone(td)
        self.assertIn("test_server", td.description)

    def test_register_default_tool_type_is_read(self):
        name = register_external_tool(
            "test_server", "tool1",
            description="A tool",
            input_schema={},
        )
        td = registry.get(name)
        self.assertEqual(td.tool_type, "read")

    def test_register_idempotent(self):
        name1 = register_external_tool(
            "test_server", "tool1",
            description="A tool",
            input_schema={},
        )
        name2 = register_external_tool(
            "test_server", "tool1",
            description="A tool updated",
            input_schema={},
        )
        self.assertEqual(name1, name2)
        # Should not create duplicate
        count = sum(1 for n in registry.all_names() if n == name1)
        self.assertEqual(count, 1)

    def test_handler_returns_stub(self):
        name = register_external_tool(
            "test_server", "stub_tool",
            description="Stub",
            input_schema={},
        )
        td = registry.get(name)
        self.assertIsNotNone(td.handler)
        result = td.handler(None, "p1", {})
        self.assertEqual(result["status"], "error")


class UnregisterServerToolsTest(unittest.TestCase):
    """Verify external tool unregistration."""

    def test_unregister_removes_tools(self):
        register_external_tool("del_server", "t1", description="T1", input_schema={})
        register_external_tool("del_server", "t2", description="T2", input_schema={})
        count = unregister_server_tools("del_server")
        self.assertEqual(count, 2)
        self.assertIsNone(registry.get("mcp.del_server.t1"))
        self.assertIsNone(registry.get("mcp.del_server.t2"))

    def test_unregister_nonexistent_returns_zero(self):
        count = unregister_server_tools("no_such_server")
        self.assertEqual(count, 0)

    def test_unregister_only_removes_target_server(self):
        register_external_tool("keep_server", "t1", description="T1", input_schema={})
        register_external_tool("del_server2", "t2", description="T2", input_schema={})
        unregister_server_tools("del_server2")
        self.assertIsNotNone(registry.get("mcp.keep_server.t1"))
        self.assertIsNone(registry.get("mcp.del_server2.t2"))
        # Cleanup
        unregister_server_tools("keep_server")


if __name__ == "__main__":
    unittest.main()
