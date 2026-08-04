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
        expected = {
            "apply_novel_blueprint",
            "generate_novel_creation_stage",
            "submit_novel_creation_stage",
            "patch_creation_session",
            "confirm_creation_artifact",
            "generate_creation_artifact",
            "refine_creation_artifact",
            "regenerate_creation_artifact",
            "cancel_creation_operation",
            "pause_creation_operation",
            "resume_creation_operation",
            "retry_creation_operation",
            "finalize_creation_session",
        }
        self.assertTrue(expected.issubset(names))

    def test_canonical_creation_reads_are_available_to_readonly_collaboration(self):
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {tool.name for tool in tools}
        expected = {
            "get_creation_session",
            "get_creation_snapshot",
            "get_creation_operation",
            "validate_creation_session",
        }
        self.assertTrue(expected.issubset(names))

    def test_creation_session_tools_do_not_require_project_id(self):
        tools = list_mcp_tools(permission_pack="project_management")
        by_name = {tool.name: tool for tool in tools}
        session_tools = {
            "patch_creation_session",
            "patch_creation_artifact",
            "patch_creation_entity",
            "confirm_creation_artifact",
            "generate_creation_artifact",
            "refine_creation_artifact",
            "regenerate_creation_artifact",
            "finalize_creation_session",
            "apply_creation_import",
        }
        for name in session_tools:
            self.assertIn(name, by_name)
            required = by_name[name].input_schema.get("required", [])
            self.assertNotIn("project_id", required)

    def test_creation_patch_schema_documents_actions_and_standard_json_patch(self):
        tools = list_mcp_tools(permission_pack="project_management")
        patch_tool = next(tool for tool in tools if tool.name == "patch_creation_artifact")
        operation = patch_tool.input_schema["properties"]["changes"]["items"]
        if "$ref" in operation:
            operation = patch_tool.input_schema["$defs"][operation["$ref"].rsplit("/", 1)[-1]]
        properties = operation["properties"]
        self.assertEqual(
            properties["action"]["anyOf"][0]["enum"],
            ["set", "replace", "append", "remove", "resize"],
        )
        self.assertEqual(properties["op"]["anyOf"][0]["enum"], ["add", "replace", "remove"])
        self.assertIn("JSON Pointer", properties["path"]["description"])

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
