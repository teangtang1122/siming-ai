"""Tests for external quality review record tool."""
import asyncio
import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.workspace.registry import registry


class ExternalQualityReviewToolRegisteredTest(unittest.TestCase):
    """Verify record_external_quality_review is registered."""

    def test_registered(self):
        td = registry.get("record_external_quality_review")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "read")

    def test_in_readonly_pack(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertIn("record_external_quality_review", names)


class RecordExternalQualityReviewTest(unittest.TestCase):
    """Verify record_external_quality_review behavior."""

    def test_missing_ids_skipped(self):
        from app.services.workspace.tools.external_writing import record_external_quality_review
        db = MagicMock()
        result = asyncio.run(record_external_quality_review(db, "p1", {}))
        self.assertEqual(result["status"], "skipped")

    def test_records_review_with_scores(self):
        from app.services.workspace.tools.external_writing import record_external_quality_review
        db = MagicMock()
        result = asyncio.run(record_external_quality_review(db, "p1", {
            "draft_id": "draft-123",
            "scores": {"opening_hook": 8, "plot_progression": 7},
            "pass": True,
            "reviewer_model": "claude-sonnet-4-6",
        }))
        self.assertEqual(result["status"], "ok")
        self.assertIn("PASS", result["detail"])
        self.assertEqual(result["data"]["total_score"], 15)

    def test_records_review_with_chapter_id(self):
        from app.services.workspace.tools.external_writing import record_external_quality_review
        chapter = MagicMock()
        chapter.id = "ch-123"
        chapter.title = "Chapter 1"

        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = chapter
        db.query.return_value = query_mock

        result = asyncio.run(record_external_quality_review(db, "p1", {
            "chapter_id": "ch-123",
            "scores": {"opening_hook": 6},
            "pass": False,
        }))
        self.assertEqual(result["status"], "ok")
        self.assertIn("FAIL", result["detail"])
        self.assertEqual(result["data"]["chapter_id"], "ch-123")

    def test_records_issues_and_suggestions(self):
        from app.services.workspace.tools.external_writing import record_external_quality_review
        db = MagicMock()
        result = asyncio.run(record_external_quality_review(db, "p1", {
            "draft_id": "draft-123",
            "scores": {"opening_hook": 8},
            "issues": ["Pacing slow"],
            "revision_suggestions": ["Add tension"],
            "pass": True,
        }))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["issues"], ["Pacing slow"])
        self.assertEqual(result["data"]["revision_suggestions"], ["Add tension"])


if __name__ == "__main__":
    unittest.main()
