"""Model-driven outline planning must stop at an author-visible draft."""

from __future__ import annotations

import asyncio
import hashlib
import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, Character, OutlineDraft, OutlineNode, Project
from app.services.context_orchestrator import ContextOrchestrator
from app.services.workspace.outline_drafts import confirm_outline_draft
from app.services.workspace.tools.external_writing import save_external_outline_draft
from app.services.workspace.tools.outline_writer import outline_writer


class OutlineDraftGenerationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add(Project(id="p1", title="Draft project", writing_style="natural"))
        self.db.add(
            OutlineNode(
                id="o1",
                project_id="p1",
                title="Chapter One",
                node_type="chapter",
                summary="The first gate opens.",
            )
        )
        self.db.add(
            Character(
                id="secret-character",
                project_id="p1",
                name="Never Auto Load Me",
                background="SECRET CHARACTER DATA",
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def planning_context(self, batch_count=1):
        orchestrator = ContextOrchestrator(self.db)
        manifest = orchestrator.prepare(
            project_id="p1",
            task_type="outline_planning",
            model="openai:test",
            arguments={
                "insert_after_id": "o1",
                "batch_count": batch_count,
                "requirements": "Plan the next chapter.",
            },
        )
        selected = orchestrator.submit_evidence(manifest, [])
        self.assertTrue(selected["selection_ready"])
        return manifest, selected["context_selection_token"]

    @patch(
        "app.services.workspace.tools.outline_writer.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    @patch(
        "app.services.workspace.tools.outline_writer.LLMGateway.local_cli_extra_body",
        return_value={},
    )
    def test_internal_writer_uses_exact_context_and_persists_no_formal_node(
        self,
        _extra_body,
        completion: AsyncMock,
    ) -> None:
        manifest, token = self.planning_context()
        completion.return_value = {
            "content": "",
            "tool_calls": [
                {
                    "id": "call-outline-1",
                    "type": "function",
                    "function": {
                        "name": "propose_outline_nodes",
                        "arguments": json.dumps(
                            {
                                "nodes": [
                                    {
                                        "title": "Chapter Two",
                                        "node_type": "chapter",
                                        "summary": "The second gate falls.",
                                        "character_names": [],
                                        "status": "pending",
                                    }
                                ],
                                "design_notes": "Escalate once.",
                            }
                        ),
                    }
                }
            ],
        }

        result = asyncio.run(
            outline_writer(
                self.db,
                "p1",
                {
                    "context_manifest_id": manifest.id,
                    "context_selection_token": token,
                    "insert_after_id": "o1",
                    "batch_count": 1,
                    "requirements": "Plan the next chapter.",
                },
            )
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["turn_directive"], "end_after_outline_draft")
        self.assertTrue(result["turn_terminal"])
        self.assertEqual(self.db.query(OutlineNode).filter_by(project_id="p1").count(), 1)
        draft = self.db.query(OutlineDraft).filter_by(project_id="p1").one()
        self.assertEqual(result["data"]["draft_id"], draft.id)
        self.assertEqual(draft.nodes_json[0]["title"], "Chapter Two")
        self.assertEqual(draft.nodes_json[0]["planned_summary"], "The second gate falls.")
        self.assertEqual(draft.nodes_json[0]["actual_summary"], "")
        self.assertEqual(
            draft.context_selection_digest,
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
        )
        self.assertNotEqual(draft.context_selection_digest, token)
        self.assertIsNotNone(manifest.consumed_at)

        call = completion.await_args
        rendered_prompt = json.dumps(call.kwargs["messages"], ensure_ascii=False)
        self.assertIn("outline_position", rendered_prompt)
        self.assertNotIn("SECRET CHARACTER DATA", rendered_prompt)
        self.assertEqual(call.kwargs["max_tokens"], manifest.output_reserve_tokens)

        completion.reset_mock()
        blocked = asyncio.run(
            outline_writer(
                self.db,
                "p1",
                {
                    "context_manifest_id": manifest.id,
                    "context_selection_token": token,
                    "insert_after_id": "o1",
                },
            )
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["turn_directive"], "blocked_on_outline_draft")
        completion.assert_not_awaited()

    @patch(
        "app.services.workspace.tools.outline_writer.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_internal_writer_rejects_unobserved_selection_token(
        self,
        completion: AsyncMock,
    ) -> None:
        manifest, _token = self.planning_context()

        result = asyncio.run(
            outline_writer(
                self.db,
                "p1",
                {
                    "context_manifest_id": manifest.id,
                    "context_selection_token": "guessed",
                    "insert_after_id": "o1",
                },
            )
        )

        self.assertEqual(result["status"], "needs_confirmation")
        self.assertEqual(self.db.query(OutlineDraft).count(), 0)
        completion.assert_not_awaited()

    @patch(
        "app.services.workspace.tools.outline_writer.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_internal_writer_rejects_unsupported_model_without_consuming_selection(
        self,
        completion: AsyncMock,
    ) -> None:
        manifest, token = self.planning_context()
        manifest.provider = "codex_cli"
        manifest.model = "test"
        self.db.commit()

        result = asyncio.run(
            outline_writer(
                self.db,
                "p1",
                {
                    "context_manifest_id": manifest.id,
                    "context_selection_token": token,
                    "insert_after_id": "o1",
                },
            )
        )

        self.db.refresh(manifest)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["data"]["reason"], "tool_capability_unavailable")
        self.assertIn("tool_capability_unavailable", result["detail"])
        self.assertIsNone(manifest.consumed_at)
        self.assertEqual(self.db.query(OutlineDraft).count(), 0)
        completion.assert_not_awaited()

    @patch(
        "app.services.workspace.tools.outline_writer.store_outline_draft",
    )
    @patch(
        "app.services.workspace.tools.outline_writer.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    @patch(
        "app.services.workspace.tools.outline_writer.LLMGateway.local_cli_extra_body",
        return_value={},
    )
    def test_internal_writer_rejects_json_content_without_native_tool_call(
        self,
        _extra_body,
        completion: AsyncMock,
        store_draft,
    ) -> None:
        manifest, token = self.planning_context()
        completion.return_value = {
            "content": json.dumps(
                {
                    "nodes": [
                        {
                            "title": "Must not persist",
                            "node_type": "chapter",
                            "summary": "Looks valid but is ordinary assistant text.",
                            "character_names": [],
                            "status": "pending",
                        }
                    ],
                    "design_notes": "Not a native tool call.",
                }
            ),
            "tool_calls": [],
        }

        result = asyncio.run(
            outline_writer(
                self.db,
                "p1",
                {
                    "context_manifest_id": manifest.id,
                    "context_selection_token": token,
                    "insert_after_id": "o1",
                    "batch_count": 1,
                    "requirements": "Plan the next chapter.",
                },
            )
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(
            result["data"]["protocol_error"],
            "required_native_tool_call_missing_or_ambiguous",
        )
        self.assertEqual(self.db.query(OutlineDraft).count(), 0)
        store_draft.assert_not_called()

    def test_external_agent_saves_the_same_draft_type_and_stops(self) -> None:
        manifest, token = self.planning_context()

        result = asyncio.run(
            save_external_outline_draft(
                self.db,
                "p1",
                {
                    "context_manifest_id": manifest.id,
                    "context_selection_token": token,
                    "insert_after_id": "o1",
                    "nodes": [
                        {
                            "title": "Chapter Two",
                            "node_type": "chapter",
                            "summary": "A reviewed external proposal.",
                            "character_names": [],
                        }
                    ],
                    "design_notes": "External Agent.",
                },
            )
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["turn_directive"], "end_after_outline_draft")
        self.assertEqual(self.db.query(OutlineNode).filter_by(project_id="p1").count(), 1)
        self.assertEqual(self.db.query(OutlineDraft).filter_by(project_id="p1").count(), 1)

    def test_external_top_level_nodes_may_repeat_the_formal_parent_title(self) -> None:
        volume = OutlineNode(
            id="volume-two",
            project_id="p1",
            title="Second Volume",
            node_type="volume",
            sort_order=1000,
        )
        self.db.add(volume)
        chapter = self.db.query(OutlineNode).filter_by(id="o1").one()
        chapter.parent_id = volume.id
        self.db.commit()

        orchestrator = ContextOrchestrator(self.db)
        manifest = orchestrator.prepare(
            project_id="p1",
            task_type="outline_planning",
            model="openai:test",
            arguments={
                "parent_id": volume.id,
                "insert_after_id": chapter.id,
                "batch_count": 1,
                "requirements": "Plan one chapter in the second volume.",
            },
        )
        selected = orchestrator.submit_evidence(manifest, [])

        result = asyncio.run(
            save_external_outline_draft(
                self.db,
                "p1",
                {
                    "context_manifest_id": manifest.id,
                    "context_selection_token": selected["context_selection_token"],
                    "parent_id": volume.id,
                    "insert_after_id": chapter.id,
                    "nodes": [
                        {
                            "title": "Chapter Two",
                            "node_type": "chapter",
                            "summary": "Continue inside the selected formal volume.",
                            "parent_title": volume.title,
                            "character_names": [],
                        }
                    ],
                },
            )
        )

        self.assertEqual(result["status"], "ok")
        draft = self.db.query(OutlineDraft).one()
        self.assertEqual(draft.parent_id, volume.id)
        self.assertEqual(draft.insert_after_id, chapter.id)
        self.assertNotIn("parent_title", draft.nodes_json[0])

    def test_confirm_links_existing_characters_and_preserves_future_names_without_creating_them(self) -> None:
        manifest, token = self.planning_context()
        saved = asyncio.run(
            save_external_outline_draft(
                self.db,
                "p1",
                {
                    "context_manifest_id": manifest.id,
                    "context_selection_token": token,
                    "insert_after_id": "o1",
                    "nodes": [
                        {
                            "title": "Chapter Two",
                            "node_type": "chapter",
                            "summary": "An existing ally meets a person who has not entered the story yet.",
                            "character_names": ["Never Auto Load Me", "Future New Role"],
                            "metadata": {"author_note": "Keep this note."},
                        }
                    ],
                },
            )
        )
        draft = self.db.query(OutlineDraft).filter_by(id=saved["data"]["draft_id"]).one()

        confirmed = asyncio.run(confirm_outline_draft(self.db, "p1", draft.id))

        self.assertEqual(confirmed["draft_status"], "confirmed")
        self.assertEqual(self.db.query(Character).filter_by(project_id="p1").count(), 1)
        self.assertIsNone(
            self.db.query(Character).filter_by(project_id="p1", name="Future New Role").first()
        )
        formal = self.db.query(OutlineNode).filter_by(project_id="p1", title="Chapter Two").one()
        self.assertEqual(
            [link.character.name for link in formal.linked_characters],
            ["Never Auto Load Me"],
        )
        self.assertEqual(formal.metadata_json["author_note"], "Keep this note.")
        self.assertEqual(
            formal.metadata_json["planned_character_names"],
            ["Never Auto Load Me", "Future New Role"],
        )
        self.assertEqual(
            formal.metadata_json["unlinked_planned_character_names"],
            ["Future New Role"],
        )
        self.assertEqual(
            confirmed["nodes"][0]["metadata"],
            formal.metadata_json,
        )

        from app.services.planned_character_links import resolve_planned_outline_character_links

        future = Character(project_id="p1", name="Future New Role", role_type="supporting")
        self.db.add(future)
        self.db.flush()

        resolved = resolve_planned_outline_character_links(self.db, future)
        self.db.flush()
        self.db.refresh(formal)

        self.assertEqual(resolved, [formal.id])
        self.assertEqual(
            [link.character.name for link in formal.linked_characters],
            ["Never Auto Load Me", "Future New Role"],
        )
        self.assertEqual(
            formal.metadata_json["unlinked_planned_character_names"],
            [],
        )

    def test_external_invalid_batch_can_be_corrected_without_consuming_context(self):
        manifest, token = self.planning_context(batch_count=6)
        nodes = [
            {"title": f"第{index}章 新线索", "node_type": "chapter", "summary": f"未来规划{index}",
             "actual_summary": "尚未发生，不能写入事实", "source_chapter_id": "unwritten",
             "cataloging_status": "completed", "character_names": []}
            for index in range(4, 10)
        ]
        args = {"context_manifest_id": manifest.id, "context_selection_token": token,
                "insert_after_id": "o1"}
        for invalid in (json.dumps(nodes), nodes[:2], ["not an object"] * 6):
            result = asyncio.run(save_external_outline_draft(self.db, "p1", {**args, "nodes": invalid}))
            self.db.refresh(manifest)
            self.assertEqual(result["status"], "error")
            self.assertIsNone(manifest.consumed_at)
            self.assertEqual(self.db.query(OutlineDraft).count(), 0)
        result = asyncio.run(save_external_outline_draft(self.db, "p1", {**args, "nodes": nodes}))
        self.assertEqual(result["status"], "ok")
        draft = self.db.query(OutlineDraft).one()
        self.assertEqual(len(draft.nodes_json), 6)
        for node in draft.nodes_json:
            self.assertEqual(node["actual_summary"], "")
            self.assertEqual(node["planned_summary"], node["summary"])
            self.assertNotIn("source_chapter_id", node)
            self.assertNotIn("cataloging_status", node)
        asyncio.run(confirm_outline_draft(self.db, project_id="p1", draft_id=draft.id))
        formal = self.db.query(OutlineNode).filter(OutlineNode.id != "o1").all()
        self.assertEqual({node.title for node in formal}, {node["title"] for node in nodes})
        for node in formal:
            self.assertFalse(node.actual_summary)
            self.assertEqual(node.planned_summary, node.summary)
            self.assertIsNone(node.source_chapter_id)
            self.assertIsNone(node.cataloging_status)

    def test_external_agent_can_save_eleven_node_request_without_contract_drift(self):
        manifest, token = self.planning_context(batch_count=11)
        position = next(
            item for item in manifest.items if item.category == "outline_position"
        )
        self.assertIn('"batch_count": 11', position.content_excerpt)
        nodes = [
            {
                "title": f"Future Chapter {index}",
                "node_type": "chapter",
                "summary": f"Plan {index}.",
                "character_names": [],
            }
            for index in range(2, 13)
        ]

        result = asyncio.run(
            save_external_outline_draft(
                self.db,
                "p1",
                {
                    "context_manifest_id": manifest.id,
                    "context_selection_token": token,
                    "insert_after_id": "o1",
                    "nodes": nodes,
                },
            )
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["data"]["nodes"]), 11)
        draft = self.db.query(OutlineDraft).one()
        self.assertEqual(len(draft.nodes_json), 11)

        confirmed = asyncio.run(confirm_outline_draft(self.db, "p1", draft.id))

        self.assertEqual(len(confirmed["chapter_outline_node_ids"]), 11)
        self.assertEqual(
            self.db.query(OutlineNode).filter_by(project_id="p1").count(), 12
        )

    @patch("app.services.workspace.tools.outline_writer.LLMGateway.local_cli_extra_body", return_value={})
    @patch("app.services.workspace.tools.outline_writer.LLMGateway.chat_completion", new_callable=AsyncMock)
    def test_internal_writer_does_not_accept_a_shortened_batch(self, completion, _extra):
        manifest, token = self.planning_context(batch_count=6)
        completion.return_value = {"content": "", "tool_calls": [{"id": "short-outline", "type": "function",
            "function": {"name": "propose_outline_nodes", "arguments": json.dumps({"nodes": [
                {"title": "Only one", "node_type": "chapter", "summary": "Incomplete proposal", "character_names": []}
            ], "design_notes": ""})}}]}
        result = asyncio.run(outline_writer(self.db, "p1", {"context_manifest_id": manifest.id,
            "context_selection_token": token, "insert_after_id": "o1", "batch_count": 6}))
        self.assertEqual(result["status"], "error")
        self.assertIn("6", result["detail"])
        self.assertEqual(self.db.query(OutlineDraft).count(), 0)


if __name__ == "__main__":
    unittest.main()
