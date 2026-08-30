"""Tests for external draft storage tools."""
import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.workspace.registry import registry


class ExternalDraftToolsRegisteredTest(unittest.TestCase):
    """Verify external draft tools are registered."""

    def test_save_draft_registered(self):
        td = registry.get("save_external_chapter_draft")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "write")

    def test_get_draft_registered(self):
        td = registry.get("get_external_chapter_draft")
        self.assertIsNotNone(td)

    def test_draft_write_requires_a_writing_capability_pack(self):
        from app.mcp.adapter import list_mcp_tools
        readonly = {t.name for t in list_mcp_tools(permission_pack="readonly_collaboration")}
        drafting = {t.name for t in list_mcp_tools(permission_pack="chapter_drafting")}
        self.assertNotIn("save_external_chapter_draft", readonly)
        self.assertIn("get_external_chapter_draft", readonly)
        self.assertIn("save_external_chapter_draft", drafting)


class SaveExternalDraftTest(unittest.TestCase):
    """Verify save_external_chapter_draft behavior."""

    def test_empty_content_skipped(self):
        from app.services.workspace.tools.external_writing import save_external_chapter_draft
        db = MagicMock()
        result = asyncio.run(save_external_chapter_draft(db, "p1", {}))
        self.assertEqual(result["status"], "skipped")

    @patch("app.services.cataloging.launcher.find_cataloging_required_chapter", return_value=None)
    @patch("app.services.cataloging.launcher.find_blocking_chapter_cataloging_job", return_value=None)
    @patch("app.services.workspace.generated_drafts.find_pending_chapter_draft", return_value=None)
    @patch("app.services.workspace.generated_drafts.store_chapter_draft")
    @patch(
        "app.services.workspace.tools.external_writing._external_draft_manifest_error",
        return_value=None,
    )
    def test_rejects_mismatched_revision_target_for_unused_outline(
        self,
        _mock_manifest_error,
        mock_store,
        _mock_pending,
        _mock_job,
        _mock_required,
    ):
        from app.services.workspace.tools.external_writing import save_external_chapter_draft
        mock_store.return_value = "draft-123"
        db = MagicMock()
        outline = MagicMock(id="o1", title="Chapter 2", node_type="chapter")
        outline_query = MagicMock()
        outline_query.filter.return_value = outline_query
        outline_query.first.return_value = outline
        chapter_query = MagicMock()
        chapter_query.filter.return_value = chapter_query
        chapter_query.order_by.return_value = chapter_query
        chapter_query.first.return_value = None
        db.query.side_effect = [outline_query, chapter_query]
        result = asyncio.run(save_external_chapter_draft(db, "p1", {
            "content": "Test chapter content",
            "title": "Caller must not control this title",
            "outline_node_id": "o1",
            "target_chapter_id": "wrong-existing-chapter",
        }))
        self.assertEqual(result["status"], "skipped")
        mock_store.assert_not_called()

    @patch("app.services.cataloging.launcher.find_cataloging_required_chapter", return_value=None)
    @patch("app.services.cataloging.launcher.find_blocking_chapter_cataloging_job", return_value=None)
    @patch("app.services.workspace.generated_drafts.find_pending_chapter_draft", return_value=None)
    def test_rejects_direct_save_without_reviewed_context_manifest(
        self,
        _mock_pending,
        _mock_job,
        _mock_required,
    ):
        from app.services.workspace.tools.external_writing import save_external_chapter_draft

        db = MagicMock()
        outline = MagicMock(id="o1", title="Chapter 2", node_type="chapter")
        outline_query = MagicMock()
        outline_query.filter.return_value = outline_query
        outline_query.first.return_value = outline
        chapter_query = MagicMock()
        chapter_query.filter.return_value = chapter_query
        chapter_query.first.return_value = None
        db.query.side_effect = [outline_query, chapter_query]

        result = asyncio.run(save_external_chapter_draft(db, "p1", {
            "content": "Unreviewed prose must not become a pending draft.",
            "outline_node_id": "o1",
        }))

        self.assertEqual(result["status"], "needs_confirmation")
        self.assertIn("context_manifest_id", result["detail"])

    @patch("app.services.cataloging.launcher.find_cataloging_required_chapter", return_value=None)
    @patch("app.services.cataloging.launcher.find_blocking_chapter_cataloging_job", return_value=None)
    @patch("app.services.workspace.generated_drafts.find_pending_chapter_draft", return_value=None)
    @patch("app.services.workspace.generated_drafts.store_chapter_draft")
    def test_refuses_outline_that_already_has_a_formal_chapter(
        self,
        mock_store,
        _mock_pending,
        _mock_job,
        _mock_required,
    ):
        from app.services.workspace.tools.external_writing import save_external_chapter_draft

        db = MagicMock()
        outline = MagicMock(id="o1", title="Chapter 1", node_type="chapter")
        outline_query = MagicMock()
        outline_query.filter.return_value = outline_query
        outline_query.first.return_value = outline
        chapter = MagicMock(id="chapter-1")
        chapter_query = MagicMock()
        chapter_query.filter.return_value = chapter_query
        chapter_query.order_by.return_value = chapter_query
        chapter_query.first.return_value = chapter
        db.query.side_effect = [outline_query, chapter_query]

        result = asyncio.run(save_external_chapter_draft(db, "p1", {
            "content": "Replacement prose must not be saved",
            "outline_node_id": "o1",
        }))

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["data"]["existing_chapter_id"], "chapter-1")
        mock_store.assert_not_called()

    @patch("app.services.cataloging.launcher.find_cataloging_required_chapter", return_value=None)
    @patch("app.services.cataloging.launcher.find_blocking_chapter_cataloging_job", return_value=None)
    @patch("app.services.workspace.generated_drafts.find_pending_chapter_draft", return_value=None)
    @patch("app.services.workspace.generated_drafts.store_chapter_draft", return_value="revision-1")
    @patch(
        "app.services.workspace.tools.external_writing._external_draft_manifest_error",
        return_value=None,
    )
    def test_saves_reviewable_revision_for_explicit_matching_target(
        self,
        _mock_manifest,
        mock_store,
        _mock_pending,
        _mock_job,
        _mock_required,
    ):
        from app.services.workspace.tools.external_writing import save_external_chapter_draft

        db = MagicMock()
        outline = MagicMock(id="o1", title="Chapter 1", node_type="chapter")
        outline_query = MagicMock()
        outline_query.filter.return_value = outline_query
        outline_query.first.return_value = outline
        chapter = MagicMock(id="chapter-1", current_version=3)
        chapter_query = MagicMock()
        chapter_query.filter.return_value = chapter_query
        chapter_query.first.return_value = chapter
        db.query.side_effect = [outline_query, chapter_query]

        result = asyncio.run(save_external_chapter_draft(db, "p1", {
            "content": "Review this revision without overwriting prose.",
            "outline_node_id": "o1",
            "target_chapter_id": "chapter-1",
            "base_chapter_version": 3,
            "context_manifest_id": "manifest-1",
            "context_selection_token": "selection-1",
        }))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["draft_kind"], "revision")
        self.assertEqual(result["data"]["target_chapter_id"], "chapter-1")
        self.assertEqual(result["data"]["base_chapter_version"], 3)
        mock_store.assert_called_once_with(
            project_id="p1",
            content="Review this revision without overwriting prose.",
            title="Chapter 1",
            outline_node_id="o1",
            context_manifest_id="manifest-1",
            target_chapter_id="chapter-1",
            base_chapter_version=3,
            db=db,
        )

    @patch("app.services.workspace.generated_drafts.store_chapter_draft")
    def test_revision_requires_the_preparation_base_version(self, mock_store):
        from app.services.workspace.tools.external_writing import save_external_chapter_draft

        db = MagicMock()
        outline = MagicMock(id="o1", title="Chapter 1", node_type="chapter")
        outline_query = MagicMock()
        outline_query.filter.return_value = outline_query
        outline_query.first.return_value = outline
        chapter = MagicMock(id="chapter-1", current_version=4)
        chapter_query = MagicMock()
        chapter_query.filter.return_value = chapter_query
        chapter_query.first.return_value = chapter
        db.query.side_effect = [outline_query, chapter_query]

        result = asyncio.run(save_external_chapter_draft(db, "p1", {
            "content": "A revision generated from an unknown base must not be accepted.",
            "outline_node_id": "o1",
            "target_chapter_id": "chapter-1",
            "context_manifest_id": "manifest-1",
            "context_selection_token": "selection-1",
        }))

        self.assertEqual(result["status"], "skipped")
        self.assertIn("base_chapter_version", result["detail"])
        mock_store.assert_not_called()


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
        mock_get.return_value = "Test content"
        db = MagicMock()
        result = asyncio.run(get_external_chapter_draft(db, "p1", {"draft_id": "draft-123"}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["content"], "Test content")


if __name__ == "__main__":
    unittest.main()
