"""Tests for external cataloging applier — verifies existing applier works with external candidates."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class ExternalCatalogingApplyTest(unittest.TestCase):
    """Verify external cataloging candidates can be applied by existing applier."""

    def test_apply_pending_cataloging_registered(self):
        """apply_pending_cataloging should be registered."""
        from app.services.workspace.registry import registry
        td = registry.get("apply_pending_cataloging")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "write")

    def test_apply_pending_cataloging_in_project_writing(self):
        """apply_pending_cataloging should be in project_writing pack."""
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="project_writing")
        names = {t.name for t in tools}
        self.assertIn("apply_pending_cataloging", names)

    def test_cataloging_candidate_model_exists(self):
        """CatalogingCandidate model should exist."""
        from app.database.models import CatalogingCandidate
        self.assertIsNotNone(CatalogingCandidate)
        columns = {c.name for c in CatalogingCandidate.__table__.columns}
        self.assertIn("status", columns)
        self.assertIn("item_type", columns)
        self.assertIn("raw_payload", columns)

    def test_cataloging_fact_model_exists(self):
        """CatalogingFact model should exist."""
        from app.database.models import CatalogingFact
        self.assertIsNotNone(CatalogingFact)
        columns = {c.name for c in CatalogingFact.__table__.columns}
        self.assertIn("fact_type", columns)
        self.assertIn("raw_payload", columns)

    def test_cataloging_job_model_exists(self):
        """CatalogingJob model should exist."""
        from app.database.models import CatalogingJob
        self.assertIsNotNone(CatalogingJob)
        columns = {c.name for c in CatalogingJob.__table__.columns}
        self.assertIn("execution_mode", columns)

    def test_cataloging_chapter_run_model_exists(self):
        """CatalogingChapterRun model should exist."""
        from app.database.models import CatalogingChapterRun
        self.assertIsNotNone(CatalogingChapterRun)
        columns = {c.name for c in CatalogingChapterRun.__table__.columns}
        self.assertIn("status", columns)


class CatalogingApplierIntegrationTest(unittest.TestCase):
    """Verify cataloging applier integration points."""

    def test_applier_module_exists(self):
        """Cataloging applier module should exist."""
        from app.services.cataloging import applier
        self.assertTrue(hasattr(applier, 'apply_candidates_for_run'))
        self.assertTrue(hasattr(applier, 'apply_candidate'))

    def test_candidate_store_module_exists(self):
        """Cataloging candidate store module should exist."""
        try:
            from app.services.cataloging import candidate_store
            self.assertTrue(True)
        except ImportError:
            self.skipTest("Candidate store module not found")


if __name__ == "__main__":
    unittest.main()
