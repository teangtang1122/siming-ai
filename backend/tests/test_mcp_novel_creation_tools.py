"""Tests for novel creation tools exposure through MCP."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.adapter import list_mcp_tools
from app.mcp.permissions import is_secret_tool
from app.services.workspace.registry import registry


class MCPNovelCreationToolsTest(unittest.TestCase):
    """Verify novel creation tools are exposed through MCP."""

    def test_readonly_tools_in_readonly_pack(self):
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertIn("start_novel_creation_session", names)
        self.assertIn("draft_novel_blueprint", names)
        self.assertIn("review_novel_blueprint", names)
        self.assertIn("get_novel_creation_session", names)

    def test_apply_in_project_management(self):
        tools = list_mcp_tools(permission_pack="project_management")
        names = {t.name for t in tools}
        self.assertIn("apply_novel_blueprint", names)
        self.assertIn("generate_novel_creation_stage", names)
        self.assertIn("submit_novel_creation_stage", names)

    def test_creation_session_tools_do_not_require_project_id(self):
        tools = list_mcp_tools(permission_pack="project_management")
        by_name = {tool.name: tool for tool in tools}
        for name in ("generate_novel_creation_stage", "submit_novel_creation_stage"):
            required = by_name[name].input_schema.get("required", [])
            self.assertNotIn("project_id", required)

    def test_apply_not_in_readonly(self):
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertNotIn("apply_novel_blueprint", names)

    def test_no_secret_tools_exposed(self):
        for pack in ["readonly_collaboration", "draft_generation", "project_writing", "project_management"]:
            tools = list_mcp_tools(permission_pack=pack)
            for t in tools:
                self.assertFalse(
                    is_secret_tool(t.name),
                    f"Secret tool {t.name} in pack {pack}",
                )

    def test_linter_passes(self):
        """All tools should have required metadata."""
        for name in registry.all_names():
            td = registry.get(name)
            if td:
                self.assertTrue(td.description, f"{name}: missing description")


if __name__ == "__main__":
    unittest.main()
