"""Tests for external cataloging tools."""
import asyncio
import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.workspace.registry import registry


class ExternalCatalogingToolsRegisteredTest(unittest.TestCase):
    """Verify external cataloging tools are registered."""

    def test_start_job_registered(self):
        td = registry.get("start_external_cataloging_job")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "read")

    def test_get_next_chapter_registered(self):
        td = registry.get("get_next_external_cataloging_chapter")
        self.assertIsNotNone(td)

    def test_save_facts_registered(self):
        td = registry.get("save_external_cataloging_facts")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "write")
        self.assertTrue(td.writes_project_data)

    def test_save_candidates_registered(self):
        td = registry.get("save_external_cataloging_candidates")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "write")
        self.assertTrue(td.writes_project_data)

    def test_verify_progress_registered(self):
        td = registry.get("verify_external_cataloging_progress")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "read")

    def test_readonly_tools_in_readonly_pack(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertIn("start_external_cataloging_job", names)
        self.assertIn("get_next_external_cataloging_chapter", names)
        self.assertIn("verify_external_cataloging_progress", names)

    def test_write_tools_not_in_readonly(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertNotIn("save_external_cataloging_facts", names)
        self.assertNotIn("save_external_cataloging_candidates", names)

    def test_write_tools_in_project_writing(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="project_writing")
        names = {t.name for t in tools}
        self.assertIn("save_external_cataloging_facts", names)
        self.assertIn("save_external_cataloging_candidates", names)


class StartExternalCatalogingJobTest(unittest.TestCase):
    """Verify start_external_cataloging_job behavior."""

    def test_no_chapters_returns_skipped(self):
        from app.services.workspace.tools.external_cataloging import start_external_cataloging_job
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.order_by.return_value = query_mock
        query_mock.all.return_value = []
        query_mock.count.return_value = 0
        db.query.return_value = query_mock
        result = asyncio.run(start_external_cataloging_job(db, "p1", {}))
        self.assertEqual(result["status"], "skipped")


class VerifyProgressTest(unittest.TestCase):
    """Verify verify_external_cataloging_progress behavior."""

    def test_missing_job_id_returns_skipped(self):
        from app.services.workspace.tools.external_cataloging import verify_external_cataloging_progress
        db = MagicMock()
        result = asyncio.run(verify_external_cataloging_progress(db, "p1", {}))
        self.assertEqual(result["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
