"""Tests for permission pack classification of all registered tools."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.workspace.registry import registry
from app.mcp.permissions import is_secret_tool


class EveryToolClassifiedTest(unittest.TestCase):
    """Verify every registered tool has a permission classification."""

    def test_all_tools_have_permission_tags(self):
        for name in registry.all_names():
            td = registry.get(name)
            self.assertIsNotNone(td, f"Tool not found: {name}")
            self.assertIsInstance(td.permission_tags, set, f"{name}: permission_tags not a set")

    def test_all_tools_have_risk_level(self):
        valid_levels = {"safe", "low", "medium", "high", "destructive"}
        for name in registry.all_names():
            td = registry.get(name)
            self.assertIn(td.risk_level, valid_levels, f"{name}: invalid risk_level {td.risk_level}")

    def test_all_tools_have_writes_project_data(self):
        for name in registry.all_names():
            td = registry.get(name)
            self.assertIsInstance(td.writes_project_data, bool, f"{name}: writes_project_data not bool")


class ReadonlyCollaborationPackTest(unittest.TestCase):
    """Verify readonly_collaboration pack contains correct tools."""

    def test_read_tools_in_readonly_pack(self):
        read_tools = ["list_projects", "get_project_info", "search_chapters",
                      "search_characters", "search_worldbuilding", "search_outline"]
        for name in read_tools:
            td = registry.get(name)
            self.assertIsNotNone(td, f"Tool not found: {name}")
            pack = registry._derive_mcp_pack(td)
            self.assertEqual(pack, "readonly_collaboration", f"{name}: pack is {pack}")

    def test_analysis_tools_in_readonly_pack(self):
        analysis_tools = ["preview_writing_context", "detect_forbidden_patterns"]
        for name in analysis_tools:
            td = registry.get(name)
            self.assertIsNotNone(td)
            pack = registry._derive_mcp_pack(td)
            self.assertEqual(pack, "readonly_collaboration", f"{name}: pack is {pack}")


class InternalLLMPackTest(unittest.TestCase):
    """Verify internal_llm pack contains model-backed tools."""

    def test_generator_tools_in_internal_llm_pack(self):
        generator_tools = ["chapter_writer", "outline_writer", "character_writer",
                           "worldbuilding_writer", "rewrite_text", "expand_text", "continue_text"]
        for name in generator_tools:
            td = registry.get(name)
            self.assertIsNotNone(td, f"Tool not found: {name}")
            pack = registry._derive_mcp_pack(td)
            self.assertEqual(pack, "internal_llm", f"{name}: pack is {pack}")

    def test_internal_model_jobs_in_internal_llm_pack(self):
        for name in ["start_cataloging_job", "resume_cataloging_job", "start_deconstruct_job"]:
            td = registry.get(name)
            self.assertIsNotNone(td, f"Tool not found: {name}")
            pack = registry._derive_mcp_pack(td)
            self.assertEqual(pack, "internal_llm", f"{name}: pack is {pack}")


class ProjectWritingPackTest(unittest.TestCase):
    """Verify project_writing pack contains correct tools."""

    def test_create_update_tools_in_writing_pack(self):
        write_tools = ["create_chapter", "update_chapter", "create_character",
                       "update_character", "create_worldbuilding_entry", "update_worldbuilding_entry"]
        for name in write_tools:
            td = registry.get(name)
            self.assertIsNotNone(td, f"Tool not found: {name}")
            pack = registry._derive_mcp_pack(td)
            self.assertEqual(pack, "project_writing", f"{name}: pack is {pack}")


class TrustedLocalMaintenancePackTest(unittest.TestCase):
    """Verify trusted_local_maintenance pack contains destructive tools."""

    def test_delete_tools_in_trusted_pack(self):
        delete_tools = ["delete_project", "delete_chapter", "delete_character",
                        "delete_outline_node", "delete_worldbuilding_entry"]
        for name in delete_tools:
            td = registry.get(name)
            self.assertIsNotNone(td, f"Tool not found: {name}")
            pack = registry._derive_mcp_pack(td)
            self.assertEqual(pack, "trusted_local_maintenance", f"{name}: pack is {pack}")


class ProjectManagementPackTest(unittest.TestCase):
    """Verify project_management pack contains management tools."""

    def test_management_tools_in_management_pack(self):
        mgmt_tools = ["create_project", "update_project_info",
                       "import_file_as_project", "import_file_as_chapters",
                       "create_scheduled_task", "update_scheduled_task",
                       "create_skill", "update_skill"]
        for name in mgmt_tools:
            td = registry.get(name)
            self.assertIsNotNone(td, f"Tool not found: {name}")
            pack = registry._derive_mcp_pack(td)
            self.assertEqual(pack, "project_management", f"{name}: pack is {pack}")


class NoSecretToolsExposedTest(unittest.TestCase):
    """Verify no secret-looking tools are exposed to MCP."""

    def test_no_secret_tools_in_any_pack(self):
        for name in registry.all_names():
            td = registry.get(name)
            if is_secret_tool(name):
                self.assertFalse(
                    td.expose_to_mcp,
                    f"Secret tool exposed to MCP: {name}",
                )

    def test_no_secret_tools_in_readonly(self):
        tools = registry.list_for_mcp(permission_pack="readonly_collaboration")
        for td in tools:
            self.assertFalse(
                is_secret_tool(td.name),
                f"Secret tool in readonly pack: {td.name}",
            )


class WritesProjectDataTest(unittest.TestCase):
    """Verify writes_project_data is correct for known tools."""

    def test_read_tools_dont_write(self):
        read_tools = ["list_projects", "search_chapters", "search_characters"]
        for name in read_tools:
            td = registry.get(name)
            self.assertFalse(td.writes_project_data, f"{name} should not write project data")

    def test_create_tools_write(self):
        write_tools = ["create_chapter", "create_character", "create_worldbuilding_entry"]
        for name in write_tools:
            td = registry.get(name)
            self.assertTrue(td.writes_project_data, f"{name} should write project data")

    def test_delete_tools_write(self):
        delete_tools = ["delete_chapter", "delete_character"]
        for name in delete_tools:
            td = registry.get(name)
            self.assertTrue(td.writes_project_data, f"{name} should write project data")


if __name__ == "__main__":
    unittest.main()
