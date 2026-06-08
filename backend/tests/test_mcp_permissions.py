"""Tests for MCP permission filter — readonly enforcement."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.permissions import get_tier, is_secret_tool, is_allowed, filter_tools
from app.services.workspace.registry import registry, ToolDef


class ReadonlyAllowTest(unittest.TestCase):
    """Verify that read/analysis/web tools are allowed in readonly mode."""

    def test_read_tools_allowed(self):
        """All tool_type=read tools must be allowed in readonly mode."""
        read_tools = [
            "list_projects", "get_project_info", "get_export_word_count",
            "search_chapters", "search_characters", "search_worldbuilding",
            "search_outline", "search_outline_tree", "search_relationships",
            "search_context",
            "list_characters", "list_chapters", "list_worldbuilding",
            "list_cataloging_jobs", "get_cataloging_job",
            "list_cataloging_candidates", "list_cataloging_facts",
            "preview_deconstruct_source", "list_deconstruct_reports",
            "get_deconstruct_report",
            "list_skills", "list_skill_templates", "list_skill_tools",
            "list_skill_versions",
            "get_today_writing_stats", "get_writing_stats_history",
            "list_duplicate_characters",
            "list_memories",
        ]
        for name in read_tools:
            with self.subTest(name=name):
                td = registry.get(name)
                self.assertIsNotNone(td, f"Tool not found: {name}")
                self.assertTrue(
                    is_allowed(td, allowed_tiers={"readonly"}),
                    f"Read tool denied: {name} (tier={get_tier(td)})",
                )

    def test_analysis_tools_allowed(self):
        """All tool_type=analysis tools must be allowed in readonly mode."""
        analysis_tools = [
            "preview_import_splits", "preview_character_merge",
            "suggest_conflicts", "design_plot",
            "detect_character_changes", "detect_new_worldbuilding",
            "detect_worldbuilding_conflicts", "detect_forbidden_patterns",
            "preview_writing_context", "preview_rag_context",
            "explain_context_selection", "evaluate_chapter",
            "preview_skill_match",
        ]
        for name in analysis_tools:
            with self.subTest(name=name):
                td = registry.get(name)
                self.assertIsNotNone(td, f"Tool not found: {name}")
                self.assertTrue(
                    is_allowed(td, allowed_tiers={"readonly"}),
                    f"Analysis tool denied: {name} (tier={get_tier(td)})",
                )

    def test_web_tool_allowed(self):
        td = registry.get("web_search")
        self.assertIsNotNone(td)
        self.assertTrue(is_allowed(td, allowed_tiers={"readonly"}))

    def test_memory_read_tools_allowed(self):
        for name in ["recall", "list_memories"]:
            td = registry.get(name)
            self.assertIsNotNone(td)
            self.assertTrue(is_allowed(td, allowed_tiers={"readonly"}))


class WriteDenyTest(unittest.TestCase):
    """Verify that write tools are denied in readonly mode."""

    def test_create_tools_denied(self):
        create_tools = [
            "create_project", "create_character", "create_chapter",
            "create_outline_node", "create_worldbuilding_entry",
            "create_relationship", "create_scheduled_task", "create_skill",
        ]
        for name in create_tools:
            with self.subTest(name=name):
                td = registry.get(name)
                self.assertIsNotNone(td, f"Tool not found: {name}")
                self.assertFalse(
                    is_allowed(td, allowed_tiers={"readonly"}),
                    f"Create tool allowed: {name} (tier={get_tier(td)})",
                )

    def test_update_tools_denied(self):
        update_tools = [
            "update_project_info", "update_character", "update_chapter",
            "update_outline_node", "update_worldbuilding_entry",
            "update_relationship", "update_scheduled_task", "update_skill",
            "update_cataloging_candidate",
        ]
        for name in update_tools:
            with self.subTest(name=name):
                td = registry.get(name)
                self.assertIsNotNone(td, f"Tool not found: {name}")
                self.assertFalse(
                    is_allowed(td, allowed_tiers={"readonly"}),
                    f"Update tool allowed: {name} (tier={get_tier(td)})",
                )

    def test_delete_tools_denied(self):
        delete_tools = [
            "delete_project", "delete_character", "delete_chapter",
            "delete_outline_node", "delete_worldbuilding_entry",
            "delete_relationship", "delete_scheduled_task", "delete_skill",
        ]
        for name in delete_tools:
            with self.subTest(name=name):
                td = registry.get(name)
                self.assertIsNotNone(td, f"Tool not found: {name}")
                self.assertFalse(
                    is_allowed(td, allowed_tiers={"readonly"}),
                    f"Delete tool allowed: {name} (tier={get_tier(td)})",
                )

    def test_merge_denied(self):
        td = registry.get("merge_duplicate_characters")
        self.assertIsNotNone(td)
        self.assertFalse(is_allowed(td, allowed_tiers={"readonly"}))

    def test_import_tools_denied(self):
        for name in ["import_text_as_chapters", "import_deconstruct_report"]:
            td = registry.get(name)
            self.assertIsNotNone(td)
            self.assertFalse(is_allowed(td, allowed_tiers={"readonly"}))

    def test_start_tools_denied(self):
        for name in ["start_cataloging_job", "start_deconstruct_job"]:
            td = registry.get(name)
            self.assertIsNotNone(td)
            self.assertFalse(is_allowed(td, allowed_tiers={"readonly"}))

    def test_run_tool_denied(self):
        td = registry.get("run_scheduled_task_now")
        self.assertIsNotNone(td)
        self.assertFalse(is_allowed(td, allowed_tiers={"readonly"}))

    def test_export_tool_denied(self):
        td = registry.get("export_project")
        self.assertIsNotNone(td)
        self.assertFalse(is_allowed(td, allowed_tiers={"readonly"}))

    def test_memory_write_denied(self):
        """remember is draft tier, forget is write_confirmed — both denied in readonly."""
        for name in ["remember", "forget"]:
            td = registry.get(name)
            self.assertIsNotNone(td)
            self.assertFalse(is_allowed(td, allowed_tiers={"readonly"}))


class GeneratorDenyTest(unittest.TestCase):
    """Verify that generator tools are denied in readonly mode."""

    def test_writer_tools_denied(self):
        writer_tools = [
            "chapter_writer", "outline_writer", "character_writer",
            "worldbuilding_writer",
        ]
        for name in writer_tools:
            with self.subTest(name=name):
                td = registry.get(name)
                self.assertIsNotNone(td)
                self.assertFalse(
                    is_allowed(td, allowed_tiers={"readonly"}),
                    f"Writer tool allowed: {name}",
                )

    def test_text_manipulation_denied(self):
        for name in ["rewrite_text", "expand_text", "continue_text"]:
            td = registry.get(name)
            self.assertIsNotNone(td)
            self.assertFalse(is_allowed(td, allowed_tiers={"readonly"}))

    def test_roleplay_denied(self):
        for name in ["roleplay_character", "dialogue_battle"]:
            td = registry.get(name)
            self.assertIsNotNone(td)
            self.assertFalse(is_allowed(td, allowed_tiers={"readonly"}))


class SecretToolDenyTest(unittest.TestCase):
    """Verify that secret-related tools are always denied."""

    def test_secret_pattern_detection(self):
        """is_secret_tool must match secret-related name patterns."""
        self.assertTrue(is_secret_tool("get_api_key"))
        self.assertTrue(is_secret_tool("update_api_key"))
        self.assertTrue(is_secret_tool("list_secrets"))
        self.assertTrue(is_secret_tool("manage_credentials"))
        self.assertTrue(is_secret_tool("refresh_token"))
        self.assertTrue(is_secret_tool("set_password"))

    def test_normal_tools_not_secret(self):
        """Normal tools must not match secret patterns."""
        self.assertFalse(is_secret_tool("list_projects"))
        self.assertFalse(is_secret_tool("search_chapters"))
        self.assertFalse(is_secret_tool("create_character"))
        self.assertFalse(is_secret_tool("web_search"))

    def test_secret_tools_denied_at_any_tier(self):
        """Even with all tiers allowed, secret tools must be denied."""
        secret_td = ToolDef(
            name="get_api_key",
            description="Get API key",
            input_schema={},
            tool_type="read",
        )
        self.assertFalse(
            is_allowed(secret_td, allowed_tiers={"readonly", "draft", "write_confirmed"}),
            "Secret tool allowed even with all tiers",
        )

    def test_secret_write_tool_denied(self):
        secret_td = ToolDef(
            name="update_api_key",
            description="Update API key",
            input_schema={},
            tool_type="write",
        )
        self.assertFalse(
            is_allowed(secret_td, allowed_tiers={"readonly", "draft", "write_confirmed"}),
            "Secret write tool allowed even with all tiers",
        )


class FilterToolsTest(unittest.TestCase):
    """Verify the filter_tools batch function."""

    def test_filter_returns_only_readonly(self):
        all_names = registry.all_names()
        all_defs = [registry.get(n) for n in all_names]
        all_defs = [d for d in all_defs if d is not None]

        readonly = filter_tools(all_defs, allowed_tiers={"readonly"})
        readonly_names = {td.name for td in readonly}

        # No write tools in result
        for td in readonly:
            self.assertIn(
                get_tier(td), {"readonly"},
                f"Non-readonly tool in filtered list: {td.name} (tier={get_tier(td)})",
            )

    def test_filter_includes_analysis(self):
        all_names = registry.all_names()
        all_defs = [registry.get(n) for n in all_names]
        all_defs = [d for d in all_defs if d is not None]

        readonly = filter_tools(all_defs, allowed_tiers={"readonly"})
        readonly_names = {td.name for td in readonly}

        self.assertIn("detect_character_changes", readonly_names)
        self.assertIn("preview_writing_context", readonly_names)
        self.assertIn("evaluate_chapter", readonly_names)


class DraftTierTest(unittest.TestCase):
    """Verify that draft tools are allowed when draft tier is enabled."""

    def test_generator_tools_allowed_in_draft(self):
        """Generator tools must be allowed when draft tier is enabled."""
        draft_tools = [
            "chapter_writer", "outline_writer", "character_writer",
            "worldbuilding_writer", "rewrite_text", "expand_text",
            "continue_text", "roleplay_character", "dialogue_battle",
        ]
        for name in draft_tools:
            with self.subTest(name=name):
                td = registry.get(name)
                self.assertIsNotNone(td, f"Tool not found: {name}")
                self.assertTrue(
                    is_allowed(td, allowed_tiers={"readonly", "draft"}),
                    f"Draft tool denied: {name} (tier={get_tier(td)})",
                )

    def test_draft_tools_still_denied_in_readonly(self):
        """Draft tools must still be denied when only readonly is enabled."""
        for name in ["chapter_writer", "rewrite_text", "remember"]:
            with self.subTest(name=name):
                td = registry.get(name)
                self.assertIsNotNone(td)
                self.assertFalse(
                    is_allowed(td, allowed_tiers={"readonly"}),
                    f"Draft tool allowed in readonly mode: {name}",
                )

    def test_write_tools_still_denied_in_draft(self):
        """Write tools must remain denied even when draft tier is enabled."""
        write_tools = [
            "create_project", "delete_chapter", "update_character",
            "merge_duplicate_characters", "import_text_as_chapters",
        ]
        for name in write_tools:
            with self.subTest(name=name):
                td = registry.get(name)
                self.assertIsNotNone(td)
                self.assertFalse(
                    is_allowed(td, allowed_tiers={"readonly", "draft"}),
                    f"Write tool allowed in draft mode: {name}",
                )

    def test_secret_tools_still_denied_in_draft(self):
        """Secret tools remain denied even with all tiers."""
        secret_td = ToolDef(
            name="get_api_key",
            description="Get API key",
            input_schema={},
            tool_type="read",
        )
        self.assertFalse(
            is_allowed(secret_td, allowed_tiers={"readonly", "draft"}),
        )

    def test_draft_filter_returns_correct_set(self):
        """filter_tools with draft tier returns readonly + draft tools."""
        all_names = registry.all_names()
        all_defs = [registry.get(n) for n in all_names]
        all_defs = [d for d in all_defs if d is not None]

        filtered = filter_tools(all_defs, allowed_tiers={"readonly", "draft"})
        tiers = {get_tier(td) for td in filtered}
        self.assertIn("readonly", tiers)
        self.assertIn("draft", tiers)
        self.assertNotIn("write_confirmed", tiers)

    def test_remember_is_draft(self):
        """remember tool should be in draft tier."""
        td = registry.get("remember")
        self.assertIsNotNone(td)
        self.assertEqual(get_tier(td), "draft")


if __name__ == "__main__":
    unittest.main()
