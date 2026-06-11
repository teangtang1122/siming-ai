"""Tests for ToolRegistry metadata contract — Phase 9 extensions."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.workspace.registry import registry, ToolDef


class ToolDefNewFieldsTest(unittest.TestCase):
    """Verify ToolDef has new Phase 9 fields."""

    def test_has_permission_tags(self):
        td = registry.get("list_projects")
        self.assertIsNotNone(td)
        self.assertIsInstance(td.permission_tags, set)

    def test_has_risk_level(self):
        td = registry.get("list_projects")
        self.assertIsNotNone(td)
        self.assertIn(td.risk_level, ("safe", "low", "medium", "high", "destructive"))

    def test_has_writes_project_data(self):
        td = registry.get("list_projects")
        self.assertIsNotNone(td)
        self.assertIsInstance(td.writes_project_data, bool)

    def test_has_expose_flags(self):
        td = registry.get("list_projects")
        self.assertIsNotNone(td)
        self.assertIsInstance(td.expose_to_internal_agent, bool)
        self.assertIsInstance(td.expose_to_scheduler, bool)
        self.assertIsInstance(td.expose_to_mcp, bool)

    def test_read_tool_defaults(self):
        td = registry.get("list_projects")
        self.assertEqual(td.tool_type, "read")
        self.assertFalse(td.writes_project_data)
        self.assertTrue(td.expose_to_internal_agent)
        self.assertTrue(td.expose_to_mcp)

    def test_write_tool_defaults(self):
        td = registry.get("create_project")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "write")
        # Default writes_project_data should be False for ToolDef default
        # but the actual tool should be True


class RegistryListMethodsTest(unittest.TestCase):
    """Verify new registry list methods."""

    def test_list_for_internal_agent(self):
        tools = registry.list_for_internal_agent()
        self.assertGreater(len(tools), 10)
        names = {td.name for td in tools}
        self.assertIn("list_projects", names)
        self.assertIn("search_chapters", names)

    def test_list_for_internal_agent_filters_type(self):
        tools = registry.list_for_internal_agent(tool_types={"read"})
        for td in tools:
            self.assertEqual(td.tool_type, "read")

    def test_list_for_scheduler(self):
        tools = registry.list_for_scheduler()
        self.assertGreater(len(tools), 5)

    def test_list_for_mcp_readonly(self):
        tools = registry.list_for_mcp(permission_pack="readonly_collaboration")
        self.assertGreater(len(tools), 5)
        for td in tools:
            self.assertTrue(td.expose_to_mcp)

    def test_list_for_mcp_draft(self):
        readonly = registry.list_for_mcp(permission_pack="readonly_collaboration")
        draft = registry.list_for_mcp(permission_pack="draft_generation")
        # Draft pack should include more tools than readonly
        self.assertGreaterEqual(len(draft), len(readonly))

    def test_list_for_frontend(self):
        tools = registry.list_for_frontend()
        self.assertGreater(len(tools), 10)
        for t in tools:
            self.assertIn("name", t)
            self.assertIn("description", t)
            self.assertIn("tool_type", t)
            self.assertIn("mcp_permission_pack", t)


class MCPPackDerivationTest(unittest.TestCase):
    """Verify MCP permission pack derivation."""

    def test_read_tools_are_readonly(self):
        td = registry.get("list_projects")
        pack = registry._derive_mcp_pack(td)
        self.assertEqual(pack, "readonly_collaboration")

    def test_api_free_analysis_tools_are_readonly(self):
        td = registry.get("preview_writing_context")
        pack = registry._derive_mcp_pack(td)
        self.assertEqual(pack, "readonly_collaboration")

    def test_model_backed_analysis_tools_are_internal_llm(self):
        td = registry.get("detect_character_changes")
        pack = registry._derive_mcp_pack(td)
        self.assertEqual(pack, "internal_llm")

    def test_generator_tools_are_internal_llm(self):
        td = registry.get("chapter_writer")
        pack = registry._derive_mcp_pack(td)
        self.assertEqual(pack, "internal_llm")

    def test_explicit_pack_override(self):
        td = ToolDef(
            name="test_tool",
            description="test",
            input_schema={},
            tool_type="read",
            mcp_permission_pack="project_writing",
        )
        pack = registry._derive_mcp_pack(td)
        self.assertEqual(pack, "project_writing")


if __name__ == "__main__":
    unittest.main()
