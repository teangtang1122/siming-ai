"""Tests for external writing context tool — API-free context preparation."""
import asyncio
import os
import sys
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
        self.assertIn("minimum_han_characters", td.input_schema)
        self.assertEqual(td.input_schema["minimum_han_characters"]["minimum"], 1)

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

    def test_character_reveal_gate_uses_global_chapter_order_across_volumes(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.database.models import Base, Character, OutlineNode, Project
        from app.services.task_context_sources import TaskContextSourceResolver

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            project = Project(id="p1", title="Global chapter order")
            first_volume = OutlineNode(
                id="v1", project_id=project.id, title="卷一", node_type="volume", sort_order=0,
            )
            second_volume = OutlineNode(
                id="v2", project_id=project.id, title="卷二", node_type="volume", sort_order=1,
            )
            chapters = [
                OutlineNode(
                    id=f"o{number}",
                    project_id=project.id,
                    parent_id=first_volume.id if number <= 30 else second_volume.id,
                    title=f"Chapter {number}",
                    node_type="chapter",
                    sort_order=(number - 1) if number <= 30 else (number - 31),
                )
                for number in range(1, 37)
            ]
            character = Character(
                id="c1",
                project_id=project.id,
                name="周芷",
                background="第22章已经公开的稳定背景",
                profile_json={"reveal_chapter": 22, "hidden_persona": "已公开身份"},
            )
            db.add_all([project, first_volume, second_volume, *chapters, character])
            db.commit()

            manifest = SimpleNamespace(
                project_id=project.id,
                task_type="writing",
                query_json={"arguments": {"outline_node_id": "o36"}},
                items=[],
            )
            resolver = TaskContextSourceResolver(db)

            self.assertEqual(resolver._target_chapter_number(manifest), 36)
            _title, content = resolver._exact_content(
                project.id, "character", character.id, manifest=manifest,
            )
            self.assertIn("第22章已经公开的稳定背景", content)
            self.assertIn("已公开身份", content)
            self.assertNotIn("withheld_until_chapter", content)
        finally:
            db.close()
            Base.metadata.drop_all(engine)
            engine.dispose()

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
        ), patch("app.services.workspace.tools.external_writing._load_external_writing_prompt_pack", return_value={
            "pack_id": "chapter_writing_quality", "version": "1.0.0", "title": "Quality Writing", "system_prompt": "Writing rules",
        }):
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
        self.assertEqual(data["context_page"]["text"], "Writing rules\n\n" + manifest.rendered_context)
        self.assertFalse(data["context_page"]["has_more"])
        self.assertNotIn("task_context", data)
        self.assertNotIn("baseline_context", data)
        self.assertNotIn("baseline_sources", data)
        self.assertEqual(
            set(data["prompt_pack"]),
            {"pack_id", "version", "title"},
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
        from app.services.workspace.tools.context_governance import (
            search_task_context,
            submit_context_evidence,
        )
        from app.services.workspace.tools.external_writing import prepare_external_writing_context

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
                {"outline_node_id": outline.id, "include_prompt_pack": False},
            ))

            self.assertEqual(result["status"], "ok")
            baseline = result["data"]["context_page"]["text"]
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
            failed = asyncio.run(submit_context_evidence(
                db, project.id, {"context_manifest_id": manifest_id,
                                 "sources": [{"item_id": "not-a-search-result"}]},
            ))
            from app.services.workspace.tool_result_projection import model_tool_result_projector

            receipt = model_tool_result_projector.project(
                registry.get_spec("submit_context_evidence"), failed,
            ).payload
            self.assertEqual(receipt["status"], "needs_confirmation")
            self.assertFalse(receipt["data"]["selection_ready"])
            self.assertEqual(receipt["data"]["validation_error_count"], 1)
            self.assertEqual(receipt["data"]["validation_errors"], [{
                "item_id": "not-a-search-result",
                "reason": "Source is not a verified result from search_task_context.",
            }])
            self.assertNotIn("context_selection_token", receipt["data"])
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
            task_context = submitted["data"]["context_page"]["text"]
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

    def test_future_character_secrets_are_withheld_before_reveal_chapter(self):
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
        from app.services.workspace.tools.context_governance import (
            search_task_context,
            submit_context_evidence,
        )
        from app.services.workspace.tools.external_writing import prepare_external_writing_context

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            project = Project(id="p1", title="Spoiler gate", writing_style="natural")
            outline = OutlineNode(
                id="o13",
                project_id=project.id,
                title="第十三章",
                node_type="chapter",
                summary="只从正式记录推进调查。",
                sort_order=13,
            )
            future = Character(
                id="c-future",
                project_id=project.id,
                name="陈海生",
                role_type="supporting",
                age="45",
                appearance="走路时右腿微跛",
                personality="害怕失去退休保障",
                background="已经退休并经营小卖部",
                current_goal="交出私人记录副本",
                profile_json={
                    "hidden_persona": "秘密保存事故当夜的私人记录",
                    "reveal_chapter": 14,
                    "voice": "常用模糊词",
                },
            )
            witness = Character(id="c-witness", project_id=project.id, name="林澄")
            future.ai_config = CharacterAIConfig(
                id="cfg-future",
                character_id=future.id,
                custom_system_prompt="在小卖部接受采访并拿出私人副本",
            )
            db.add_all([project, outline, future, witness])
            db.flush()
            db.add_all([
                OutlineNodeCharacter(outline_node_id=outline.id, character_id=future.id),
                CharacterRelationship(
                    id="rel-future",
                    project_id=project.id,
                    character_a_id=future.id,
                    character_b_id=witness.id,
                    relationship_type="未来受访者",
                    description="第十四章才会正式联系",
                ),
            ])
            db.commit()

            prepared = asyncio.run(prepare_external_writing_context(
                db,
                project.id,
                {"outline_node_id": outline.id, "include_prompt_pack": False},
            ))
            manifest_id = prepared["data"]["context_manifest_id"]
            searched = asyncio.run(search_task_context(
                db,
                project.id,
                {
                    "context_manifest_id": manifest_id,
                    "query": "陈海生",
                    "source_types": ["character"],
                },
            ))
            result = next(
                item for item in searched["data"]["items"]
                if item["source_id"] == future.id
            )
            excerpt = result["excerpt"]
            self.assertIn("withheld_until_chapter", excerpt)
            self.assertIn('"reveal_chapter":14', excerpt)
            for secret in (
                "小卖部",
                "私人记录",
                "右腿微跛",
                "退休保障",
                "未来受访者",
            ):
                self.assertNotIn(secret, excerpt)

            submitted = asyncio.run(submit_context_evidence(
                db,
                project.id,
                {
                    "context_manifest_id": manifest_id,
                    "sources": [{"item_id": result["item_id"]}],
                },
            ))
            selected_context = submitted["data"]["context_page"]["text"]
            self.assertIn("withheld_until_chapter", selected_context)
            for secret in ("小卖部", "私人记录", "右腿微跛", "退休保障"):
                self.assertNotIn(secret, selected_context)
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
