"""Focused coverage for auditable task-context governance."""
import asyncio
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import AgentRun, Base, Chapter, ContextManifestItem, ModelContextProfile, OutlineNode, Project
from app.services.context_orchestrator import ContextOrchestrator


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

    def test_unknown_model_uses_conservative_window_and_hard_budget(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="unknown-provider:unknown-model",
            arguments={"outline_node_id": "o1", "requirements": "Write the opening."},
        )
        self.assertEqual(manifest.context_window_tokens, 16384)
        self.assertEqual(manifest.input_budget_tokens, 8500)
        self.assertLessEqual(manifest.estimated_input_tokens, manifest.input_budget_tokens)
        self.assertEqual(manifest.status, "ready")
        self.assertTrue(any("Unknown model context profile" in warning for warning in manifest.warnings_json))

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

        target = next(item for item in manifest.items if item.category == "target_outline")
        partial = self.service.submit_evidence(manifest, [{
            "source_type": target.source_type,
            "source_id": target.source_id,
            "source_hash": target.source_hash,
        }])
        self.assertEqual(partial["accepted_count"], 1)
        self.assertFalse(self.service.validate(manifest, require_external_evidence=True)[0])

        required_sources = [
            {
                "source_type": item.source_type,
                "source_id": item.source_id,
                "source_hash": item.source_hash,
            }
            for item in manifest.items
            if item.required
        ]
        result = self.service.submit_evidence(manifest, required_sources)
        self.assertEqual(result["accepted_count"], len(required_sources))
        self.assertTrue(self.service.validate(manifest, require_external_evidence=True)[0])

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
            "arguments": {"chapter_id": first_chapter.id},
        }))
        second = asyncio.run(prepare_task_context(self.db, "p1", {
            "task_type": "cataloging",
            "run_id": run.id,
            "arguments": {"chapter_id": second_chapter.id},
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

    def test_external_route_cannot_bypass_evidence_with_internal_manifest(self):
        from app.services.workspace.tools.chapters import create_chapter

        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            execution_route="internal_api",
            arguments={"outline_node_id": "o1"},
        )
        blocked = asyncio.run(create_chapter(self.db, "p1", {
            "title": "Opening",
            "content": "The protagonist crosses the city gate.",
            "context_manifest_id": manifest.id,
            "_context_execution_route": "external_mcp",
            "skip_style_repair": True,
        }))
        self.assertEqual(blocked["status"], "needs_confirmation")

        self.service.submit_evidence(manifest, [
            {
                "source_type": item.source_type,
                "source_id": item.source_id,
                "source_hash": item.source_hash,
            }
            for item in manifest.items
            if item.required
        ])
        with patch("app.services.workspace.tools.chapters.sync_chapter_to_file"):
            created = asyncio.run(create_chapter(self.db, "p1", {
                "title": "Opening",
                "content": "The protagonist crosses the city gate.",
                "context_manifest_id": manifest.id,
                "_context_execution_route": "external_mcp",
                "skip_style_repair": True,
            }))
        self.assertEqual(created["status"], "ok")


if __name__ == "__main__":
    unittest.main()
