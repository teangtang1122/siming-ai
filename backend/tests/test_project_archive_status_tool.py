"""Tests for get_project_archive_status tool."""
import asyncio
import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _run(coro):
    """Run an async coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class ProjectArchiveStatusToolTest(unittest.TestCase):
    """Verify get_project_archive_status returns correct counts."""

    def test_empty_project(self):
        """Empty project returns zero counts and import_chapters recommendation."""
        from app.services.workspace.tools.project_status import get_project_archive_status

        db = MagicMock()
        # All queries return 0; CatalogingJob.first() returns None
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.count.return_value = 0
        q.order_by.return_value = q
        q.first.return_value = None
        db.query.return_value = q

        result = _run(get_project_archive_status(db, "proj-1", {}))

        self.assertEqual(result["status"], "ok")
        data = result["data"]
        self.assertEqual(data["chapters_count"], 0)
        self.assertEqual(data["characters_count"], 0)
        self.assertEqual(data["outline_nodes_count"], 0)
        self.assertEqual(data["worldbuilding_count"], 0)
        self.assertEqual(data["chapter_summaries_count"], 0)
        self.assertEqual(data["character_aliases_count"], 0)
        self.assertEqual(data["relationships_count"], 0)
        self.assertIsNone(data["last_cataloging_job"])
        self.assertIn("import_chapters", data["recommended_next_steps"])

    def test_project_with_chapters_but_no_archive(self):
        """Chapters imported but no characters/outline triggers warnings."""
        from app.services.workspace.tools.project_status import get_project_archive_status

        call_count = {"n": 0}

        def mock_query(model):
            q = MagicMock()
            q.filter.return_value = q
            q.join.return_value = q
            q.order_by.return_value = q
            q.first.return_value = None
            # First call (Chapter) returns 150, rest return 0
            idx = call_count["n"]
            call_count["n"] += 1
            q.count.return_value = 150 if idx == 0 else 0
            return q

        db = MagicMock()
        db.query = mock_query

        result = _run(get_project_archive_status(db, "proj-1", {}))

        data = result["data"]
        self.assertEqual(data["chapters_count"], 150)
        self.assertEqual(data["characters_count"], 0)
        self.assertIn("chapters_imported_but_no_characters", data["warnings"])
        self.assertIn("chapters_imported_but_no_outline", data["warnings"])
        self.assertIn("run_cataloging", data["recommended_next_steps"])

    def test_project_with_full_archive(self):
        """Fully archived project has no warnings."""
        from app.services.workspace.tools.project_status import get_project_archive_status

        call_count = {"n": 0}
        # chapters, summaries, outline, characters, aliases, rels, wb, job_count
        counts = [150, 150, 150, 20, 5, 30, 10, 0]

        def mock_query(model):
            q = MagicMock()
            q.filter.return_value = q
            q.join.return_value = q
            q.order_by.return_value = q
            q.first.return_value = None
            idx = call_count["n"]
            call_count["n"] += 1
            q.count.return_value = counts[idx] if idx < len(counts) else 0
            return q

        db = MagicMock()
        db.query = mock_query

        result = _run(get_project_archive_status(db, "proj-1", {}))

        data = result["data"]
        self.assertEqual(data["chapters_count"], 150)
        self.assertEqual(data["characters_count"], 20)
        self.assertEqual(data["chapter_summaries_count"], 150)
        self.assertEqual(data["outline_nodes_count"], 150)
        self.assertEqual(data["character_aliases_count"], 5)
        self.assertEqual(data["relationships_count"], 30)
        self.assertEqual(data["worldbuilding_count"], 10)
        self.assertEqual(data["warnings"], [])

    def test_returns_last_cataloging_job(self):
        """Last cataloging job info is included when present."""
        from app.services.workspace.tools.project_status import get_project_archive_status

        mock_job = MagicMock()
        mock_job.id = "job-1"
        mock_job.status = "completed"
        mock_job.execution_mode = "auto"
        mock_job.total_chapters = 10
        mock_job.completed_chapters = 10
        mock_job.created_at = None

        call_count = {"n": 0}

        def mock_query(model):
            q = MagicMock()
            q.filter.return_value = q
            q.join.return_value = q
            q.order_by.return_value = q
            q.count.return_value = 0
            idx = call_count["n"]
            call_count["n"] += 1
            # CatalogingJob query (8th call) returns the mock job
            if idx == 7:
                q.first.return_value = mock_job
            else:
                q.first.return_value = None
            return q

        db = MagicMock()
        db.query = mock_query

        result = _run(get_project_archive_status(db, "proj-1", {}))

        data = result["data"]
        self.assertIsNotNone(data["last_cataloging_job"])
        self.assertEqual(data["last_cataloging_job"]["job_id"], "job-1")
        self.assertEqual(data["last_cataloging_job"]["status"], "completed")

    def test_detail_string_includes_counts(self):
        """Detail string summarizes key counts."""
        from app.services.workspace.tools.project_status import get_project_archive_status

        call_count = {"n": 0}
        # chapters, summaries, outline, characters, aliases, rels, wb, job
        counts = [5, 5, 5, 3, 1, 2, 4, 0]

        def mock_query(model):
            q = MagicMock()
            q.filter.return_value = q
            q.join.return_value = q
            q.order_by.return_value = q
            q.first.return_value = None
            idx = call_count["n"]
            call_count["n"] += 1
            q.count.return_value = counts[idx] if idx < len(counts) else 0
            return q

        db = MagicMock()
        db.query = mock_query

        result = _run(get_project_archive_status(db, "proj-1", {}))

        self.assertIn("5 chapters", result["detail"])
        self.assertIn("3 characters", result["detail"])
        self.assertIn("5 outline nodes", result["detail"])
        self.assertIn("4 worldbuilding entries", result["detail"])


if __name__ == "__main__":
    unittest.main()
