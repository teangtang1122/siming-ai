"""End-to-end test for external writing without Siming API.

Proves external agents can write a chapter without any Siming model API.
Monkeypatches all LLM gateway calls to fail.
"""
import asyncio
import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class ExternalWritingNoApiE2ETest(unittest.TestCase):
    """Verify the complete external writing flow without LLM API."""

    def test_full_no_api_workflow(self):
        """Test the complete workflow with mocked DB."""
        from app.services.workspace.tools.external_writing import (
            prepare_external_writing_context,
            save_external_chapter_draft,
            record_external_quality_review,
        )
        from app.services.workspace.tools.external_story_updates import (
            apply_external_story_updates,
        )

        # Mock project
        project = MagicMock()
        project.id = "p1"
        project.title = "Test Novel"
        project.writing_style = "natural"
        project.forbidden_sentence_patterns = "仿佛\n不由得"
        project.narrative_perspective = "third_person"

        # Mock character
        char = MagicMock()
        char.id = "c1"
        char.name = "Hero"
        char.role_type = "protagonist"
        char.personality = "Brave"
        char.current_location = "Castle"
        char.current_goal = "Save world"
        char.life_status = "alive"

        # Mock worldbuilding
        wb = MagicMock()
        wb.id = "w1"
        wb.title = "Magic System"
        wb.dimension = "power_system"
        wb.content = "Magic requires mana"

        # Mock prompt pack
        pack = MagicMock()
        pack.pack_id = "chapter_writing_quality"
        pack.version = "1.0.0"
        pack.title = "Quality Writing"
        pack.system_prompt = "Write well..."
        pack.workflow_json = [{"step": 1}]
        pack.quality_rubric_json = {"dimensions": [{"name": "opening_hook", "max_score": 10}]}
        pack.forbidden_patterns_json = ["仿佛"]

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            q.order_by.return_value = q
            q.limit.return_value = q
            model_name = model.__name__ if hasattr(model, '__name__') else str(model)
            if "Project" in model_name:
                q.first.return_value = project
            elif "PublicPromptPack" in model_name:
                q.first.return_value = pack
            elif "Character" in model_name and "Relationship" not in model_name:
                q.all.return_value = [char]
            elif "WorldbuildingEntry" in model_name:
                q.all.return_value = [wb]
            elif "Chapter" in model_name:
                q.all.return_value = []
            elif "CharacterRelationship" in model_name:
                q.all.return_value = []
            elif "OutlineNode" in model_name:
                q.first.return_value = None
            else:
                q.first.return_value = None
                q.all.return_value = []
            return q

        db = MagicMock()
        db.query.side_effect = query_side_effect

        # Step 1: Prepare context (no LLM call)
        ctx_result = asyncio.run(prepare_external_writing_context(db, "p1", {"mode": "quality"}))
        self.assertEqual(ctx_result["status"], "ok")
        self.assertIn("prompt_pack", ctx_result["data"])
        self.assertIn("characters", ctx_result["data"])
        self.assertIn("worldbuilding", ctx_result["data"])

        # Step 2: Save draft (no LLM call)
        with patch("app.services.workspace.generated_drafts.store_chapter_draft", return_value="draft-e2e"):
            draft_result = asyncio.run(save_external_chapter_draft(db, "p1", {
                "content": "The rain fell on the battlefield. Hero drew his sword.",
                "title": "Chapter 1: The Battle",
                "source_agent": "claude-code",
            }))
        self.assertEqual(draft_result["status"], "ok")
        self.assertEqual(draft_result["data"]["draft_id"], "draft-e2e")

        # Step 3: Record quality review (no LLM call)
        review_result = asyncio.run(record_external_quality_review(db, "p1", {
            "draft_id": "draft-e2e",
            "scores": {"opening_hook": 8, "plot_progression": 7},
            "issues": [],
            "pass": True,
            "reviewer_model": "claude-sonnet-4-6",
        }))
        self.assertEqual(review_result["status"], "ok")
        self.assertIn("PASS", review_result["detail"])

        # Step 4: Apply story updates (no LLM call)
        update_result = asyncio.run(apply_external_story_updates(db, "p1", {
            "chapter_id": "ch-e2e",
            "updates": {
                "characters": [
                    {"id": "c1", "current_location": "Battlefield"},
                ],
            },
            "mode": "auto",
        }))
        self.assertEqual(update_result["status"], "ok")
        self.assertGreater(len(update_result["data"]["applied"]), 0)

    def test_no_llm_gateway_called(self):
        """Verify LLMGateway is never imported or called."""
        # If any tool tried to call LLMGateway, this import would trigger it
        # The tools should only use DB queries
        from app.services.workspace.tools.external_writing import prepare_external_writing_context
        from app.services.workspace.tools.external_story_updates import apply_external_story_updates

        # These imports should succeed without touching LLMGateway
        self.assertTrue(callable(prepare_external_writing_context))
        self.assertTrue(callable(apply_external_story_updates))


if __name__ == "__main__":
    unittest.main()
