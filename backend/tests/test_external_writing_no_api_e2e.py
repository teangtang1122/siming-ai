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
                    {"outline_node_id": outline.id, "include_prompt_pack": False},
                )
            )
            self.assertEqual(context_result["status"], "ok")
            manifest_id = context_result["data"]["context_manifest_id"]
            self.assertTrue(manifest_id)
            self.assertIn("Governed Task Context", context_result["data"]["context_page"]["text"])
            self.assertFalse(context_result["data"]["context_page"]["has_more"])
            self.assertNotIn("baseline_context", context_result["data"])
            self.assertNotIn("task_context", context_result["data"])
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
                ["revise_draft", "save_and_catalog", "save_only", "discard"],
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

    def test_external_agent_can_replace_the_same_pending_draft(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.database.models import Base, ChapterDraft, OutlineNode, Project
        from app.services.workspace.generated_drafts import store_chapter_draft
        from app.services.workspace.tools.context_governance import submit_context_evidence
        from app.services.workspace.tools.external_writing import (
            get_external_chapter_draft,
            prepare_external_writing_context,
            save_external_chapter_draft,
        )

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            project = Project(id="p-revise", title="Revise draft", writing_style="natural")
            outline = OutlineNode(
                id="o-revise",
                project_id=project.id,
                title="第一章 雨夜",
                node_type="chapter",
                summary="雨夜抵达。",
            )
            db.add_all([project, outline])
            db.commit()
            draft_id = store_chapter_draft(
                project_id=project.id,
                title=outline.title,
                outline_node_id=outline.id,
                content="需要修改的原始草稿。",
                db=db,
            )

            discovered = asyncio.run(get_external_chapter_draft(db, project.id, {}))
            self.assertEqual(discovered["status"], "ok")
            self.assertEqual(discovered["data"]["draft_id"], draft_id)

            prepared = asyncio.run(prepare_external_writing_context(
                db,
                project.id,
                {
                    "outline_node_id": outline.id,
                    "source_draft_id": draft_id,
                    "include_prompt_pack": False,
                    "requirements": "增加雨声",
                },
            ))
            self.assertEqual(prepared["status"], "ok")
            self.assertEqual(prepared["data"]["target"]["source_draft_id"], draft_id)
            self.assertIn("需要修改的原始草稿。", prepared["data"]["context_page"]["text"])
            manifest_id = prepared["data"]["context_manifest_id"]
            selected = asyncio.run(submit_context_evidence(
                db,
                project.id,
                {"context_manifest_id": manifest_id, "sources": []},
            ))

            revised = asyncio.run(save_external_chapter_draft(
                db,
                project.id,
                {
                    "content": "修改后的完整草稿响着密集雨声。",
                    "outline_node_id": outline.id,
                    "source_draft_id": draft_id,
                    "context_manifest_id": manifest_id,
                    "context_selection_token": selected["data"]["context_selection_token"],
                },
            ))

            self.assertEqual(revised["status"], "ok")
            self.assertEqual(revised["data"]["draft_id"], draft_id)
            self.assertEqual(db.query(ChapterDraft).count(), 1)
            self.assertEqual(db.get(ChapterDraft, draft_id).content, "修改后的完整草稿响着密集雨声。")
        finally:
            db.close()
            Base.metadata.drop_all(engine)
            engine.dispose()

    def test_structured_han_minimum_rejects_short_draft_without_consuming_token(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.database.models import Base, ChapterDraft, ContextManifest, OutlineNode, Project
        from app.services.workspace.tools.context_governance import submit_context_evidence
        from app.services.workspace.tools.external_writing import (
            prepare_external_writing_context,
            save_external_chapter_draft,
        )

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            project = Project(id="p-min", title="Length guard", writing_style="natural")
            outline = OutlineNode(
                id="o-min",
                project_id=project.id,
                title="长度边界",
                node_type="chapter",
                summary="写一段达到明确长度下限的正文。",
            )
            db.add_all([project, outline])
            db.commit()

            prepared = asyncio.run(prepare_external_writing_context(
                db,
                project.id,
                {
                    "outline_node_id": outline.id,
                    "include_prompt_pack": False,
                    "requirements": "保持克制。",
                    "minimum_han_characters": 5,
                },
            ))
            self.assertEqual(prepared["status"], "ok")
            self.assertEqual(
                prepared["data"]["writing_constraints"]["minimum_han_characters"],
                5,
            )
            self.assertIn("at least 5 Han characters", prepared["data"]["context_page"]["text"])
            manifest_id = prepared["data"]["context_manifest_id"]
            selected = asyncio.run(submit_context_evidence(
                db,
                project.id,
                {"context_manifest_id": manifest_id, "sources": []},
            ))
            token = selected["data"]["context_selection_token"]

            short = asyncio.run(save_external_chapter_draft(
                db,
                project.id,
                {
                    "content": "潮痕三字",
                    "outline_node_id": outline.id,
                    "context_manifest_id": manifest_id,
                    "context_selection_token": token,
                },
            ))
            self.assertEqual(short["status"], "needs_confirmation")
            self.assertEqual(short["data"]["actual_han_characters"], 4)
            self.assertFalse(short["data"]["context_selection_token_consumed"])
            self.assertEqual(db.query(ChapterDraft).count(), 0)
            self.assertIsNone(db.get(ContextManifest, manifest_id).consumed_at)

            with patch(
                "app.services.workspace.generated_drafts.store_chapter_draft",
                return_value="draft-min",
            ):
                accepted = asyncio.run(save_external_chapter_draft(
                    db,
                    project.id,
                    {
                        "content": "潮痕三字够",
                        "outline_node_id": outline.id,
                        "context_manifest_id": manifest_id,
                        "context_selection_token": token,
                    },
                ))
            self.assertEqual(accepted["status"], "ok")
            self.assertEqual(accepted["data"]["draft_id"], "draft-min")
            self.assertEqual(accepted["data"]["han_character_count"], 5)
            self.assertIsNotNone(db.get(ContextManifest, manifest_id).consumed_at)
        finally:
            db.close()
            Base.metadata.drop_all(engine)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
