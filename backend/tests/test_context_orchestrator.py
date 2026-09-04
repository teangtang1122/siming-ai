"""Focused coverage for auditable task-context governance."""
import asyncio
import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    AgentRun,
    APIConfig,
    Base,
    Chapter,
    Character,
    CharacterAIConfig,
    CharacterRelationship,
    CharacterTimeline,
    ContextManifest,
    ContextManifestItem,
    LocalModel,
    ModelContextProfile,
    ModelTaskSetting,
    NarrativeDebt,
    NovelCreationSession,
    OutlineNode,
    Project,
    RagChunk,
    WorldbuildingEntry,
)
from app.modules.model_runtime.application.request_override import use_request_provider
from app.modules.model_runtime.domain.configuration import ModelProviderConfig
from app.services.context_orchestrator import TASK_CONTEXT_CONTRACTS, ContextOrchestrator
from app.services.conversation_context.assembly import resolve_generation_model_binding
from app.services.task_context_sources import TaskContextSourceResolver


class ContextOrchestratorTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.db.add(Project(id="p1", title="Test project", writing_style="natural"))
        self.db.add(OutlineNode(
            id="o1",
            project_id="p1",
            title="Opening",
            node_type="chapter",
            summary="The protagonist crosses the city gate and sees the enemy banner.",
        ))
        self.db.commit()
        self.service = ContextOrchestrator(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_unknown_remote_model_uses_256k_fallback_and_hard_budget(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="unknown-provider:unknown-model",
            arguments={"outline_node_id": "o1", "requirements": "Write the opening."},
        )
        self.assertEqual(manifest.context_window_tokens, 256_000)
        self.assertEqual(manifest.output_reserve_tokens, 16_000)
        self.assertEqual(
            manifest.input_budget_tokens,
            manifest.context_window_tokens
            - manifest.output_reserve_tokens
            - manifest.safety_margin_tokens,
        )
        self.assertGreater(manifest.input_budget_tokens, 32_000)
        self.assertLessEqual(manifest.estimated_input_tokens, manifest.input_budget_tokens)
        self.assertEqual(manifest.status, "ready")
        self.assertTrue(any("temporary 256K fallback" in warning for warning in manifest.warnings_json))

    def test_outline_planning_uses_only_position_style_and_model_selected_evidence(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="outline_planning",
            model="openai:test",
            arguments={
                "insert_after_id": "o1",
                "batch_count": 3,
                "requirements": "Plan the next three chapters.",
            },
        )

        self.assertEqual(manifest.status, "ready")
        self.assertEqual(
            {item.category for item in manifest.items},
            {"style", "outline_position", "user_requirement"},
        )
        position = next(item for item in manifest.items if item.category == "outline_position")
        self.assertIn('"insert_after_id": "o1"', position.content_excerpt)
        self.assertIn('"batch_count": 3', position.content_excerpt)

        selected = self.service.submit_evidence(manifest, [])
        self.assertTrue(selected["selection_ready"])
        self.assertIn("outline_position", selected["task_context"])
        usable, detail = self.service.validate_task_selection(
            manifest,
            task_type="outline_planning",
            token=selected["context_selection_token"],
            parent_id=None,
            insert_after_id="o1",
        )
        self.assertTrue(usable, detail)

    def test_outline_position_change_invalidates_planning_selection(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="outline_planning",
            model="openai:test",
            arguments={"insert_after_id": "o1"},
        )
        selected = self.service.submit_evidence(manifest, [])
        volume = OutlineNode(
            id="volume-1",
            project_id="p1",
            title="Volume One",
            node_type="volume",
        )
        self.db.add(volume)
        self.db.flush()
        outline = self.db.query(OutlineNode).filter(OutlineNode.id == "o1").one()
        outline.parent_id = volume.id
        self.db.flush()

        usable, detail = self.service.validate_task_selection(
            manifest,
            task_type="outline_planning",
            token=selected["context_selection_token"],
            parent_id=None,
            insert_after_id="o1",
        )
        self.assertFalse(usable)
        self.assertIn("changed", detail.lower())

    def test_context_selection_token_is_single_use_but_can_be_reselected(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )
        selected = self.service.submit_evidence(manifest, [])
        token = selected["context_selection_token"]
        self.assertTrue(self.service.mark_consumed(manifest))

        usable, detail = self.service.validate_task_selection(
            manifest,
            token=token,
            task_type="writing",
            outline_node_id="o1",
        )
        self.assertFalse(usable)
        self.assertIn("consumed", detail)
        self.assertFalse(self.service.mark_consumed(manifest))

        reselection = self.service.submit_evidence(manifest, [])
        self.assertTrue(reselection["selection_ready"])
        self.assertNotEqual(reselection["context_selection_token"], token)
        self.assertIsNone(manifest.consumed_at)

    def test_model_can_select_more_than_twenty_four_small_sources_when_budget_allows(self):
        self.db.add_all(
            [
                Character(
                    id=f"character-{index}",
                    project_id="p1",
                    name=f"{'Alpha' if index < 20 else 'Beta'} Witness {index}",
                    personality="brief witness record",
                )
                for index in range(25)
            ]
        )
        self.db.commit()
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )
        resolver = TaskContextSourceResolver(self.db)
        search_items = []
        for index in range(25):
            item = ContextManifestItem(
                manifest_id=manifest.id,
                project_id="p1",
                category="agent_search",
                source_type="character",
                source_id=f"character-{index}",
                chunk_id=f"test-character-{index}",
                source_hash="pending",
                title=f"Witness {index}",
                content_excerpt=f"Verified candidate {index}",
                sort_order=100 + index,
            )
            exact = resolver.exact_source(manifest, item)
            self.assertIsNotNone(exact)
            item.source_hash = exact.source_hash
            manifest.items.append(item)
            search_items.append(item)
        self.db.flush()

        selected = self.service.submit_evidence(
            manifest,
            [{"item_id": item.id} for item in search_items],
        )
        self.assertTrue(selected["selection_ready"])
        self.assertEqual(selected["accepted_count"], 25)

    def test_superseded_worldbuilding_cannot_be_pinned_from_a_lingering_rag_chunk(self):
        active = WorldbuildingEntry(
            id="world-active",
            project_id="p1",
            dimension="history",
            title="Current record",
            content="The current verified record.",
            status="active",
        )
        superseded = WorldbuildingEntry(
            id="world-superseded",
            project_id="p1",
            dimension="history",
            title="Old wrong record",
            content="A withdrawn and incorrect account.",
            status="superseded",
        )
        self.db.add_all([
            active,
            superseded,
            RagChunk(
                id="chunk-active",
                document_id="document-active",
                project_id="p1",
                source_type="worldbuilding",
                source_id=active.id,
                title=active.title,
                content=active.content,
            ),
            RagChunk(
                id="chunk-superseded",
                document_id="document-superseded",
                project_id="p1",
                source_type="worldbuilding",
                source_id=superseded.id,
                title=superseded.title,
                content=superseded.content,
            ),
        ])
        self.db.commit()

        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
            pinned_source_ids=[active.id, superseded.id],
        )

        pinned = [item for item in manifest.items if item.category == "pinned"]
        self.assertEqual([item.source_id for item in pinned], [active.id])
        self.assertNotIn(superseded.content, manifest.rendered_context)
        self.assertEqual(manifest.coverage_json["pinned"]["item_count"], 1)

    def test_semantic_search_hides_superseded_worldbuilding_lingering_chunks(self):
        active = WorldbuildingEntry(
            id="world-active",
            project_id="p1",
            dimension="history",
            title="Current record",
            content="The current verified record.",
            status="active",
        )
        superseded = WorldbuildingEntry(
            id="world-superseded",
            project_id="p1",
            dimension="history",
            title="Old wrong record",
            content="A withdrawn and incorrect account.",
            status="superseded",
        )
        self.db.add_all([
            active,
            superseded,
            RagChunk(
                id="chunk-active",
                document_id="document-active",
                project_id="p1",
                source_type="worldbuilding",
                source_id=active.id,
                title=active.title,
                content=active.content,
            ),
            RagChunk(
                id="chunk-superseded",
                document_id="document-superseded",
                project_id="p1",
                source_type="worldbuilding",
                source_id=superseded.id,
                title=superseded.title,
                content=superseded.content,
            ),
        ])
        self.db.commit()

        with (
            patch(
                "app.services.context_orchestrator.search_chunks",
                return_value=[],
            ),
            patch.object(
                self.service,
                "_semantic_scores",
                return_value={"chunk-active": 0.5, "chunk-superseded": 1.0},
            ),
        ):
            results = self.service._hybrid_candidates("p1", "record", {})

        self.assertEqual([item.source_id for item in results], [active.id])

    def test_task_search_returns_each_authoritative_source_only_once(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )

        def candidate(source_id: str, chunk_id: str, score: float):
            return SimpleNamespace(
                source_type="character",
                source_id=source_id,
                chunk_id=chunk_id,
                source_hash=f"hash-{source_id}",
                title=f"角色 {source_id}",
                content=f"角色 {source_id} 的候选片段 {chunk_id}",
                lexical_score=score,
                semantic_score=None,
                recency_score=None,
                structural_score=None,
                final_score=score,
            )

        with patch.object(
            self.service,
            "_hybrid_candidates",
            return_value=[
                candidate("chen", "chen-1", 1.0),
                candidate("chen", "chen-2", 0.9),
                candidate("chen", "chen-3", 0.8),
                candidate("zhou", "zhou-1", 0.7),
            ],
        ):
            results = self.service.search_task_context(
                manifest,
                query="陈海生",
                source_types=["character"],
            )

        self.assertEqual([item["source_id"] for item in results], ["chen", "zhou"])
        self.assertEqual(results[0]["chunk_id"], "chen-1")

    def test_task_search_preview_hydrates_current_worldbuilding_not_stale_rag_text(self):
        entry = WorldbuildingEntry(
            id="world-current",
            project_id="p1",
            dimension="culture",
            title="通信签收表",
            content="当前权威内容：呼叫栏18:50；旧结论已经撤回。",
            status="active",
        )
        self.db.add(entry)
        self.db.commit()
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )
        exact = TaskContextSourceResolver(self.db).exact_identity_source(
            manifest,
            "worldbuilding",
            entry.id,
        )
        self.assertIsNotNone(exact)
        stale = SimpleNamespace(
            source_type="worldbuilding",
            source_id=entry.id,
            chunk_id="stale-world-chunk",
            source_hash=exact.source_hash,
            title="通信签收表（旧索引）",
            content="旧索引错误：18:38值班员口头报时已经证实。",
            lexical_score=1.0,
            semantic_score=None,
            recency_score=1.0,
            structural_score=0.25,
            final_score=0.9,
        )

        with patch.object(self.service, "_hybrid_candidates", return_value=[stale]):
            results = self.service.search_task_context(
                manifest,
                query="签收",
                source_types=["worldbuilding"],
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "通信签收表")
        self.assertIn("呼叫栏18:50", results[0]["excerpt"])
        self.assertNotIn("18:38值班员口头报时", results[0]["excerpt"])
        stored = next(item for item in manifest.items if item.category == "agent_search")
        self.assertIn("当前权威内容", stored.content_excerpt)
        self.assertNotIn("旧索引错误", stored.content_excerpt)

    def test_selected_exact_source_is_not_cut_by_a_fixed_character_limit(self):
        marker = "正文末尾不可丢失标记"
        chapter = Chapter(
            id="long-exact-source",
            project_id="p1",
            title="长篇精确资料",
            content="长篇资料" * 4_000 + marker,
        )
        self.db.add(chapter)
        self.db.commit()
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )
        candidates = self.service.search_task_context(
            manifest,
            query=marker,
            source_types=["chapter"],
        )
        candidate = next(item for item in candidates if item["source_id"] == chapter.id)

        selected = self.service.submit_evidence(
            manifest,
            [{"item_id": candidate["item_id"]}],
        )

        self.assertTrue(selected["selection_ready"])
        self.assertIn(marker, selected["task_context"])
        exact_item = next(
            item for item in manifest.items if item.category == "agent_selected"
        )
        self.assertGreater(len(exact_item.content_excerpt), 12_000)

    def test_deepseek_creation_budget_uses_registered_large_output_capacity(self):
        self.db.add(
            APIConfig(
                provider="deepseek",
                api_key_encrypted="encrypted-placeholder",
                default_model="deepseek-v3",
                base_url_override="https://api.deepseek.com/v1",
            )
        )
        self.db.commit()
        profile = self.service.resolve_model_profile(
            "deepseek:deepseek-v3",
            "new_project",
        )
        budget = self.service.budget_for(TASK_CONTEXT_CONTRACTS["new_project"], profile)

        self.assertTrue(profile.known)
        self.assertEqual(profile.model_name, "deepseek-v4-flash")
        self.assertEqual(profile.context_window_tokens, 1_000_000)
        self.assertEqual(profile.max_output_tokens, 384_000)
        self.assertEqual(budget.output_reserve_tokens, 300_000)
        self.assertEqual(budget.hard_input_budget_tokens, 699_488)

    def test_documented_deepseek_identity_is_known_before_config_lookup(self):
        profile = self.service.resolve_model_profile(
            "deepseek:deepseek-v4-flash",
            "assistant",
        )

        self.assertTrue(profile.known)
        self.assertEqual(profile.context_window_tokens, 1_000_000)
        self.assertEqual(profile.max_output_tokens, 384_000)

    def test_documented_cloud_model_is_sendable_without_manual_profile(self):
        self.db.add(APIConfig(
            provider="openai",
            api_key_encrypted="encrypted-placeholder",
            default_model="gpt-4o",
        ))
        self.db.commit()

        profile = self.service.resolve_model_profile("openai:gpt-4o", "writing")
        binding, counter, margin = resolve_generation_model_binding(
            orchestrator=self.service,
            model="openai:gpt-4o",
            task_type="writing",
            protocol="chat_completions",
            system_prompt="system",
            current_tools=(),
        )

        self.assertTrue(profile.known)
        self.assertEqual(profile.context_window_tokens, 128_000)
        self.assertEqual(profile.max_output_tokens, 16_384)
        self.assertEqual(binding.capacity_assurance.value, "conservative")
        self.assertEqual(binding.context_window_tokens, 128_000)
        self.assertEqual(counter.counter_id, "conservative.utf8_bytes.v1")
        self.assertEqual(margin, 512)

    def test_documented_model_name_on_custom_proxy_remains_unverified(self):
        self.db.add(APIConfig(
            provider="openai",
            api_key_encrypted="encrypted-placeholder",
            default_model="gpt-4o",
            base_url_override="https://proxy.example/v1",
        ))
        self.db.commit()

        profile = self.service.resolve_model_profile("openai:gpt-4o", "writing")

        self.assertFalse(profile.known)

    def test_manual_profile_overrides_documented_capacity(self):
        self.db.add(ModelContextProfile(
            provider="openai",
            model_name="gpt-4o",
            context_window_tokens=64_000,
            max_output_tokens=4_000,
            safety_margin_tokens=1_024,
        ))
        self.db.commit()

        profile = self.service.resolve_model_profile("openai:gpt-4o", "writing")

        self.assertTrue(profile.known)
        self.assertEqual(profile.context_window_tokens, 64_000)
        self.assertEqual(profile.max_output_tokens, 4_000)
        self.assertEqual(profile.safety_margin_tokens, 1_024)

    def test_manual_profile_overrides_unknown_model_fallback(self):
        self.db.add(ModelContextProfile(
            provider="custom_vendor",
            model_name="writer-private",
            context_window_tokens=96_000,
            max_output_tokens=8_000,
            safety_margin_tokens=1_024,
        ))
        self.db.commit()

        profile = self.service.resolve_model_profile(
            "custom_vendor:writer-private",
            "writing",
        )
        binding, counter, margin = resolve_generation_model_binding(
            orchestrator=self.service,
            model="custom_vendor:writer-private",
            task_type="writing",
            protocol="chat_completions",
            system_prompt="system",
            current_tools=(),
        )

        self.assertTrue(profile.known)
        self.assertEqual(profile.context_window_tokens, 96_000)
        self.assertEqual(profile.max_output_tokens, 8_000)
        self.assertEqual(profile.safety_margin_tokens, 1_024)
        self.assertEqual(binding.capacity_assurance.value, "conservative")
        self.assertEqual(binding.context_window_tokens, 96_000)
        self.assertEqual(counter.counter_id, "conservative.utf8_bytes.v1")
        self.assertEqual(margin, 1_024)

    def test_persistent_profile_overrides_mobile_request_capacity(self):
        self.db.add(ModelContextProfile(
            provider="mobile_openai",
            model_name="phone-model",
            context_window_tokens=96_000,
            max_output_tokens=8_000,
            safety_margin_tokens=1_024,
        ))
        self.db.commit()
        for assurance in ("conservative", "unverified"):
            with self.subTest(assurance=assurance):
                request_provider = ModelProviderConfig(
                    provider="mobile_openai",
                    default_model="phone-model",
                    api_key="request-only-secret",
                    base_url="https://api.example.test/v1",
                    context_window_tokens=256_000,
                    max_output_tokens=6_000,
                    safety_margin_tokens=4_096,
                    capacity_assurance=assurance,
                )

                with use_request_provider(request_provider):
                    profile = self.service.resolve_model_profile(
                        "mobile_openai:phone-model",
                        "writing",
                    )

                self.assertTrue(profile.known)
                self.assertEqual(profile.context_window_tokens, 96_000)
                self.assertEqual(profile.max_output_tokens, 8_000)
                self.assertEqual(profile.safety_margin_tokens, 1_024)

    def test_provider_discovery_metadata_is_a_verified_exact_capacity(self):
        self.db.add(APIConfig(
            provider="custom_vendor",
            api_key_encrypted="encrypted-placeholder",
            default_model="writer-large",
            available_models_json=[{
                "id": "writer-large",
                "display_name": "Writer Large",
                "context_window_tokens": 96_000,
                "max_output_tokens": 8_000,
                "safety_margin_tokens": 1_024,
                "capacity_source": "provider_models_api",
            }],
        ))
        self.db.commit()

        profile = self.service.resolve_model_profile(
            "custom_vendor:writer-large",
            "writing",
        )

        self.assertTrue(profile.known)
        self.assertEqual(profile.context_window_tokens, 96_000)
        self.assertEqual(profile.max_output_tokens, 8_000)
        self.assertEqual(profile.safety_margin_tokens, 1_024)

    def test_provider_metadata_output_is_capped_to_the_verified_window(self):
        self.db.add(APIConfig(
            provider="custom_vendor",
            api_key_encrypted="encrypted-placeholder",
            default_model="writer-small",
            max_output_tokens=8_000,
            available_models_json=[{
                "id": "writer-small",
                "context_window_tokens": 4_096,
                "safety_margin_tokens": 512,
                "capacity_source": "provider_models_api",
            }],
        ))
        self.db.commit()

        profile = self.service.resolve_model_profile(
            "custom_vendor:writer-small",
            "writing",
        )

        self.assertTrue(profile.known)
        self.assertEqual(profile.context_window_tokens, 4_096)
        self.assertEqual(profile.max_output_tokens, 3_583)

    def test_implausible_provider_metadata_remains_unverified(self):
        self.db.add(APIConfig(
            provider="custom_vendor",
            api_key_encrypted="encrypted-placeholder",
            default_model="writer-impossible",
            available_models_json=[{
                "id": "writer-impossible",
                "context_window_tokens": 100_000_000,
                "max_output_tokens": 8_000,
                "capacity_source": "provider_models_api",
            }],
        ))
        self.db.commit()

        profile = self.service.resolve_model_profile(
            "custom_vendor:writer-impossible",
            "writing",
        )

        self.assertFalse(profile.known)

    def test_formal_creation_brief_is_a_required_writing_style_anchor(self):
        creation = NovelCreationSession(
            id="creation-p1",
            created_project_id="p1",
            status="completed",
            revision=4,
            draft_json={
                "form": {
                    "target_words": 2_500_000,
                    "target_chapters": 1_000,
                    "writing_style": "克制冷峻，以动作推进",
                    "special_requirements": ["升级必须有代价"],
                },
                "stages": {
                    "constraints": {
                        "status": "confirmed",
                        "data": {
                            "target_words": 2_500_000,
                            "target_chapters": 1_000,
                            "writing_style": "克制冷峻，以动作推进",
                            "special_requirements": ["升级必须有代价"],
                        },
                    },
                    "concepts": {
                        "status": "confirmed",
                        "data": {
                            "selected_concept_id": "concept-1",
                            "options": [{
                                "id": "concept-1",
                                "title": "经脉迷局",
                                "core_conflict": "求真与宗族秩序冲突",
                            }],
                        },
                    },
                    "world_style": {
                        "status": "confirmed",
                        "data": {"style_rules": ["少解释，多可验证细节"]},
                    },
                },
            },
        )
        self.db.add(creation)
        self.db.commit()

        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )

        style = next(item for item in manifest.items if item.category == "style")
        self.assertTrue(style.required)
        self.assertIn("2500000", style.content_excerpt)
        self.assertIn("1000", style.content_excerpt)
        self.assertIn("经脉迷局", style.content_excerpt)
        self.assertIn("少解释，多可验证细节", style.content_excerpt)

        updated = dict(creation.draft_json)
        updated["form"] = {**updated["form"], "target_chapters": 1_200}
        updated["stages"] = {
            **updated["stages"],
            "constraints": {
                **updated["stages"]["constraints"],
                "data": {
                    **updated["stages"]["constraints"]["data"],
                    "target_chapters": 1_200,
                },
            },
        }
        creation.draft_json = updated
        creation.revision = 5
        self.db.flush()

        usable, detail = self.service.validate(manifest)
        self.assertFalse(usable)
        self.assertEqual(manifest.status, "stale")
        self.assertIn("Source changed", detail)

    def test_local_model_manifest_uses_task_context_instead_of_fixed_16k(self):
        self.db.add(LocalModel(
            model_key="local-qwen",
            display_name="Local Qwen",
            context_length=262144,
            status="installed",
        ))
        self.db.add(ModelTaskSetting(
            task_type="writing",
            provider="local_llama_cpp",
            model_name="local-qwen",
            context_length=8192,
        ))
        self.db.commit()

        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="local_llama_cpp:local-qwen",
            arguments={"outline_node_id": "o1"},
        )

        self.assertEqual(manifest.context_window_tokens, 8192)
        self.assertTrue(manifest.contract_json["model_profile_known"])

    def test_local_model_default_tracks_hardware_and_model_capacity(self):
        self.db.add(LocalModel(
            model_key="small-context",
            display_name="Small Context",
            context_length=12000,
            status="installed",
        ))
        self.db.commit()

        with patch(
            "app.services.local_runtime.hardware.detect_hardware",
            return_value=type("Profile", (), {"recommended_context": 32768})(),
        ), patch(
            "app.services.local_runtime.manager.LocalRuntimeManager.status",
            return_value={"running": False},
        ):
            profile = self.service.resolve_model_profile(
                "local_llama_cpp:small-context",
                "planning",
            )

        self.assertEqual(profile.context_window_tokens, 12000)
        self.assertTrue(profile.known)

    def test_local_context_profile_cannot_exceed_runtime_task_setting(self):
        self.db.add(LocalModel(
            model_key="local-qwen",
            display_name="Local Qwen",
            context_length=262144,
            status="installed",
        ))
        self.db.add(ModelTaskSetting(
            task_type="cataloging",
            provider="local_llama_cpp",
            model_name="local-qwen",
            context_length=16384,
        ))
        self.db.add(ModelContextProfile(
            provider="local_llama_cpp",
            model_name="local-qwen",
            context_window_tokens=65536,
            safety_margin_tokens=512,
        ))
        self.db.commit()

        profile = self.service.resolve_model_profile(
            "local_llama_cpp:local-qwen",
            "cataloging",
        )

        self.assertEqual(profile.context_window_tokens, 16384)

    def test_writing_manifest_loads_full_character_only_after_model_selection(self):
        hero = Character(
            id="c-hero",
            project_id="p1",
            name="姜尘",
            role_type="protagonist",
            current_location="边荒城",
            current_goal="查清遗骨异动",
            mental_state="警惕但克制",
            profile_json={
                "core_motivation": "保护城中百姓",
                "voice": "短句、少解释",
                "moral_taboo": "不以无辜者为饵",
            },
        )
        elder = Character(
            id="c-elder",
            project_id="p1",
            name="石翁",
            role_type="supporting",
        )
        hero.ai_config = CharacterAIConfig(
            id="cfg-hero",
            character_id=hero.id,
            tone_style="沉静克制",
            catchphrases='["先看证据"]',
            verbosity="brief",
            emotion_tendency="外冷内热",
            custom_system_prompt="遇到风险先观察再行动。",
        )
        self.db.add_all([hero, elder])
        self.db.flush()
        self.db.add(CharacterRelationship(
            id="rel-hero-elder",
            project_id="p1",
            character_a_id=hero.id,
            character_b_id=elder.id,
            relationship_type="师友",
            description="石翁传授姜尘辨骨之法。",
        ))
        self.db.commit()

        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )
        self.assertFalse(any(item.source_type == "character" for item in manifest.items))
        self.assertNotIn("姜尘", manifest.rendered_context)

        candidates = self.service.search_task_context(
            manifest,
            query="姜尘 保护边荒城 辨骨",
            source_types=["character"],
        )
        hero_candidate = next(item for item in candidates if item["source_id"] == hero.id)
        selected = self.service.submit_evidence(
            manifest,
            [{"item_id": hero_candidate["item_id"]}],
        )

        self.assertTrue(selected["selection_ready"])
        item = next(item for item in manifest.items if item.category == "agent_selected")
        self.assertIn("保护城中百姓", item.content_excerpt)
        self.assertIn("短句、少解释", item.content_excerpt)
        self.assertIn("沉静克制", item.content_excerpt)
        self.assertIn("brief", item.content_excerpt)
        self.assertIn("石翁", item.content_excerpt)
        self.assertIn("师友", item.content_excerpt)

        hero.ai_config.tone_style = "冷峻直接"
        self.db.flush()
        self.assertEqual(manifest.status, "stale")

    def test_writing_soft_target_warns_without_rejecting_selected_evidence(self):
        chapters = [
            Chapter(
                id=f"soft-chapter-{index}",
                project_id="p1",
                title=f"资料章{index}",
                content=f"唯一标记{index}" + "文" * 12_000,
            )
            for index in range(3)
        ]
        self.db.add_all(chapters)
        self.db.commit()
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )

        item_ids = []
        for index, chapter in enumerate(chapters):
            candidates = self.service.search_task_context(
                manifest,
                query=f"唯一标记{index}",
                source_types=["chapter"],
            )
            candidate = next(item for item in candidates if item["source_id"] == chapter.id)
            item_ids.append(candidate["item_id"])

        result = self.service.submit_evidence(
            manifest,
            [{"item_id": item_id} for item_id in item_ids],
        )

        self.assertTrue(result["selection_ready"])
        self.assertEqual(result["soft_target_tokens"], 32_000)
        self.assertTrue(result["soft_target_exceeded"])
        self.assertGreater(result["estimated_input_tokens"], 32_000)
        self.assertTrue(any("soft target" in warning for warning in result["warnings"]))

    def test_new_character_relationship_invalidates_existing_writing_manifest(self):
        first = Character(id="c-first", project_id="p1", name="甲方")
        second = Character(id="c-second", project_id="p1", name="乙方")
        self.db.add_all([first, second])
        self.db.commit()
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )
        candidates = self.service.search_task_context(
            manifest,
            query="甲方",
            source_types=["character"],
        )
        selected = next(item for item in candidates if item["source_id"] == first.id)
        result = self.service.submit_evidence(manifest, [{"item_id": selected["item_id"]}])
        self.assertTrue(result["selection_ready"])
        self.assertEqual(manifest.status, "ready")

        self.db.add(CharacterRelationship(
            id="rel-new",
            project_id="p1",
            character_a_id=first.id,
            character_b_id=second.id,
            relationship_type="盟友",
        ))
        self.db.flush()

        self.assertEqual(manifest.status, "stale")

    def test_writing_manifest_can_finalize_selected_character_timeline(self):
        character = Character(id="c-timeline", project_id="p1", name="巡城使")
        chapter = Chapter(
            id="ch-timeline",
            project_id="p1",
            title="旧日巡城",
            content="巡城使在旧城墙上负伤。",
        )
        self.db.add_all([character, chapter])
        self.db.flush()
        self.db.add(
            CharacterTimeline(
                id="timeline-event",
                character_id=character.id,
                chapter_id=chapter.id,
                event_type="injury",
                event_description="巡城时被箭矢擦伤左肩",
                emotional_state_change="开始怀疑内应",
            )
        )
        self.db.commit()

        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )
        candidates = self.service.search_task_context(
            manifest,
            query="巡城使 左肩 内应",
            source_types=["character_timeline"],
        )
        timeline = next(item for item in candidates if item["source_id"] == character.id)

        result = self.service.submit_evidence(
            manifest,
            [{"item_id": timeline["item_id"]}],
        )

        self.assertTrue(result["selection_ready"])
        self.assertIn("被箭矢擦伤左肩", result["task_context"])
        self.assertIn("开始怀疑内应", result["task_context"])

    def test_selected_character_timeline_includes_events_older_than_fifty(self):
        character = Character(id="c-long-timeline", project_id="p1", name="长史官")
        chapter = Chapter(
            id="ch-long-timeline",
            project_id="p1",
            title="五十一年旧事",
            content="时间线资料章。",
        )
        marker = "最早时间线事件不可丢失"
        self.db.add_all([character, chapter])
        self.db.flush()
        self.db.add_all(
            [
                CharacterTimeline(
                    id=f"long-timeline-{index}",
                    character_id=character.id,
                    chapter_id=chapter.id,
                    event_type="key_decision",
                    event_description=marker if index == 0 else f"后续事件 {index}",
                    created_at=datetime(2026, 1, 1) + timedelta(days=index),
                )
                for index in range(51)
            ]
        )
        self.db.commit()

        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )
        candidates = self.service.search_task_context(
            manifest,
            query=marker,
            source_types=["character_timeline"],
        )
        timeline = next(item for item in candidates if item["source_id"] == character.id)

        selected = self.service.submit_evidence(
            manifest,
            [{"item_id": timeline["item_id"]}],
        )

        self.assertTrue(selected["selection_ready"])
        self.assertIn(marker, selected["task_context"])

    def test_selected_governance_source_includes_items_beyond_preview_limit(self):
        marker = "低优先级但本章明确需要的治理项"
        self.db.add_all(
            [
                NarrativeDebt(
                    id=f"debt-{index}",
                    project_id="p1",
                    title=marker if index == 12 else f"高优先级债务 {index}",
                    status="open",
                    priority="low" if index == 12 else "critical",
                    dedupe_key=f"debt-key-{index}",
                )
                for index in range(13)
            ]
        )
        self.db.commit()
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )
        candidates = self.service.search_task_context(
            manifest,
            query=marker,
        )
        governance = next(
            item for item in candidates if item["source_type"] == "narrative_governance"
        )

        selected = self.service.submit_evidence(
            manifest,
            [{"item_id": governance["item_id"]}],
        )

        self.assertTrue(selected["selection_ready"])
        self.assertIn(marker, selected["task_context"])

    def test_missing_writing_anchor_requires_confirmation(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"requirements": "Write the opening."},
        )
        self.assertEqual(manifest.status, "needs_confirmation")
        self.assertEqual(manifest.coverage_json["target_outline"]["status"], "missing")

    def test_required_anchor_is_never_silently_removed_by_budget(self):
        self.db.add(ModelContextProfile(
            provider="openai",
            model_name="small",
            context_window_tokens=2600,
            max_output_tokens=2048,
            safety_margin_tokens=512,
        ))
        outline = self.db.query(OutlineNode).filter(OutlineNode.id == "o1").first()
        outline.summary = "x" * 5000
        self.db.commit()

        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:small",
            arguments={"outline_node_id": "o1"},
        )
        self.assertEqual(manifest.status, "needs_confirmation")
        self.assertEqual(manifest.coverage_json["target_outline"]["status"], "missing")
        self.assertLessEqual(manifest.estimated_input_tokens, manifest.input_budget_tokens)

    def test_source_change_marks_manifest_stale(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )
        outline = self.db.query(OutlineNode).filter(OutlineNode.id == "o1").first()
        outline.summary = "A changed outline fact."
        self.db.flush()

        self.assertEqual(manifest.status, "stale")
        usable, detail = self.service.validate(manifest)
        self.assertFalse(usable)
        self.assertEqual(manifest.status, "stale")
        self.assertIn("Source changed", detail)

    def test_override_is_auditable_but_stale_cannot_be_overridden(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={},
        )
        self.service.override(manifest, reason="Author intentionally writes without an outline.", actor="author")
        self.assertEqual(manifest.status, "overridden")
        self.assertEqual(manifest.override_actor, "author")
        self.assertTrue(self.service.validate(manifest)[0])

        manifest.status = "stale"
        with self.assertRaises(ValueError):
            self.service.override(manifest, reason="Ignore stale source.")

    def test_external_formal_write_requires_verified_evidence(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            execution_route="external_mcp",
            arguments={"outline_node_id": "o1"},
        )
        usable, _ = self.service.validate(manifest, require_external_evidence=True)
        self.assertFalse(usable)

        result = self.service.submit_evidence(manifest, [])
        self.assertTrue(result["selection_ready"])
        self.assertTrue(result["context_selection_token"])
        self.assertTrue(self.service.validate(manifest, require_external_evidence=True)[0])

    def test_legacy_auto_context_categories_cannot_enter_writing_generation(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )
        manifest.items.append(ContextManifestItem(
            manifest_id=manifest.id,
            project_id="p1",
            category="scene_character",
            source_type="inline",
            source_id="legacy-auto-character",
            source_hash="legacy",
            title="Legacy auto-loaded character",
            content_excerpt="THIS MUST NEVER ENTER THE WRITING PROMPT",
            sort_order=99,
        ))
        self.db.flush()

        selection = self.service.submit_evidence(manifest, [])

        self.assertTrue(selection["selection_ready"])
        self.assertNotIn("THIS MUST NEVER", selection["task_context"])
        self.assertFalse(any(
            item.category == "scene_character"
            for item in self.service.task_generation_items(manifest)
        ))

    def test_previous_context_policy_manifest_must_be_reprepared(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )
        manifest.policy_version = 2

        usable, detail = self.service.validate(manifest)

        self.assertFalse(usable)
        self.assertEqual(manifest.status, "stale")
        self.assertIn("policy changed", detail)

    def test_new_search_invalidates_finalized_writing_selection(self):
        character = Character(id="c-search", project_id="p1", name="守门人")
        self.db.add(character)
        self.db.commit()
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )
        candidates = self.service.search_task_context(
            manifest,
            query="守门人",
            source_types=["character"],
        )
        selected = self.service.submit_evidence(
            manifest,
            [{"item_id": candidates[0]["item_id"]}],
        )
        old_token = selected["context_selection_token"]
        self.assertTrue(self.service.validate_task_selection(
            manifest,
            task_type="writing",
            token=old_token,
            outline_node_id="o1",
        )[0])

        self.service.search_task_context(
            manifest,
            query="城门 敌军",
            source_types=["outline"],
        )

        payload = self.service.manifest_payload(manifest, include_content=False)
        self.assertEqual(payload["selection"]["status"], "pending")
        self.assertFalse(any(item.category == "agent_selected" for item in manifest.items))
        usable, detail = self.service.validate_task_selection(
            manifest,
            task_type="writing",
            token=old_token,
            outline_node_id="o1",
        )
        self.assertFalse(usable)
        self.assertIn("submit", detail)

    def test_writing_selection_rejects_sources_not_returned_by_search(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )

        result = self.service.submit_evidence(
            manifest,
            [{"source_type": "character", "source_id": "invented-id"}],
        )

        self.assertFalse(result["selection_ready"])
        self.assertEqual(result["accepted_count"], 0)
        self.assertIn("verified result", result["rejected"][0]["reason"])

    def test_writing_selection_uses_only_documented_exact_reference_forms(self):
        self.db.add(Character(
            id="selector-witness",
            project_id="p1",
            name="Selector Witness",
            personality="selector evidence",
        ))
        self.db.commit()

        def searched_manifest():
            manifest = self.service.prepare(
                project_id="p1",
                task_type="writing",
                model="openai:test",
                arguments={"outline_node_id": "o1"},
            )
            item = ContextManifestItem(
                manifest_id=manifest.id,
                project_id="p1",
                category="agent_search",
                source_type="character",
                source_id="selector-witness",
                chunk_id=f"selector-chunk-{manifest.id}",
                source_hash="pending",
                title="Selector Witness",
                content_excerpt="Verified selector candidate",
                sort_order=100,
            )
            exact = TaskContextSourceResolver(self.db).exact_source(manifest, item)
            self.assertIsNotNone(exact)
            item.source_hash = exact.source_hash
            manifest.items.append(item)
            self.db.flush()
            return manifest, item

        for selector in ("item_id", "chunk_id", "source"):
            with self.subTest(selector=selector):
                manifest, item = searched_manifest()
                if selector == "item_id":
                    reference = {"item_id": item.id}
                elif selector == "chunk_id":
                    reference = {"chunk_id": item.chunk_id}
                else:
                    reference = {
                        "source_type": item.source_type,
                        "source_id": item.source_id,
                        "source_hash": item.source_hash,
                    }
                result = self.service.submit_evidence(manifest, [reference])
                self.assertTrue(result["selection_ready"])
                self.assertEqual(result["accepted_count"], 1)

        manifest, item = searched_manifest()
        mixed = self.service.submit_evidence(
            manifest,
            [{"item_id": item.id}, {"id": item.id}],
        )
        self.assertFalse(mixed["selection_ready"])
        self.assertEqual(mixed["accepted_count"], 0)
        self.assertNotIn("context_selection_token", mixed)
        self.assertIn("must include", mixed["rejected"][0]["reason"])

        manifest, item = searched_manifest()
        conflicting = self.service.submit_evidence(
            manifest,
            [{"item_id": item.id, "source_id": "another-source"}],
        )
        self.assertFalse(conflicting["selection_ready"])
        self.assertEqual(conflicting["accepted_count"], 0)
        self.assertIn("verified result", conflicting["rejected"][0]["reason"])

    def test_rebuild_is_resumable_and_does_not_require_semantic_runtime(self):
        job = self.service.create_rebuild_job(requested_by="test")
        self.assertEqual(job.status, "queued")
        self.service.run_rebuild_job(job)
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.completed_projects, 1)
        self.assertEqual(self.service.project_rebuild_block_reason("p1"), "")

        # Startup recovery should observe this completed current-version job
        # rather than queueing and blocking the project again on every launch.
        follow_up = self.service.create_rebuild_job(requested_by="startup")
        self.assertEqual(follow_up.id, job.id)

    def test_search_stays_available_while_rebuild_blocks_generation(self):
        from app.services.workspace.tools.rag_tools import search_context

        job = self.service.create_rebuild_job(requested_by="test")
        self.assertEqual(job.status, "queued")
        result = asyncio.run(search_context(self.db, "p1", {"query": "敌军"}))

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["data"]["rebuild_in_progress"])
        self.assertEqual(result["data"]["manifest_status"], "blocked_rebuild")

    def test_scoped_agent_tasks_get_distinct_manifest_and_prompt_manifest_can_be_reused(self):
        from app.services.workspace.tools.context_governance import prepare_task_context

        first_chapter = Chapter(project_id="p1", title="Chapter one", content="The gate opens.")
        second_chapter = Chapter(project_id="p1", title="Chapter two", content="The enemy arrives.")
        run = AgentRun(project_id="p1", source="mcp", title="cataloging")
        self.db.add_all([first_chapter, second_chapter, run])
        self.db.flush()

        first = asyncio.run(prepare_task_context(self.db, "p1", {
            "task_type": "cataloging",
            "run_id": run.id,
            "chapter_id": first_chapter.id,
        }))
        second = asyncio.run(prepare_task_context(self.db, "p1", {
            "task_type": "cataloging",
            "run_id": run.id,
            "chapter_id": second_chapter.id,
        }))

        first_id = first["data"]["manifest_id"]
        second_id = second["data"]["manifest_id"]
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(run.context_manifest_id, second_id)

        reused = asyncio.run(prepare_task_context(self.db, "p1", {
            "task_type": "cataloging",
            "context_manifest_id": first_id,
        }))
        self.assertEqual(reused["data"]["manifest_id"], first_id)

    def test_run_bound_manifest_recovers_from_invalid_model_supplied_id(self):
        from app.services.workspace.tools.context_governance import (
            prepare_task_context,
            submit_context_evidence,
        )

        run = AgentRun(project_id="p1", source="mcp", title="writing")
        self.db.add(run)
        self.db.flush()
        prepared = asyncio.run(prepare_task_context(self.db, "p1", {
            "task_type": "writing",
            "run_id": run.id,
            "outline_node_id": "o1",
        }))
        manifest_id = prepared["data"]["context_manifest_id"]
        self.assertEqual(run.context_manifest_id, manifest_id)

        submitted = asyncio.run(submit_context_evidence(self.db, "p1", {
            "run_id": run.id,
            "context_manifest_id": "model-invented-manifest-id",
            "sources": [],
        }))
        self.assertNotEqual(submitted["detail"], "Context manifest not found")
        self.assertEqual(submitted["data"]["manifest_id"], manifest_id)

    def test_missing_writing_target_returns_actionable_receipt_without_phantom_manifest(self):
        from app.services.workspace.tools.context_governance import prepare_task_context

        before = self.db.query(ContextManifest).count()
        result = asyncio.run(prepare_task_context(self.db, "p1", {
            "task_type": "writing",
        }))

        self.assertEqual(result["status"], "needs_confirmation")
        self.assertEqual(result["data"]["reason"], "missing_task_anchor")
        self.assertEqual(result["data"]["required_arguments"], ["outline_node_id"])
        self.assertEqual(result["data"]["next_tool"], "prepare_task_context")
        self.assertNotIn("context_manifest_id", result["data"])
        self.assertNotIn("manifest_id", result["data"])
        self.assertEqual(self.db.query(ContextManifest).count(), before)

    def test_mcp_ready_manifest_is_committed_and_bound_to_run(self):
        from app.mcp.adapter import execute_tool

        run = AgentRun(project_id="p1", source="mcp", title="writing")
        self.db.add(run)
        self.db.commit()

        result = asyncio.run(execute_tool(
            self.db,
            "p1",
            "prepare_task_context",
            {
                "task_type": "writing",
                "run_id": run.id,
                "execution_route": "external_mcp",
                "outline_node_id": "o1",
            },
            allowed_tiers={"readonly"},
        ))
        self.assertFalse(result.is_error)
        payload = json.loads(result.content[0]["text"])
        self.assertEqual(payload["status"], "ready")
        manifest_id = payload["data"]["context_manifest_id"]

        # Expire the current identity map and verify both records from the
        # committed database state, matching a later MCP process/tool call.
        self.db.expire_all()
        persisted = self.db.query(ContextManifest).filter(ContextManifest.id == manifest_id).first()
        rebound_run = self.db.query(AgentRun).filter(AgentRun.id == run.id).first()
        self.assertIsNotNone(persisted)
        self.assertEqual(rebound_run.context_manifest_id, manifest_id)

if __name__ == "__main__":
    unittest.main()
