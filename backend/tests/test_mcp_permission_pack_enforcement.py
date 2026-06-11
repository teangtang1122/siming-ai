"""Tests for MCP permission pack enforcement."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.adapter import list_mcp_tools, is_tool_allowed
from app.services.workspace.registry import registry


class ListMcpToolsByPackTest(unittest.TestCase):
    """Verify list_mcp_tools filters by permission pack."""

    def test_readonly_pack_has_read_tools(self):
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertIn("list_projects", names)
        self.assertIn("search_chapters", names)
        self.assertIn("detect_character_changes", names)

    def test_project_scoped_tools_require_project_id_in_schema(self):
        tools = {tool.name: tool for tool in list_mcp_tools(permission_pack="readonly_collaboration")}
        self.assertIn("list_chapters", tools)
        self.assertIn("project_id", tools["list_chapters"].input_schema.get("required", []))

    def test_global_tools_do_not_require_project_id_in_schema(self):
        tools = {tool.name: tool for tool in list_mcp_tools(permission_pack="readonly_collaboration")}
        self.assertIn("list_projects", tools)
        self.assertNotIn("project_id", tools["list_projects"].input_schema.get("required", []))

    def test_readonly_pack_no_write_tools(self):
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertNotIn("create_chapter", names)
        self.assertNotIn("delete_project", names)

    def test_draft_pack_includes_generators(self):
        readonly = list_mcp_tools(permission_pack="readonly_collaboration")
        draft = list_mcp_tools(permission_pack="draft_generation")
        readonly_names = {t.name for t in readonly}
        draft_names = {t.name for t in draft}
        # Draft pack should include generators
        self.assertIn("chapter_writer", draft_names)
        # Draft pack should include all readonly tools
        self.assertTrue(readonly_names.issubset(draft_names))

    def test_project_writing_pack_includes_create(self):
        tools = list_mcp_tools(permission_pack="project_writing")
        names = {t.name for t in tools}
        self.assertIn("create_chapter", names)
        self.assertIn("update_chapter", names)

    def test_project_management_pack_includes_project_crud(self):
        tools = list_mcp_tools(permission_pack="project_management")
        names = {t.name for t in tools}
        self.assertIn("create_project", names)
        self.assertIn("create_scheduled_task", names)
        self.assertIn("import_file_as_project", names)
        self.assertIn("import_file_as_chapters", names)

    def test_trusted_pack_includes_destructive(self):
        tools = list_mcp_tools(permission_pack="trusted_local_maintenance")
        names = {t.name for t in tools}
        self.assertIn("delete_project", names)
        self.assertIn("delete_chapter", names)

    def test_no_secret_tools_in_any_pack(self):
        from app.mcp.permissions import is_secret_tool
        for pack in ["readonly_collaboration", "draft_generation", "project_writing",
                     "project_management", "trusted_local_maintenance"]:
            tools = list_mcp_tools(permission_pack=pack)
            for t in tools:
                self.assertFalse(
                    is_secret_tool(t.name),
                    f"Secret tool {t.name} in pack {pack}",
                )


class IsToolAllowedByPackTest(unittest.TestCase):
    """Verify is_tool_allowed with permission packs."""

    def test_read_tool_allowed_in_readonly(self):
        self.assertTrue(is_tool_allowed("list_projects", permission_pack="readonly_collaboration"))

    def test_write_tool_not_allowed_in_readonly(self):
        self.assertFalse(is_tool_allowed("create_chapter", permission_pack="readonly_collaboration"))

    def test_generator_allowed_in_draft(self):
        self.assertTrue(is_tool_allowed("chapter_writer", permission_pack="draft_generation"))

    def test_generator_not_allowed_in_readonly(self):
        self.assertFalse(is_tool_allowed("chapter_writer", permission_pack="readonly_collaboration"))

    def test_create_allowed_in_project_writing(self):
        self.assertTrue(is_tool_allowed("create_chapter", permission_pack="project_writing"))

    def test_delete_allowed_in_trusted(self):
        self.assertTrue(is_tool_allowed("delete_project", permission_pack="trusted_local_maintenance"))

    def test_delete_not_allowed_in_project_writing(self):
        self.assertFalse(is_tool_allowed("delete_project", permission_pack="project_writing"))

    def test_unknown_tool_not_allowed(self):
        self.assertFalse(is_tool_allowed("nonexistent_tool", permission_pack="readonly_collaboration"))


class PackHierarchyTest(unittest.TestCase):
    """Verify pack hierarchy — higher packs include lower packs."""

    def test_draft_includes_readonly(self):
        readonly = {t.name for t in list_mcp_tools(permission_pack="readonly_collaboration")}
        draft = {t.name for t in list_mcp_tools(permission_pack="draft_generation")}
        self.assertTrue(readonly.issubset(draft))

    def test_writing_includes_draft(self):
        draft = {t.name for t in list_mcp_tools(permission_pack="draft_generation")}
        writing = {t.name for t in list_mcp_tools(permission_pack="project_writing")}
        self.assertTrue(draft.issubset(writing))

    def test_management_includes_writing(self):
        writing = {t.name for t in list_mcp_tools(permission_pack="project_writing")}
        management = {t.name for t in list_mcp_tools(permission_pack="project_management")}
        self.assertTrue(writing.issubset(management))


if __name__ == "__main__":
    unittest.main()
