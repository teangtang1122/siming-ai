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

    def planning_context(self):
        orchestrator = ContextOrchestrator(self.db)
        manifest = orchestrator.prepare(
            project_id="p1",
            task_type="outline_planning",
            model="openai:test",
            arguments={
                "insert_after_id": "o1",
                "batch_count": 1,
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


if __name__ == "__main__":
    unittest.main()
