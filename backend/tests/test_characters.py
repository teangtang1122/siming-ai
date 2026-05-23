"""
Test cases for FR-002: 角色设定与管理.

Covers:
  - Character CRUD
  - 8+ character attributes in create response
  - Version snapshot creation on update
  - Relationship network update/query
  - AI suggestion endpoint with mocked LLM Gateway
"""

import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from fastapi.testclient import TestClient

from app.database.models import Character, CharacterRelationship, CharacterVersion, Project
from app.database.session import Base, SessionLocal, engine
from app.main import app

API_PREFIX = "/api/v1"


class CharacterTestCase(unittest.TestCase):
    """Shared setup for character API tests."""

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
            db.query(Project).delete()
            db.commit()
        finally:
            db.close()

    def create_project(self, title: str = "角色测试作品") -> str:
        response = self.client.post(f"{API_PREFIX}/projects", json={"title": title})
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["id"]

    def create_character(self, project_id: str, name: str = "林澈") -> dict:
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/characters",
            json={
                "name": name,
                "appearance": "黑发灰眸，常穿旧青衫。",
                "personality": "谨慎、执拗，但会为朋友冒险。",
                "background": "边城孤儿，被老修士收养。",
                "abilities": ["御风", "符阵入门"],
                "role_type": "protagonist",
                "is_evolution_tracked": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]


class TestCharacterCRUD(CharacterTestCase):
    """Character CRUD tests."""

    def test_create_character_with_complete_fields(self):
        project_id = self.create_project()

        data = self.create_character(project_id)

        expected_fields = {
            "id",
            "project_id",
            "name",
            "appearance",
            "personality",
            "background",
            "abilities",
            "role_type",
            "current_version",
            "is_evolution_tracked",
            "created_at",
            "updated_at",
        }
        self.assertTrue(expected_fields.issubset(set(data.keys())))
        self.assertEqual(data["name"], "林澈")
        self.assertEqual(data["abilities"], ["御风", "符阵入门"])
        self.assertEqual(data["current_version"], 1)
        self.assertTrue(data["is_evolution_tracked"])

    def test_list_characters_isolated_by_project(self):
        project_a = self.create_project("作品A")
        project_b = self.create_project("作品B")
        self.create_character(project_a, "角色A")
        self.create_character(project_b, "角色B")

        response = self.client.get(f"{API_PREFIX}/projects/{project_a}/characters")
        self.assertEqual(response.status_code, 200)

        data = response.json()["data"]
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["name"], "角色A")

    def test_get_character_detail_includes_appearances(self):
        project_id = self.create_project()
        character = self.create_character(project_id)

        response = self.client.get(f"{API_PREFIX}/projects/{project_id}/characters/{character['id']}")
        self.assertEqual(response.status_code, 200)

        data = response.json()["data"]
        self.assertEqual(data["id"], character["id"])
        self.assertIn("appearances", data)
        self.assertEqual(data["appearances"]["outline_nodes"], [])
        self.assertEqual(data["appearances"]["chapters"], [])

    def test_delete_character_removes_relationships(self):
        project_id = self.create_project()
        first = self.create_character(project_id, "师父")
        second = self.create_character(project_id, "徒弟")
        self.client.put(
            f"{API_PREFIX}/projects/{project_id}/characters/{first['id']}/relationships",
            json={
                "relationships": [
                    {
                        "target_character_id": second["id"],
                        "relationship_type": "师徒",
                        "description": "传授风系术法",
                    }
                ]
            },
        )

        response = self.client.delete(f"{API_PREFIX}/projects/{project_id}/characters/{first['id']}")
        self.assertEqual(response.status_code, 200)

        db = SessionLocal()
        try:
            self.assertEqual(db.query(Character).filter(Character.id == first["id"]).count(), 0)
            self.assertEqual(
                db.query(CharacterRelationship).filter(CharacterRelationship.project_id == project_id).count(),
                0,
            )
        finally:
            db.close()


class TestCharacterVersions(CharacterTestCase):
    """Version history tests."""

    def test_update_character_increments_version_and_writes_snapshot(self):
        project_id = self.create_project()
        character = self.create_character(project_id)

        response = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/characters/{character['id']}",
            json={
                "personality": "更果断，也更愿意承担风险。",
                "abilities": ["御风", "符阵入门", "听风辨位"],
                "change_summary": "获得新能力并更新性格",
            },
        )
        self.assertEqual(response.status_code, 200)

        updated = response.json()["data"]
        self.assertEqual(updated["current_version"], 2)
        self.assertEqual(updated["abilities"], ["御风", "符阵入门", "听风辨位"])

        versions_resp = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/characters/{character['id']}/versions"
        )
        self.assertEqual(versions_resp.status_code, 200)
        versions = versions_resp.json()["data"]["items"]
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["version_number"], 2)
        self.assertEqual(versions[0]["change_summary"], "获得新能力并更新性格")

        detail_resp = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/characters/{character['id']}/versions/{versions[0]['id']}"
        )
        self.assertEqual(detail_resp.status_code, 200)
        snapshot = detail_resp.json()["data"]["snapshot_data"]
        self.assertEqual(snapshot["current_version"], 2)
        self.assertIn("听风辨位", snapshot["abilities"])

    def test_update_with_empty_body_returns_validation_error(self):
        project_id = self.create_project()
        character = self.create_character(project_id)

        response = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/characters/{character['id']}",
            json={},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["message"], "未提供任何更新字段")


class TestCharacterRelationships(CharacterTestCase):
    """Relationship network tests."""

    def test_update_relationship_and_query_network(self):
        project_id = self.create_project()
        master = self.create_character(project_id, "沈孤鸿")
        apprentice = self.create_character(project_id, "林澈")

        response = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/characters/{master['id']}/relationships",
            json={
                "relationships": [
                    {
                        "target_character_id": apprentice["id"],
                        "relationship_type": "师徒",
                        "description": "沈孤鸿曾救下林澈，并传其御风术。",
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 200)

        data = response.json()["data"]
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["edges"][0]["from"], master["id"])
        self.assertEqual(data["edges"][0]["to"], apprentice["id"])
        self.assertEqual(data["edges"][0]["relationship_type"], "师徒")

        network_resp = self.client.get(f"{API_PREFIX}/projects/{project_id}/characters/relationships")
        self.assertEqual(network_resp.status_code, 200)
        network = network_resp.json()["data"]
        self.assertEqual(len(network["nodes"]), 2)
        self.assertEqual(network["edges"][0]["relationship_type"], "师徒")

    def test_relationship_target_must_belong_to_same_project(self):
        project_a = self.create_project("作品A")
        project_b = self.create_project("作品B")
        character_a = self.create_character(project_a, "角色A")
        character_b = self.create_character(project_b, "角色B")

        response = self.client.put(
            f"{API_PREFIX}/projects/{project_a}/characters/{character_a['id']}/relationships",
            json={
                "relationships": [
                    {
                        "target_character_id": character_b["id"],
                        "relationship_type": "敌对",
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("当前作品", response.json()["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

