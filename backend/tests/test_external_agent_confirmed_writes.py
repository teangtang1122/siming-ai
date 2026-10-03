"""Tests for external Agent confirmed write flow."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.external_agent.write_requests import request_write, confirm_write, reject_write, WRITE_TYPES


class WriteTypesTest(unittest.TestCase):
    """Verify supported write types."""

    def test_write_types_defined(self):
        self.assertNotIn("create_chapter", WRITE_TYPES)
        self.assertNotIn("update_chapter", WRITE_TYPES)
        self.assertIn("create_character", WRITE_TYPES)
        self.assertIn("create_worldbuilding", WRITE_TYPES)

    def test_outline_creation_requires_author_confirmed_draft(self):
        self.assertNotIn("create_outline", WRITE_TYPES)
        self.assertIn("update_outline", WRITE_TYPES)


class RequestWriteTest(unittest.TestCase):
    """Verify request_write function."""

    def test_invalid_run_returns_error(self):
        from unittest.mock import MagicMock
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = request_write(db, "nonexistent", "create_character", "test")
        self.assertEqual(result["status"], "error")

    def test_invalid_write_type_returns_error(self):
        from unittest.mock import MagicMock
        from app.database.models import AgentRun
        run = AgentRun()
        run.id = "r1"
        run.project_id = "p1"
        run.status = "running"

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = run

        result = request_write(db, "r1", "invalid_type", "test")
        self.assertEqual(result["status"], "error")
        self.assertIn("Unsupported", result["detail"])


class ConfirmWriteTest(unittest.TestCase):
    """Verify confirm_write function."""

    def test_invalid_run_returns_error(self):
        from unittest.mock import MagicMock
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = confirm_write(db, "nonexistent", 1)
        self.assertEqual(result["status"], "error")

    def test_missing_event_returns_error(self):
        from unittest.mock import MagicMock
        from app.database.models import AgentRun
        run = AgentRun()
        run.id = "r1"
        run.status = "running"

        db = MagicMock()
        # First query returns run, second returns None (no event)
        db.query.return_value.filter.return_value.first.side_effect = [run, None]

        result = confirm_write(db, "r1", 999)
        self.assertEqual(result["status"], "error")


class RejectWriteTest(unittest.TestCase):
    """Verify reject_write function."""

    def test_invalid_run_returns_error(self):
        from unittest.mock import MagicMock
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = reject_write(db, "nonexistent", 1)
        self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
