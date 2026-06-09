"""Tests for external Agent reporting MCP tools."""
import asyncio
import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.adapter import list_mcp_tools, is_tool_allowed
from app.mcp.permissions import get_tier
from app.services.workspace.registry import registry


class ExternalAgentToolsRegisteredTest(unittest.TestCase):
    """Verify external agent tools are registered in the workspace registry."""

    def test_start_agent_run_registered(self):
        td = registry.get("start_agent_run")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "read")

    def test_report_agent_plan_registered(self):
        td = registry.get("report_agent_plan")
        self.assertIsNotNone(td)

    def test_report_agent_progress_registered(self):
        td = registry.get("report_agent_progress")
        self.assertIsNotNone(td)

    def test_report_context_selected_registered(self):
        td = registry.get("report_context_selected")
        self.assertIsNotNone(td)

    def test_append_draft_chunk_registered(self):
        td = registry.get("append_draft_chunk")
        self.assertIsNotNone(td)

    def test_mark_draft_ready_registered(self):
        td = registry.get("mark_draft_ready")
        self.assertIsNotNone(td)

    def test_finish_agent_run_registered(self):
        td = registry.get("finish_agent_run")
        self.assertIsNotNone(td)


class ExternalAgentToolsPermissionTest(unittest.TestCase):
    """Verify external agent tools are allowed in readonly mode."""

    def test_all_reporting_tools_allowed_in_readonly(self):
        tools = [
            "start_agent_run", "report_agent_plan", "report_agent_progress",
            "report_context_selected", "append_draft_chunk", "mark_draft_ready",
            "finish_agent_run",
        ]
        for name in tools:
            with self.subTest(name=name):
                self.assertTrue(
                    is_tool_allowed(name, allowed_tiers={"readonly"}),
                    f"Tool not allowed in readonly: {name}",
                )

    def test_reporting_tools_tier_is_readonly(self):
        tools = [
            "start_agent_run", "report_agent_plan", "report_agent_progress",
            "report_context_selected", "append_draft_chunk", "mark_draft_ready",
            "finish_agent_run",
        ]
        for name in tools:
            with self.subTest(name=name):
                td = registry.get(name)
                self.assertIsNotNone(td)
                self.assertEqual(get_tier(td), "readonly", f"{name} tier is not readonly")


class ExternalAgentToolsInMcpListTest(unittest.TestCase):
    """Verify external agent tools appear in MCP tools/list."""

    def test_reporting_tools_in_mcp_list(self):
        mcp_tools = list_mcp_tools(allowed_tiers={"readonly"})
        names = {t.name for t in mcp_tools}
        self.assertIn("start_agent_run", names)
        self.assertIn("report_agent_plan", names)
        self.assertIn("finish_agent_run", names)

    def test_secret_tools_still_not_in_mcp_list(self):
        mcp_tools = list_mcp_tools(allowed_tiers={"readonly", "draft", "write_confirmed"})
        names = {t.name for t in mcp_tools}
        for name in names:
            self.assertFalse(
                any(p in name.lower() for p in ["api_key", "secret", "credential", "token", "password"]),
                f"Secret tool exposed: {name}",
            )


if __name__ == "__main__":
    unittest.main()
