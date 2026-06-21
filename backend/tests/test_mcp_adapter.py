"""Tests for the MCP adapter — ToolRegistry to MCP tool conversion."""
import sys
import os
import unittest

# Ensure backend is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.adapter import list_mcp_tools, is_tool_allowed, get_tool_def
from app.mcp.schemas import tool_def_to_mcp_tool, make_text_result, make_json_result
from app.mcp.permissions import get_tier, is_secret_tool, filter_tools
from app.services.workspace.registry import registry


class McpToolListTest(unittest.TestCase):
    """Verify that MCP tools/list returns the correct readonly tools."""

    def test_readonly_tools_exposed(self):
        """The 8 required readonly tools must appear in the MCP tool list."""
        tools = list_mcp_tools(allowed_tiers={"readonly"})
        names = {t.name for t in tools}

        required = {
            "list_projects",
            "get_project_info",
            "search_chapters",
            "search_characters",
            "search_worldbuilding",
            "search_outline",
            "search_context",
            "preview_writing_context",
        }
        missing = required - names
        self.assertEqual(missing, set(), f"Missing required readonly tools: {missing}")

    def test_no_write_tools_exposed(self):
        """Write tools must NOT appear in readonly tool list."""
        tools = list_mcp_tools(allowed_tiers={"readonly"})
        names = {t.name for t in tools}

        write_patterns = [
            "create_project", "create_character", "create_chapter",
            "update_project_info", "update_character", "update_chapter",
            "delete_project", "delete_character", "delete_chapter",
            "merge_duplicate_characters",
            "import_text_as_chapters", "import_file_as_chapters", "import_file_as_project",
            "import_deconstruct_report",
            "start_cataloging_job", "start_deconstruct_job",
            "run_scheduled_task_now",
        ]
        exposed_writes = [w for w in write_patterns if w in names]
        self.assertEqual(exposed_writes, [], f"Write tools leaked into readonly list: {exposed_writes}")

    def test_no_generator_tools_exposed(self):
        """Generator tools must NOT appear in readonly tool list."""
        tools = list_mcp_tools(allowed_tiers={"readonly"})
        names = {t.name for t in tools}

        generator_tools = [
            "chapter_writer", "outline_writer", "character_writer",
            "worldbuilding_writer", "rewrite_text", "expand_text",
            "continue_text", "roleplay_character", "dialogue_battle",
        ]
        exposed_generators = [g for g in generator_tools if g in names]
        self.assertEqual(exposed_generators, [], f"Generator tools leaked: {exposed_generators}")

    def test_no_secret_tools_exposed(self):
        """Secret-related tools must never appear regardless of tier."""
        tools = list_mcp_tools(allowed_tiers={"readonly", "draft", "write_confirmed"})
        names = {t.name for t in tools}

        # Even if we allow all tiers, secret tools should be blocked.
        # We test the is_secret_tool function directly too.
        for name in names:
            self.assertFalse(
                is_secret_tool(name),
                f"Secret tool exposed: {name}",
            )

    def test_tool_count_sane(self):
        """Readonly tools should be a reasonable subset (not zero, not all)."""
        tools = list_mcp_tools(allowed_tiers={"readonly"})
        self.assertGreater(len(tools), 10, "Too few readonly tools")
        self.assertLess(len(tools), 80, "Too many readonly tools — filter may be broken")


class McpToolSchemaTest(unittest.TestCase):
    """Verify MCP tool schema conversion."""

    def test_mcp_tool_has_required_fields(self):
        tools = list_mcp_tools(allowed_tiers={"readonly"})
        for t in tools:
            self.assertIsInstance(t.name, str)
            self.assertTrue(t.name, f"Empty name")
            self.assertIsInstance(t.description, str)
            self.assertTrue(t.description, f"Empty description for {t.name}")
            self.assertIsInstance(t.input_schema, dict)
            self.assertIn("type", t.input_schema)
            self.assertEqual(t.input_schema["type"], "object")

    def test_mcp_tool_has_properties(self):
        tools = list_mcp_tools(allowed_tiers={"readonly"})
        for t in tools:
            self.assertIn("properties", t.input_schema, f"{t.name} missing properties")

    def test_mcp_tools_expose_project_id_override(self):
        tools = list_mcp_tools(permission_pack="project_management")
        by_name = {t.name: t for t in tools}
        self.assertIn("project_id", by_name["search_chapters"].input_schema["properties"])
        self.assertIn("project_id", by_name["create_project"].input_schema["properties"])

    def test_mcp_tools_expose_agent_run_context(self):
        tools = list_mcp_tools(permission_pack="project_management")
        by_name = {t.name: t for t in tools}
        self.assertIn("run_id", by_name["search_chapters"].input_schema["properties"])
        self.assertIn("run_id", by_name["report_agent_progress"].input_schema["properties"])


class PermissionFilterTest(unittest.TestCase):
    """Verify the permission filter logic."""

    def test_read_type_is_readonly(self):
        td = get_tool_def("list_projects")
        self.assertIsNotNone(td)
        self.assertEqual(get_tier(td), "readonly")

    def test_write_type_is_write_confirmed(self):
        td = get_tool_def("create_project")
        self.assertIsNotNone(td)
        self.assertEqual(get_tier(td), "write_confirmed")

    def test_generator_type_is_draft(self):
        td = get_tool_def("chapter_writer")
        self.assertIsNotNone(td)
        self.assertEqual(get_tier(td), "draft")

    def test_analysis_type_is_readonly(self):
        td = get_tool_def("detect_character_changes")
        self.assertIsNotNone(td)
        self.assertEqual(get_tier(td), "readonly")

    def test_web_type_is_readonly(self):
        td = get_tool_def("web_search")
        self.assertIsNotNone(td)
        self.assertEqual(get_tier(td), "readonly")

    def test_is_tool_allowed_readonly(self):
        self.assertTrue(is_tool_allowed("list_projects", allowed_tiers={"readonly"}))
        self.assertFalse(is_tool_allowed("create_project", allowed_tiers={"readonly"}))

    def test_is_tool_allowed_all_tiers(self):
        self.assertTrue(is_tool_allowed("list_projects", allowed_tiers={"readonly", "draft", "write_confirmed"}))
        self.assertTrue(is_tool_allowed("chapter_writer", allowed_tiers={"readonly", "draft", "write_confirmed"}))

    def test_unknown_tool_not_allowed(self):
        self.assertFalse(is_tool_allowed("nonexistent_tool", allowed_tiers={"readonly"}))


class SchemaHelperTest(unittest.TestCase):
    """Verify schema helper functions."""

    def test_tool_def_to_mcp_tool(self):
        mcp = tool_def_to_mcp_tool(
            name="test_tool",
            description="A test tool",
            input_schema={"query": {"type": "string"}},
            required=["query"],
        )
        self.assertEqual(mcp.name, "test_tool")
        self.assertEqual(mcp.description, "A test tool")
        self.assertIn("required", mcp.input_schema)
        self.assertEqual(mcp.input_schema["required"], ["query"])

    def test_make_text_result(self):
        r = make_text_result("hello")
        self.assertFalse(r.is_error)
        self.assertEqual(r.content[0]["type"], "text")
        self.assertEqual(r.content[0]["text"], "hello")

    def test_make_text_result_error(self):
        r = make_text_result("oops", is_error=True)
        self.assertTrue(r.is_error)

    def test_make_json_result(self):
        r = make_json_result({"key": "value"})
        self.assertFalse(r.is_error)
        self.assertIn('"key"', r.content[0]["text"])


if __name__ == "__main__":
    unittest.main()
