"""Tests for external draft storage tools."""
import asyncio
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.workspace.registry import registry


class ExternalDraftToolsRegisteredTest(unittest.TestCase):
    """Verify external draft tools are registered."""

    def test_save_draft_registered(self):
        td = registry.get("save_external_chapter_draft")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "read")

    def test_get_draft_registered(self):
        td = registry.get("get_external_chapter_draft")
        self.assertIsNotNone(td)

    def test_in_readonly_pack(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertIn("save_external_chapter_draft", names)
        self.assertIn("get_external_chapter_draft", names)


class SaveExternalDraftTest(unittest.TestCase):
    """Verify save_external_chapter_draft behavior."""

    def test_empty_content_skipped(self):
        from app.services.workspace.tools.external_writing import save_external_chapter_draft
        db = MagicMock()
        result = asyncio.run(save_external_chapter_draft(db, "p1", {}))
        self.assertEqual(result["status"], "skipped")

    @patch("app.services.workspace.generated_drafts.store_chapter_draft")
    def test_saves_draft(self, mock_store):
        from app.services.workspace.tools.external_writing import save_external_chapter_draft
        mock_store.return_value = "draft-123"
        db = MagicMock()
        result = asyncio.run(save_external_chapter_draft(db, "p1", {
            "content": "Test chapter content",
            "title": "Chapter 1",
        }))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["draft_id"], "draft-123")
        mock_store.assert_called_once()


class GetExternalDraftTest(unittest.TestCase):
    """Verify get_external_chapter_draft behavior."""

    def test_missing_draft_id_skipped(self):
        from app.services.workspace.tools.external_writing import get_external_chapter_draft
        db = MagicMock()
        result = asyncio.run(get_external_chapter_draft(db, "p1", {}))
        self.assertEqual(result["status"], "skipped")

    @patch("app.services.workspace.generated_drafts.get_chapter_draft")
    def test_draft_not_found(self, mock_get):
        from app.services.workspace.tools.external_writing import get_external_chapter_draft
        mock_get.return_value = None
        db = MagicMock()
        result = asyncio.run(get_external_chapter_draft(db, "p1", {"draft_id": "nonexistent"}))
        self.assertEqual(result["status"], "skipped")

    @patch("app.services.workspace.generated_drafts.get_chapter_draft")
    def test_returns_draft(self, mock_get):
        from app.services.workspace.tools.external_writing import get_external_chapter_draft
        mock_get.return_value = {
            "project_id": "p1",
            "title": "Chapter 1",
            "content": "Test content",
            "outline_node_id": "n1",
        }
        db = MagicMock()
        result = asyncio.run(get_external_chapter_draft(db, "p1", {"draft_id": "draft-123"}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["title"], "Chapter 1")


if __name__ == "__main__":
    unittest.main()
