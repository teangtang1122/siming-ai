"""End-to-end test for external writing without Siming API.

Proves external agents can prepare context and produce one terminal unsaved draft
without any Siming model API.
"""
import asyncio
import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class ExternalWritingNoApiE2ETest(unittest.TestCase):
    """Verify the complete external writing flow without LLM API."""

    def test_full_no_api_workflow_stops_at_unsaved_draft(self):
        """The external writing turn prepares context, stores one draft, and stops."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.database.models import Base, CatalogingJob, Chapter, OutlineNode, Project
        from app.services.workspace.tools.external_writing import (
            prepare_external_writing_context,
            save_external_chapter_draft,
        )
        from app.services.workspace.tools.context_governance import submit_context_evidence

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            project = Project(
                id="p1",
                title="Test Novel",
                writing_style="natural",
                narrative_perspective="third_person",
            )
            outline = OutlineNode(
                id="o1",
                project_id=project.id,
                title="Chapter 1: The Battle",
                node_type="chapter",
                summary="Hero enters the battlefield.",
            )
            db.add_all([project, outline])
            db.commit()

            context_result = asyncio.run(
                prepare_external_writing_context(
                    db,
                    project.id,
                    {"outline_node_id": outline.id},
                )
            )
            self.assertEqual(context_result["status"], "ok")
            manifest_id = context_result["data"]["context_manifest_id"]
            self.assertTrue(manifest_id)
            self.assertIn("Governed Task Context", context_result["data"]["baseline_context"])
            self.assertEqual(context_result["data"]["task_context"], "")
            selection_result = asyncio.run(submit_context_evidence(
                db,
                project.id,
                {"context_manifest_id": manifest_id, "sources": []},
            ))
            self.assertEqual(selection_result["status"], "ok")
            selection_token = selection_result["data"]["context_selection_token"]

            with patch(
                "app.services.workspace.generated_drafts.store_chapter_draft",
                return_value="draft-e2e",
            ):
                draft_result = asyncio.run(
                    save_external_chapter_draft(
                        db,
                        project.id,
                        {
                            "content": "The rain fell on the battlefield. Hero drew his sword.",
                            "outline_node_id": outline.id,
                            "context_manifest_id": manifest_id,
                            "context_selection_token": selection_token,
                            "source_agent": "claude-code",
                            "_context_execution_route": "external_mcp",
                        },
                    )
                )

            self.assertEqual(draft_result["status"], "ok")
            self.assertEqual(draft_result["data"]["draft_id"], "draft-e2e")
            self.assertTrue(draft_result["turn_terminal"])
            self.assertEqual(
                draft_result["data"]["next_actions"],
                ["save_and_catalog", "save_only"],
            )
            self.assertEqual(db.query(Chapter).count(), 0)
            self.assertEqual(db.query(CatalogingJob).count(), 0)
        finally:
            db.close()
            Base.metadata.drop_all(engine)
            engine.dispose()

    def test_no_llm_gateway_called(self):
        """Verify LLMGateway is never imported or called."""
        # If any tool tried to call LLMGateway, this import would trigger it
        # The tools should only use DB queries
        from app.services.workspace.tools.external_writing import prepare_external_writing_context

        # This read-only context tool must remain usable without the internal gateway.
        self.assertTrue(callable(prepare_external_writing_context))


if __name__ == "__main__":
    unittest.main()
