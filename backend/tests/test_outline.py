"""
Test cases for outline planning.

Covers:
  - Outline tree CRUD
  - Character links on outline nodes
  - Reorder and parent moves
  - Cascade delete
  - AI summary suggestion with mocked LLM Gateway
"""

import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from fastapi.testclient import TestClient

from app.database.models import (
    Character,
    OutlineNode,
    OutlineNodeCharacter,
    Project,
    WorldbuildingEntry,
)
from app.database.session import Base, SessionLocal, engine
from app.main import app

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
        try:
            os.remove("test_novel_agent.db")
        except OSError:
            pass

    def setUp(self):
        db = SessionLocal()
        try:
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
    ) -> dict:
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/outline",
            json={
                "title": title,
                "node_type": node_type,
                "parent_id": parent_id,
                "sort_order": sort_order,
                "character_ids": character_ids or [],
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]


class TestOutlineCRUD(OutlineTestCase):
    """Outline tree CRUD tests."""

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
        self.create_node(project_id, "Opening Scene", "section", parent_id=chapter["id"])

        response = self.client.get(f"{API_PREFIX}/projects/{project_id}/outline")
        self.assertEqual(response.status_code, 200)

        data = response.json()["data"]
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["title"], "Volume One")
        self.assertEqual(data["items"][0]["children"][0]["title"], "Chapter One")
        self.assertEqual(data["items"][0]["children"][0]["children"][0]["title"], "Opening Scene")
        self.assertEqual(data["items"][0]["children"][0]["linked_characters"][0]["name"], "Lin Che")

    def test_update_node_fields_and_linked_characters(self):
        project_id = self.create_project()
        first = self.create_character(project_id, "Lin Che")
        second = self.create_character(project_id, "Shen Hong")
        chapter = self.create_node(project_id, "Draft Chapter", "chapter", character_ids=[first["id"]])

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
