"""Tests for MCP permission source resolution."""
import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.external_agent.permissions import (
    resolve_effective_pack,
    _highest_pack,
    _packs_up_to,
    PACK_ORDER,
)


class HighestPackTest(unittest.TestCase):
    """Verify _highest_pack calculation."""

    def test_readonly_only(self):
        self.assertEqual(_highest_pack(["readonly_collaboration"]), "readonly_collaboration")

    def test_readonly_and_draft(self):
        self.assertEqual(_highest_pack(["readonly_collaboration", "draft_generation"]), "draft_generation")

    def test_all_packs(self):
        self.assertEqual(_highest_pack(PACK_ORDER), "trusted_local_maintenance")

    def test_unknown_pack_ignored(self):
        self.assertEqual(_highest_pack(["unknown_pack", "readonly_collaboration"]), "readonly_collaboration")

    def test_empty_list(self):
        self.assertEqual(_highest_pack([]), "readonly_collaboration")


class PacksUpToTest(unittest.TestCase):
    """Verify _packs_up_to calculation."""

    def test_readonly(self):
        self.assertEqual(_packs_up_to("readonly_collaboration"), ["readonly_collaboration"])

    def test_draft(self):
        result = _packs_up_to("draft_generation")
        self.assertEqual(result, ["readonly_collaboration", "draft_generation"])

    def test_project_writing(self):
        result = _packs_up_to("project_writing")
        self.assertEqual(len(result), 3)

    def test_unknown(self):
        result = _packs_up_to("unknown")
        self.assertEqual(result, ["unknown"])


class ResolveEffectivePackTest(unittest.TestCase):
    """Verify resolve_effective_pack logic."""

    def test_cli_override_takes_priority(self):
        db = MagicMock()
        result = resolve_effective_pack(db, cli_pack="project_management")
        self.assertEqual(result["effective_pack"], "project_management")
        self.assertEqual(result["source"], "cli_override")
        self.assertTrue(result["cli_override"])

    def test_auto_mode_not_cli_override(self):
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock
        result = resolve_effective_pack(db, cli_pack="auto")
        self.assertFalse(result["cli_override"])

    def test_project_override_takes_priority_over_global(self):
        db = MagicMock()

        # Mock project settings
        project_settings = MagicMock()
        project_settings.enabled_packs = ["project_writing"]

        # Mock global settings
        global_settings = MagicMock()
        global_settings.enabled_packs = ["readonly_collaboration"]
        global_settings.mcp_permission_source = "global_settings"

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            model_name = model.__name__ if hasattr(model, '__name__') else str(model)
            if "ExternalAgentSettings" in model_name and "Global" not in model_name:
                q.first.return_value = project_settings
            elif "ExternalAgentGlobalSettings" in model_name:
                q.first.return_value = global_settings
            else:
                q.first.return_value = None
            return q

        db.query.side_effect = query_side_effect
        result = resolve_effective_pack(db, project_id="p1")
        self.assertEqual(result["effective_pack"], "project_writing")
        self.assertEqual(result["source"], "project_override")

    def test_global_settings_used_when_no_project(self):
        db = MagicMock()

        global_settings = MagicMock()
        global_settings.enabled_packs = ["draft_generation"]
        global_settings.mcp_permission_source = "global_settings"

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            model_name = model.__name__ if hasattr(model, '__name__') else str(model)
            if "ExternalAgentGlobalSettings" in model_name:
                q.first.return_value = global_settings
            else:
                q.first.return_value = None
            return q

        db.query.side_effect = query_side_effect
        result = resolve_effective_pack(db)
        self.assertEqual(result["effective_pack"], "draft_generation")
        self.assertEqual(result["source"], "global_settings")

    def test_default_when_no_settings(self):
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock
        result = resolve_effective_pack(db)
        self.assertEqual(result["effective_pack"], "readonly_collaboration")
        self.assertEqual(result["source"], "default")


class McpPermissionStatusToolTest(unittest.TestCase):
    """Verify get_mcp_permission_status tool is registered."""

    def test_registered(self):
        from app.services.workspace.registry import registry
        td = registry.get("get_mcp_permission_status")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "read")

    def test_in_readonly_pack(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertIn("get_mcp_permission_status", names)


if __name__ == "__main__":
    unittest.main()
