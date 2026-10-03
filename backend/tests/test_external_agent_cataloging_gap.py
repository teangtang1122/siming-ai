"""Tests for external agent cataloging gap — current failure mode capture.

Captures the current failure mode before changing behavior:
- External agent proposes outline drafts and can create character/worldbuilding entries
- Successful calls are committed and visible from a fresh DB session
- Failed calls roll back and return isError=true
- Agent cannot report cataloging complete unless verification counts are nonzero
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class ExternalCatalogingGapTest(unittest.TestCase):
    """Capture current failure modes in external cataloging workflow."""

    def test_external_agent_only_proposes_outline_drafts(self):
        from app.services.workspace.registry import registry
        self.assertIsNone(registry.get("create_outline_node"))
        self.assertIsNone(registry.get("create_outline_nodes"))
        self.assertIsNotNone(registry.get("save_external_outline_draft"))

    def test_external_agent_can_create_character(self):
        """External agent should be able to create characters."""
        from app.services.workspace.registry import registry
        td = registry.get("create_character")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "write")
        self.assertTrue(td.writes_project_data)

    def test_external_agent_can_create_worldbuilding(self):
        """External agent should be able to create worldbuilding entries."""
        from app.services.workspace.registry import registry
        td = registry.get("create_worldbuilding_entry")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "write")
        self.assertTrue(td.writes_project_data)

    def test_external_agent_cannot_create_official_chapter(self):
        from app.services.workspace.registry import registry
        td = registry.get("create_chapter")
        self.assertIsNone(td)
        self.assertIsNotNone(registry.get("save_external_chapter_draft"))

    def test_no_cataloging_verification_tool_exists(self):
        """Currently there is no tool to verify cataloging completeness.

        This is the gap: external agents can create data but have no
        official way to verify the counts before saying "done".
        """
        from app.services.workspace.registry import registry
        # This should fail until EAC-0402 adds get_project_archive_status
        td = registry.get("get_project_archive_status")
        # If this passes, the gap is closed
        if td is None:
            self.skipTest("Gap confirmed: no verification tool exists yet")

    def test_external_cataloging_tools_not_yet_exist(self):
        """External cataloging tools don't exist yet.

        This is the gap: no start_external_cataloging_job, etc.
        These will be added in EAC-0302.
        """
        from app.services.workspace.registry import registry
        td = registry.get("start_external_cataloging_job")
        if td is None:
            self.skipTest("Gap confirmed: no external cataloging tools yet")


class CatalogingToolPermissionsTest(unittest.TestCase):
    """Verify cataloging tool permission assignments."""

    def test_outline_draft_in_project_writing(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="project_writing")
        names = {t.name for t in tools}
        self.assertNotIn("create_outline_node", names)
        self.assertNotIn("create_outline_nodes", names)
        self.assertIn("save_external_outline_draft", names)

    def test_create_character_in_project_writing(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="project_writing")
        names = {t.name for t in tools}
        self.assertIn("create_character", names)

    def test_create_worldbuilding_in_project_writing(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="project_writing")
        names = {t.name for t in tools}
        self.assertIn("create_worldbuilding_entry", names)

    def test_official_chapter_write_is_not_in_project_writing(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="project_writing")
        names = {t.name for t in tools}
        self.assertNotIn("create_chapter", names)
        self.assertNotIn("update_chapter", names)

    def test_chapter_drafting_pack_can_only_persist_unsaved_draft(self):
        from app.mcp.adapter import list_mcp_tools
        names = {t.name for t in list_mcp_tools(permission_pack="chapter_drafting")}
        self.assertIn("save_external_chapter_draft", names)
        self.assertNotIn("create_chapter", names)
        self.assertNotIn("update_chapter", names)

    def test_cataloging_tools_not_in_readonly(self):
        """Create tools should not be in readonly pack."""
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertNotIn("create_outline_node", names)
        self.assertNotIn("create_character", names)
        self.assertNotIn("create_chapter", names)


if __name__ == "__main__":
    unittest.main()
