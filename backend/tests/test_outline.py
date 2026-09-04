"""
Test cases for outline planning.

Covers:
  - Outline tree CRUD
  - Character links on outline nodes
  - Reorder and parent moves
  - Cascade delete
  - AI summary suggestion with mocked LLM Gateway
"""

import asyncio
import os
import unittest
from contextlib import suppress
from unittest.mock import MagicMock, patch

os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from fastapi.testclient import TestClient

from app.core.exceptions import ValidationError
from app.database.models import (
    Character,
    OutlineDraft,
    OutlineNode,
    OutlineNodeCharacter,
    Project,
    WorldbuildingEntry,
)
from app.database.session import Base, SessionLocal, engine
from app.main import app
from app.services.outline_service import node_to_dict
from app.services.workspace.outline_drafts import (
    _outline_tree_hash,
    confirm_outline_draft,
)

API_PREFIX = "/api/v1"


class OutlineTestCase(unittest.TestCase):
    """Shared setup for outline API tests."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        with suppress(OSError):
            os.remove("test_novel_agent.db")

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(OutlineDraft).delete()
            db.query(OutlineNodeCharacter).delete()
            db.query(OutlineNode).delete()
            db.query(WorldbuildingEntry).delete()
            db.query(Character).delete()
            db.query(Project).delete()
            db.commit()
        finally:
            db.close()

    def create_project(self, title: str = "Outline Test Novel") -> str:
        response = self.client.post(
            f"{API_PREFIX}/projects",
            json={"title": title, "description": "A project for outline tests."},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["id"]

    def create_character(self, project_id: str, name: str = "Lin Che") -> dict:
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/characters",
            json={"name": name, "role_type": "protagonist", "abilities": ["wind"]},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]

    def create_node(
        self,
        project_id: str,
        title: str,
        node_type: str = "chapter",
        parent_id: str | None = None,
        sort_order: int = 0,
        character_ids: list[str] | None = None,
        metadata: dict | None = None,
    ) -> dict:
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/outline",
            json={
                "title": title,
                "node_type": node_type,
                "parent_id": parent_id,
                "sort_order": sort_order,
                "character_ids": character_ids or [],
                "metadata": metadata,
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]


class TestWorkspaceOutlineBatch(unittest.TestCase):
    def test_oversized_batch_is_rejected_without_silent_truncation(self):
        from app.services.workspace.tools.outline import create_outline_nodes

        nodes = [
            {"node_type": "chapter", "title": f"Chapter {index}"}
            for index in range(1, 14)
        ]
        with patch(
            "app.services.workspace.tools.outline.create_outline_node"
        ) as create_node:
            result = asyncio.run(
                create_outline_nodes(MagicMock(), "p1", {"nodes": nodes})
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["data"]["nodes"], [])
        create_node.assert_not_called()


class TestOutlineCRUD(OutlineTestCase):
    """Outline tree CRUD tests."""

    def test_create_node_materializes_zero_one_two_and_four_character_links(self):
        project_id = self.create_project()
        characters = [
            self.create_character(project_id, f"Character {index}")
            for index in range(1, 5)
        ]

        for link_count in (0, 1, 2, 4):
            with self.subTest(link_count=link_count):
                expected = characters[:link_count]
                response = self.client.post(
                    f"{API_PREFIX}/projects/{project_id}/outline",
                    json={
                        "title": f"Linked Chapter {link_count}",
                        "node_type": "chapter",
                        "sort_order": link_count,
                        "character_ids": [item["id"] for item in expected],
                    },
                )

                self.assertEqual(response.status_code, 200, response.text)
                created = response.json()["data"]
                self.assertEqual(
                    [item["id"] for item in created["linked_characters"]],
                    [item["id"] for item in expected],
                )

                listed = self.client.get(
                    f"{API_PREFIX}/projects/{project_id}/outline"
                ).json()["data"]["flat"]
                persisted = next(item for item in listed if item["id"] == created["id"])
                self.assertEqual(
                    [item["id"] for item in persisted["linked_characters"]],
                    [item["id"] for item in expected],
                )

                db = SessionLocal()
                try:
                    self.assertEqual(
                        db.query(OutlineNodeCharacter)
                        .filter(OutlineNodeCharacter.outline_node_id == created["id"])
                        .count(),
                        link_count,
                    )
                finally:
                    db.close()

    def test_create_node_deduplicates_character_ids_in_input_order(self):
        project_id = self.create_project()
        first = self.create_character(project_id, "First role")
        second = self.create_character(project_id, "Second role")

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/outline",
            json={
                "title": "Deduplicated links",
                "node_type": "chapter",
                "character_ids": [first["id"], second["id"], first["id"]],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item["id"] for item in response.json()["data"]["linked_characters"]],
            [first["id"], second["id"]],
        )

    def test_create_node_with_foreign_character_rolls_back_node_and_links(self):
        project_id = self.create_project("Owner project")
        foreign_project_id = self.create_project("Foreign project")
        foreign_character = self.create_character(foreign_project_id, "Foreign role")

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/outline",
            json={
                "title": "Must not persist",
                "node_type": "chapter",
                "character_ids": [foreign_character["id"]],
            },
        )

        self.assertEqual(response.status_code, 400, response.text)
        db = SessionLocal()
        try:
            self.assertEqual(
                db.query(OutlineNode)
                .filter(
                    OutlineNode.project_id == project_id,
                    OutlineNode.title == "Must not persist",
                )
                .count(),
                0,
            )
            self.assertEqual(
                db.query(OutlineNodeCharacter)
                .filter(OutlineNodeCharacter.character_id == foreign_character["id"])
                .count(),
                0,
            )
        finally:
            db.close()

    def test_node_serializer_tolerates_multiple_pending_character_links(self):
        node = OutlineNode(
            id="pending-outline",
            project_id="project-1",
            title="Pending links",
            node_type="chapter",
        )
        for index in range(2):
            node.linked_characters.append(
                OutlineNodeCharacter(
                    character=Character(
                        id=f"pending-character-{index}",
                        project_id="project-1",
                        name=f"Pending character {index}",
                        role_type="supporting",
                    )
                )
            )

        payload = node_to_dict(node)

        self.assertEqual(len(payload["linked_characters"]), 2)

    def test_create_three_level_tree_with_character_links(self):
        project_id = self.create_project()
        character = self.create_character(project_id)

        volume = self.create_node(project_id, "Volume One", "volume")
        chapter = self.create_node(
            project_id,
            "Chapter One",
            "chapter",
            parent_id=volume["id"],
            character_ids=[character["id"]],
        )
        self.create_node(
            project_id,
            "Opening Scene",
            "section",
            parent_id=chapter["id"],
            metadata={"scene_number": 1, "purpose": "逼迫主角作出选择", "location": "边城门"},
        )

        response = self.client.get(f"{API_PREFIX}/projects/{project_id}/outline")
        self.assertEqual(response.status_code, 200)

        data = response.json()["data"]
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["title"], "Volume One")
        self.assertEqual(data["items"][0]["children"][0]["title"], "Chapter One")
        self.assertEqual(data["items"][0]["children"][0]["children"][0]["title"], "Opening Scene")
        self.assertEqual(data["items"][0]["children"][0]["children"][0]["metadata"]["scene_number"], 1)
        self.assertEqual(data["items"][0]["children"][0]["linked_characters"][0]["name"], "Lin Che")

    def test_update_node_fields_and_linked_characters(self):
        project_id = self.create_project()
        first = self.create_character(project_id, "Lin Che")
        second = self.create_character(project_id, "Shen Hong")
        chapter = self.create_node(project_id, "Draft Chapter", "chapter", character_ids=[first["id"]])
        section = self.create_node(
            project_id,
            "Persisted Scene",
            "section",
            parent_id=chapter["id"],
        )

        response = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/outline/{chapter['id']}",
            json={
                "title": "Storm at the Gate",
                "summary": "The protagonist chooses to defend the border gate.",
                "status": "in_progress",
                "character_ids": [second["id"]],
            },
        )
        self.assertEqual(response.status_code, 200)

        data = response.json()["data"]
        self.assertEqual(data["title"], "Storm at the Gate")
        self.assertEqual(data["status"], "in_progress")
        self.assertEqual(data["linked_characters"][0]["name"], "Shen Hong")
        self.assertEqual([item["id"] for item in data["children"]], [section["id"]])

    def test_http_create_read_and_update_preserve_planned_and_actual_summaries(self):
        project_id = self.create_project()
        created_response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/outline",
            json={
                "title": "Auditable Scene",
                "node_type": "section",
                "summary": "Visible summary",
                "planned_summary": "Author plan",
                "actual_summary": "Initial actual record",
                "status": "completed",
            },
        )
        self.assertEqual(created_response.status_code, 200, created_response.text)
        created = created_response.json()["data"]
        self.assertEqual(created["planned_summary"], "Author plan")
        self.assertEqual(created["actual_summary"], "Initial actual record")

        updated_response = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/outline/{created['id']}",
            json={
                "summary": "Author-corrected visible summary",
                "actual_summary": "Author-corrected actual record",
            },
        )
        self.assertEqual(updated_response.status_code, 200, updated_response.text)
        updated = updated_response.json()["data"]
        self.assertEqual(updated["summary"], "Author-corrected visible summary")
        self.assertEqual(updated["planned_summary"], "Author plan")
        self.assertEqual(updated["actual_summary"], "Author-corrected actual record")

        listed = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/outline"
        ).json()["data"]["flat"]
        persisted = next(item for item in listed if item["id"] == created["id"])
        self.assertEqual(persisted["summary"], "Author-corrected visible summary")
        self.assertEqual(persisted["planned_summary"], "Author plan")
        self.assertEqual(persisted["actual_summary"], "Author-corrected actual record")

    def test_delete_node_cascades_children(self):
        project_id = self.create_project()
        volume = self.create_node(project_id, "Volume One", "volume")
        chapter = self.create_node(project_id, "Chapter One", "chapter", parent_id=volume["id"])
        self.create_node(project_id, "Opening Scene", "section", parent_id=chapter["id"])

        response = self.client.delete(f"{API_PREFIX}/projects/{project_id}/outline/{volume['id']}")
        self.assertEqual(response.status_code, 200)

        list_response = self.client.get(f"{API_PREFIX}/projects/{project_id}/outline")
        self.assertEqual(list_response.json()["data"]["total"], 0)

        db = SessionLocal()
        try:
            self.assertEqual(db.query(OutlineNode).filter(OutlineNode.project_id == project_id).count(), 0)
        finally:
            db.close()


class TestOutlineReorder(OutlineTestCase):
    """Outline reorder tests."""

    def test_reorder_sibling_list(self):
        project_id = self.create_project()
        volume = self.create_node(project_id, "Volume One", "volume")
        first = self.create_node(project_id, "First Chapter", "chapter", parent_id=volume["id"], sort_order=0)
        second = self.create_node(project_id, "Second Chapter", "chapter", parent_id=volume["id"], sort_order=1)

        response = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/outline/reorder",
            json={"parent_id": volume["id"], "sort_order": [second["id"], first["id"]]},
        )
        self.assertEqual(response.status_code, 200)

        children = response.json()["data"]["items"][0]["children"]
        self.assertEqual([item["title"] for item in children], ["Second Chapter", "First Chapter"])
        self.assertEqual([item["sort_order"] for item in children], [0, 1])

    def test_move_node_to_new_parent(self):
        project_id = self.create_project()
        volume = self.create_node(project_id, "Volume One", "volume")
        first = self.create_node(project_id, "First Chapter", "chapter", parent_id=volume["id"], sort_order=0)
        second = self.create_node(project_id, "Second Chapter", "chapter", parent_id=volume["id"], sort_order=1)
        section = self.create_node(project_id, "Scene", "section", parent_id=first["id"])

        response = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/outline/reorder",
            json={"items": [{"id": section["id"], "parent_id": second["id"], "sort_order": 0}]},
        )
        self.assertEqual(response.status_code, 200)

        flat = {item["id"]: item for item in response.json()["data"]["flat"]}
        self.assertEqual(flat[section["id"]]["parent_id"], second["id"])

    def test_reorder_rejects_cycle(self):
        project_id = self.create_project()
        volume = self.create_node(project_id, "Volume One", "volume")
        chapter = self.create_node(project_id, "Chapter One", "chapter", parent_id=volume["id"])

        response = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/outline/reorder",
            json={"items": [{"id": volume["id"], "parent_id": chapter["id"], "sort_order": 0}]},
        )
        self.assertEqual(response.status_code, 400)


class TestOutlineDraftReview(OutlineTestCase):
    """Generated outlines remain drafts until the author confirms them."""

    def create_draft(
        self,
        project_id: str,
        *,
        parent_id: str | None = None,
        insert_after_id: str | None = None,
        character_names: list[str] | None = None,
    ) -> str:
        db = SessionLocal()
        try:
            draft = OutlineDraft(
                project_id=project_id,
                parent_id=parent_id,
                insert_after_id=insert_after_id,
                status="pending",
                nodes_json=[
                    {
                        "node_type": "chapter",
                        "title": "Chapter Two",
                        "summary": "The counterattack begins.",
                        "character_names": list(character_names or []),
                        "status": "pending",
                    },
                    {
                        "node_type": "section",
                        "title": "Chapter Two / Breach",
                        "summary": "The gate is breached.",
                        "parent_title": "Chapter Two",
                        "character_names": [],
                        "status": "pending",
                    },
                ],
                design_notes="Escalate the conflict.",
                context_selection_digest="0" * 64,
                base_outline_hash=_outline_tree_hash(db, project_id),
            )
            db.add(draft)
            db.commit()
            db.refresh(draft)
            return str(draft.id)
        finally:
            db.close()

    def test_pending_draft_can_be_restored_and_edited_without_formal_write(self):
        project_id = self.create_project()
        draft_id = self.create_draft(project_id)

        pending = self.client.get(f"{API_PREFIX}/projects/{project_id}/outline-drafts/pending")
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json()["data"]["draft_id"], draft_id)
        self.assertEqual(pending.json()["data"]["next_actions"][0], "edit")

        updated = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/outline-drafts/{draft_id}",
            json={
                "nodes": [
                    {
                        "node_type": "chapter",
                        "title": "Chapter Two Revised",
                        "summary": "The author changes the plan.",
                    }
                ],
                "design_notes": "Author edited.",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["data"]["nodes"][0]["title"], "Chapter Two Revised")

        outline = self.client.get(f"{API_PREFIX}/projects/{project_id}/outline").json()["data"]
        self.assertEqual(outline["total"], 0)

    def test_confirm_is_atomic_ordered_idempotent_and_returns_real_write_target(self):
        project_id = self.create_project()
        volume = self.create_node(project_id, "Volume One", "volume")
        first = self.create_node(
            project_id,
            "Chapter One",
            "chapter",
            parent_id=volume["id"],
            sort_order=0,
        )
        last = self.create_node(
            project_id,
            "Chapter Three",
            "chapter",
            parent_id=volume["id"],
            sort_order=1,
        )
        draft_id = self.create_draft(
            project_id,
            parent_id=volume["id"],
            insert_after_id=first["id"],
        )

        confirmed = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/outline-drafts/{draft_id}/confirm",
            json={"write_after_confirm": True},
        )
        self.assertEqual(confirmed.status_code, 200)
        data = confirmed.json()["data"]
        self.assertEqual(data["draft_status"], "confirmed")
        self.assertEqual(len(data["saved_outline_node_ids"]), 2)
        self.assertEqual(len(data["chapter_outline_node_ids"]), 1)
        self.assertTrue(data["next_author_request"]["requires_new_agent_turn"])
        self.assertEqual(
            data["next_author_request"]["outline_node_id"],
            data["chapter_outline_node_ids"][0],
        )

        tree = self.client.get(f"{API_PREFIX}/projects/{project_id}/outline").json()["data"]
        children = tree["items"][0]["children"]
        self.assertEqual(
            [node["title"] for node in children],
            ["Chapter One", "Chapter Two", "Chapter Three"],
        )
        self.assertEqual(children[1]["children"][0]["title"], "Chapter Two / Breach")
        self.assertEqual(children[2]["id"], last["id"])
        self.assertEqual(children[2]["sort_order"], 2)

        replay = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/outline-drafts/{draft_id}/confirm",
            json={"write_after_confirm": False},
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(
            replay.json()["data"]["saved_outline_node_ids"],
            data["saved_outline_node_ids"],
        )
        tree_after = self.client.get(f"{API_PREFIX}/projects/{project_id}/outline").json()["data"]
        self.assertEqual(tree_after["total"], 5)
        self.assertIsNone(
            self.client.get(
                f"{API_PREFIX}/projects/{project_id}/outline-drafts/pending"
            ).json()["data"]
        )

    def test_confirmed_draft_returns_and_persists_linked_characters(self):
        project_id = self.create_project()
        character = self.create_character(project_id, "Draft role")
        draft_id = self.create_draft(
            project_id,
            character_names=[character["name"]],
        )

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/outline-drafts/{draft_id}/confirm",
            json={"write_after_confirm": False},
        )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]
        chapter = next(
            item for item in result["nodes"] if item["node_type"] == "chapter"
        )
        self.assertEqual(
            [item["id"] for item in chapter["linked_characters"]],
            [character["id"]],
        )

        formal = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/outline"
        ).json()["data"]["flat"]
        persisted = next(item for item in formal if item["id"] == chapter["id"])
        self.assertEqual(
            [item["id"] for item in persisted["linked_characters"]],
            [character["id"]],
        )
    def test_regenerate_and_discard_only_return_new_author_work(self):
        project_id = self.create_project()
        draft_id = self.create_draft(project_id)

        regenerated = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/outline-drafts/{draft_id}/regenerate"
        )
        self.assertEqual(regenerated.status_code, 200)
        request = regenerated.json()["data"]["next_author_request"]
        self.assertTrue(request["requires_new_agent_turn"])
        self.assertIn("重新规划", request["message"])
        db = SessionLocal()
        try:
            self.assertEqual(db.get(OutlineDraft, draft_id).status, "superseded")
        finally:
            db.close()
        self.assertIsNone(
            self.client.get(
                f"{API_PREFIX}/projects/{project_id}/outline-drafts/pending"
            ).json()["data"]
        )

    def test_confirm_rejects_outline_changed_after_proposal_and_keeps_draft(self):
        project_id = self.create_project()
        first = self.create_node(project_id, "Chapter One", "chapter", sort_order=0)
        draft_id = self.create_draft(project_id, insert_after_id=first["id"])
        self.create_node(project_id, "Concurrent Chapter", "chapter", sort_order=1)

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/outline-drafts/{draft_id}/confirm",
            json={"write_after_confirm": False},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("正式大纲在提案生成后已变化", response.json()["message"])
        pending = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/outline-drafts/pending"
        ).json()["data"]
        self.assertEqual(pending["draft_id"], draft_id)
        tree = self.client.get(f"{API_PREFIX}/projects/{project_id}/outline").json()["data"]
        self.assertEqual(tree["total"], 2)

    def test_confirm_rolls_back_every_node_when_batch_result_is_partial(self):
        project_id = self.create_project()
        draft_id = self.create_draft(project_id)

        async def partial_create(db, _project_id, _args):
            leaked = OutlineNode(
                project_id=project_id,
                title="Partial node",
                node_type="chapter",
                sort_order=0,
            )
            db.add(leaked)
            db.flush()
            return {
                "status": "ok",
                "detail": "partial",
                "data": {
                    "nodes": [{"id": leaked.id, "title": leaked.title}],
                    "skipped": ["second node"],
                },
            }

        db = SessionLocal()
        try:
            with patch(
                "app.services.workspace.tools.outline.create_outline_nodes",
                new=partial_create,
            ), self.assertRaises(ValidationError):
                asyncio.run(confirm_outline_draft(db, project_id, draft_id))
            self.assertEqual(
                db.query(OutlineNode).filter(OutlineNode.project_id == project_id).count(),
                0,
            )
            draft = db.query(OutlineDraft).filter(OutlineDraft.id == draft_id).one()
            self.assertEqual(draft.status, "pending")
        finally:
            db.close()


class TestWorkspaceOutlineLinks(OutlineTestCase):
    def test_ai_outline_links_replace_clear_and_preserve_authoritatively(self):
        from app.services.workspace.tools.outline import (
            create_outline_node,
            update_outline_node,
        )

        project_id = self.create_project()
        first = self.create_character(project_id, "First role")
        second = self.create_character(project_id, "Second role")
        db = SessionLocal()
        try:
            created = asyncio.run(
                create_outline_node(
                    db,
                    project_id,
                    {
                        "title": "AI linked chapter",
                        "node_type": "chapter",
                        "character_names": [first["name"], second["name"]],
                    },
                )
            )
            self.assertEqual(created["status"], "ok")
            self.assertEqual(
                [item["id"] for item in created["data"]["linked_characters"]],
                [first["id"], second["id"]],
            )
            node_id = created["data"]["id"]

            cleared = asyncio.run(
                update_outline_node(
                    db,
                    project_id,
                    {"id": node_id, "character_names": []},
                )
            )
            self.assertEqual(cleared["data"]["linked_characters"], [])

            replaced = asyncio.run(
                update_outline_node(
                    db,
                    project_id,
                    {"id": node_id, "character_names": [second["name"]]},
                )
            )
            self.assertEqual(
                [item["id"] for item in replaced["data"]["linked_characters"]],
                [second["id"]],
            )

            preserved = asyncio.run(
                update_outline_node(
                    db,
                    project_id,
                    {"id": node_id, "summary": "Keep the existing links."},
                )
            )
            self.assertEqual(
                [item["id"] for item in preserved["data"]["linked_characters"]],
                [second["id"]],
            )
            db.commit()
        finally:
            db.close()

    def test_ai_outline_unknown_character_is_rejected_before_any_mutation(self):
        from app.services.workspace.tools.outline import (
            create_outline_node,
            update_outline_node,
        )

        project_id = self.create_project()
        character = self.create_character(project_id, "Known role")
        existing = self.create_node(
            project_id,
            "Existing chapter",
            character_ids=[character["id"]],
        )
        db = SessionLocal()
        try:
            with self.assertRaisesRegex(ValidationError, "Unknown role"):
                asyncio.run(
                    create_outline_node(
                        db,
                        project_id,
                        {
                            "title": "Rejected AI chapter",
                            "node_type": "chapter",
                            "character_names": ["Unknown role"],
                        },
                    )
                )
            self.assertEqual(
                db.query(OutlineNode)
                .filter(OutlineNode.title == "Rejected AI chapter")
                .count(),
                0,
            )

            with self.assertRaisesRegex(ValidationError, "Unknown role"):
                asyncio.run(
                    update_outline_node(
                        db,
                        project_id,
                        {
                            "id": existing["id"],
                            "summary": "Must not be applied",
                            "character_names": [character["name"], "Unknown role"],
                        },
                    )
                )
            node = db.get(OutlineNode, existing["id"])
            self.assertIsNone(node.summary)
            self.assertEqual(
                [link.character_id for link in node.linked_characters],
                [character["id"]],
            )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
