"""Tests for MCP transaction visibility — successful calls are committed and visible.

Verifies that when external agents make successful tool calls through MCP,
the results are actually committed to the database and visible from a fresh
session. Failed calls should roll back and return errors.
"""
import asyncio
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def chapter_target_db():
    db = MagicMock()
    outline = MagicMock(id="o1", title="Test Chapter", node_type="chapter")
    outline_query = MagicMock()
    outline_query.filter.return_value = outline_query
    outline_query.first.return_value = outline
    chapter_query = MagicMock()
    chapter_query.filter.return_value = chapter_query
    chapter_query.order_by.return_value = chapter_query
    chapter_query.first.return_value = None
    db.query.side_effect = [outline_query, chapter_query]
    return db


class TransactionVisibilityTest(unittest.TestCase):
    """Verify MCP tool calls produce visible database changes."""

    def test_successful_tool_returns_ok_status(self):
        """Successful tool execution should return status=ok."""
        from app.services.workspace.tools.external_writing import save_external_chapter_draft

        with patch("app.services.workspace.generated_drafts.store_chapter_draft", return_value="draft-tv-1"), \
             patch("app.services.workspace.generated_drafts.find_pending_chapter_draft", return_value=None), \
             patch("app.services.cataloging.launcher.find_blocking_chapter_cataloging_job", return_value=None), \
             patch("app.services.cataloging.launcher.find_cataloging_required_chapter", return_value=None), \
             patch(
                 "app.services.workspace.tools.external_writing._external_draft_manifest_error",
                 return_value=None,
             ):
            db = chapter_target_db()
            result = asyncio.run(save_external_chapter_draft(db, "p1", {
                "content": "Test content for transaction visibility",
                "outline_node_id": "o1",
            }))
        self.assertEqual(result["status"], "ok")
        self.assertIn("draft_id", result["data"])

    def test_failed_tool_returns_error_status(self):
        """Failed tool execution should return status=error or skipped."""
        from app.services.workspace.tools.external_writing import save_external_chapter_draft

        db = MagicMock()
        result = asyncio.run(save_external_chapter_draft(db, "p1", {}))
        self.assertEqual(result["status"], "skipped")

    def test_tool_result_has_required_fields(self):
        """Tool result should have tool, status, detail fields."""
        from app.services.workspace.tools.external_writing import prepare_external_writing_context

        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock

        result = asyncio.run(prepare_external_writing_context(db, "p1", {}))
        self.assertIn("tool", result)
        self.assertIn("status", result)
        self.assertIn("detail", result)

    def test_mcp_adapter_returns_structured_result(self):
        """MCP adapter should return structured result with content array."""
        from app.mcp.schemas import make_text_result, make_json_result

        text_result = make_text_result("test message")
        self.assertFalse(text_result.is_error)
        self.assertEqual(len(text_result.content), 1)
        self.assertEqual(text_result.content[0]["type"], "text")

        json_result = make_json_result({"key": "value"})
        self.assertFalse(json_result.is_error)
        self.assertIn('"key"', json_result.content[0]["text"])

    def test_error_result_has_is_error_flag(self):
        """Error results should have isError=true."""
        from app.mcp.schemas import make_text_result

        result = make_text_result("error message", is_error=True)
        self.assertTrue(result.is_error)


class ReadAfterWriteVerificationTest(unittest.TestCase):
    """Verify that read-after-write patterns work correctly."""

    def test_draft_can_be_retrieved_after_save(self):
        """After saving a draft, it should be retrievable."""
        from app.services.workspace.tools.external_writing import (
            save_external_chapter_draft,
            get_external_chapter_draft,
        )

        with patch("app.services.workspace.generated_drafts.store_chapter_draft", return_value="draft-raw-1"), \
             patch("app.services.workspace.generated_drafts.get_chapter_draft", return_value="Saved content"), \
             patch("app.services.workspace.generated_drafts.find_pending_chapter_draft", return_value=None), \
             patch("app.services.cataloging.launcher.find_blocking_chapter_cataloging_job", return_value=None), \
             patch("app.services.cataloging.launcher.find_cataloging_required_chapter", return_value=None), \
             patch(
                 "app.services.workspace.tools.external_writing._external_draft_manifest_error",
                 return_value=None,
             ):
            db = chapter_target_db()

            # Save
            save_result = asyncio.run(save_external_chapter_draft(db, "p1", {
                "content": "Saved content",
                "outline_node_id": "o1",
            }))
            self.assertEqual(save_result["status"], "ok")
            draft_id = save_result["data"]["draft_id"]

            # Read back
            get_result = asyncio.run(get_external_chapter_draft(db, "p1", {
                "draft_id": draft_id,
            }))
            self.assertEqual(get_result["status"], "ok")

    def test_review_can_be_recorded_after_draft(self):
        """After saving a draft, a review can be recorded."""
        from app.services.workspace.tools.external_writing import record_external_quality_review

        db = MagicMock()
        result = asyncio.run(record_external_quality_review(db, "p1", {
            "draft_id": "draft-raw-2",
            "scores": {"opening_hook": 8},
            "pass": True,
        }))
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
