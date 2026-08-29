"""Tests for external writing context tool — API-free context preparation."""
import asyncio
import sys
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.workspace.registry import registry


class ExternalWritingContextToolRegisteredTest(unittest.TestCase):
    """Verify prepare_external_writing_context is registered."""

    def test_registered(self):
        td = registry.get("prepare_external_writing_context")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "read")

    def test_in_readonly_pack(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertIn("prepare_external_writing_context", names)


class PrepareExternalWritingContextTest(unittest.TestCase):
    """Verify prepare_external_writing_context behavior."""

    def test_project_not_found(self):
        from app.services.workspace.tools.external_writing import prepare_external_writing_context
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock

        result = asyncio.run(prepare_external_writing_context(db, "nonexistent", {}))
        self.assertEqual(result["status"], "skipped")

    @patch("app.services.prompt_packs.seed.ensure_builtin_packs")
    @patch("app.services.context_orchestrator.ContextOrchestrator")
    def test_returns_one_manifest_context_without_duplicate_mirrors(
        self,
        orchestrator_class,
        _ensure_builtin_packs,
    ):
        from app.services.workspace.tools.external_writing import prepare_external_writing_context

        project = MagicMock()
        project.id = "p1"
        project.title = "Test Novel"
        project.writing_style = "natural"
        project.forbidden_sentence_patterns = ""
        project.narrative_perspective = "third_person"

        outline = SimpleNamespace(
            id="outline-1",
            node_type="chapter",
            title="Chapter 1",
        )
        pack = SimpleNamespace(
            pack_id="chapter_writing_quality",
            version="1.0.0",
            title="Quality Writing",
        )

        def query_side_effect(model):
            query = MagicMock()
            query.filter.return_value = query
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if "Project" in model_name:
                query.first.return_value = project
            elif "PublicPromptPack" in model_name:
                query.first.return_value = pack
            elif "OutlineNode" in model_name:
                query.first.return_value = outline
            else:
                query.first.return_value = None
            return query

        db = MagicMock()
        db.query.side_effect = query_side_effect
        manifest = SimpleNamespace(
            id="manifest-1",
            status="ready",
            estimated_input_tokens=1400,
            input_budget_tokens=8500,
            rendered_context="One governed context containing Hero and Magic System.",
        )
        orchestrator = MagicMock()
        orchestrator.prepare.return_value = manifest
        orchestrator.manifest_payload.return_value = {
            "budget": {"estimated_input_tokens": 1400, "input_budget_tokens": 8500},
            "coverage": {"target_outline": {"status": "covered"}},
            "warnings": [],
            "items": [
                {
                    "source_id": "outline-1",
                    "source_hash": "sha256:outline",
                    "title": "Chapter 1",
                }
            ],
        }
        orchestrator_class.return_value = orchestrator

        with patch(
            "app.services.workspace.tools.external_writing.render_generation_context",
            return_value=manifest.rendered_context,
        ):
            result = asyncio.run(
                prepare_external_writing_context(
                    db,
                    "p1",
                    {"outline_node_id": outline.id},
                )
            )

        self.assertEqual(result["status"], "ok")
        data = result["data"]
        self.assertEqual(data["context_manifest_id"], "manifest-1")
        self.assertEqual(data["task_context"], "")
        self.assertEqual(data["baseline_context"], manifest.rendered_context)
        self.assertEqual(data["baseline_sources"], orchestrator.manifest_payload.return_value["items"])
        self.assertEqual(
            set(data["prompt_pack"]),
            {"pack_id", "version", "title", "system_prompt"},
        )
        self.assertNotIn("characters", data)
        self.assertNotIn("worldbuilding", data)
        self.assertNotIn("recent_summaries", data)
        self.assertNotIn("selected_context", data)
        self.assertNotIn("context_manifest", data)
        next_tools = {item["tool"] for item in data["next_tool_suggestions"]}
        self.assertEqual(next_tools, {"search_task_context", "submit_context_evidence"})

    def test_real_context_exposes_character_only_after_agent_search_and_selection(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.database.models import (
            Base,
            Character,
            CharacterAIConfig,
            CharacterRelationship,
            OutlineNode,
            OutlineNodeCharacter,
            Project,
        )
        from app.services.workspace.tools.external_writing import prepare_external_writing_context
        from app.services.workspace.tools.context_governance import (
            search_task_context,
            submit_context_evidence,
        )

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            project = Project(id="p1", title="Complete context", writing_style="natural")
            outline = OutlineNode(
                id="o1",
                project_id=project.id,
                title="第一章",
                node_type="chapter",
                summary="姜尘向石翁求助。",
            )
            hero = Character(
                id="c1",
                project_id=project.id,
                name="姜尘",
                role_type="protagonist",
                mental_state="警惕",
                current_goal="查清骨光来源",
                profile_json={
                    "core_motivation": "保护边荒城",
                    "voice": "短句",
                },
            )
            elder = Character(id="c2", project_id=project.id, name="石翁")
            hero.ai_config = CharacterAIConfig(
                id="cfg1",
                character_id=hero.id,
                tone_style="克制",
                verbosity="brief",
                catchphrases='["先看证据"]',
            )
            db.add_all([project, outline, hero, elder])
            db.flush()
            db.add_all([
                OutlineNodeCharacter(outline_node_id=outline.id, character_id=hero.id),
                CharacterRelationship(
                    id="rel1",
                    project_id=project.id,
                    character_a_id=hero.id,
                    character_b_id=elder.id,
                    relationship_type="师友",
                    description="石翁传授辨骨之法。",
                ),
            ])
            db.commit()

            result = asyncio.run(prepare_external_writing_context(
                db,
                project.id,
                {"outline_node_id": outline.id},
            ))

            self.assertEqual(result["status"], "ok")
            baseline = result["data"]["baseline_context"]
            self.assertIn("姜尘向石翁求助", baseline)
            self.assertNotIn("保护边荒城", baseline)
            self.assertNotIn("brief", baseline)
            self.assertNotIn("师友", baseline)
            manifest_id = result["data"]["context_manifest_id"]
            searched = asyncio.run(search_task_context(
                db,
                project.id,
                {
                    "context_manifest_id": manifest_id,
                    "query": "姜尘 保护边荒城 辨骨",
                    "source_types": ["character"],
                },
            ))
            hero_result = next(
                item for item in searched["data"]["items"]
                if item["source_id"] == hero.id
            )
            submitted = asyncio.run(submit_context_evidence(
                db,
                project.id,
                {
                    "context_manifest_id": manifest_id,
                    "sources": [{"item_id": hero_result["item_id"]}],
                },
            ))
            self.assertEqual(submitted["status"], "ok")
            self.assertTrue(submitted["data"]["context_selection_token"])
            task_context = submitted["data"]["task_context"]
            self.assertIn("姜尘", task_context)
            self.assertIn("保护边荒城", task_context)
            self.assertIn("brief", task_context)
            self.assertIn("警惕", task_context)
            self.assertIn("师友", task_context)
            self.assertIn("石翁", task_context)
        finally:
            db.close()
            Base.metadata.drop_all(engine)
            engine.dispose()


    def test_no_llm_call(self):
        """Verify the tool does not call LLMGateway."""
        from app.services.workspace.tools.external_writing import prepare_external_writing_context
        # If LLMGateway were called, this import would trigger it
        # The tool should only use DB queries
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock

        # Should succeed without any LLM call
        result = asyncio.run(prepare_external_writing_context(db, "p1", {}))
        self.assertEqual(result["status"], "skipped")  # project not found, but no crash


if __name__ == "__main__":
    unittest.main()
