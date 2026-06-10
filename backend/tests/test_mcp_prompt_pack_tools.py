"""Tests for prompt pack tools exposure through MCP."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.adapter import list_mcp_tools
from app.mcp.permissions import is_secret_tool


class MCPPromptPackToolsTest(unittest.TestCase):
    """Verify prompt pack tools are exposed through MCP readonly pack."""

    def test_prompt_pack_tools_in_readonly(self):
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        required = {
            "list_prompt_packs",
            "get_prompt_pack",
            "get_tool_playbook",
            "get_quality_rubric",
        }
        missing = required - names
        self.assertEqual(missing, set(), f"Missing tools: {missing}")

    def test_no_secret_tools_exposed(self):
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        for t in tools:
            self.assertFalse(
                is_secret_tool(t.name),
                f"Secret tool exposed: {t.name}",
            )

    def test_novel_creation_tools_in_readonly(self):
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertIn("start_novel_creation_session", names)
        self.assertIn("draft_novel_blueprint", names)
        self.assertIn("review_novel_blueprint", names)

    def test_apply_blueprint_not_in_readonly(self):
        """apply_novel_blueprint is a write tool — should not be in readonly."""
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertNotIn("apply_novel_blueprint", names)

    def test_apply_blueprint_in_project_management(self):
        tools = list_mcp_tools(permission_pack="project_management")
        names = {t.name for t in tools}
        self.assertIn("apply_novel_blueprint", names)


if __name__ == "__main__":
    unittest.main()
