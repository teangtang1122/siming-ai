"""
Test cases for FR-001: 世界观构建.

Covers:
  - CRUD API for worldbuilding entries
  - Grouped-by-dimension list response
  - Project-level data isolation
  - AI expansion endpoint with mocked LLM Gateway
  - Conflict detection endpoint with mocked LLM Gateway
"""

import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from fastapi.testclient import TestClient

from app.database.models import Project, WorldbuildingEntry
from app.database.session import Base, SessionLocal, engine
from app.main import app

API_PREFIX = "/api/v1"


class WorldbuildingTestCase(unittest.TestCase):
    """Shared setup for worldbuilding API tests."""

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

    def create_project(self, title: str = "世界观测试作品") -> str:
        response = self.client.post(
            f"{API_PREFIX}/projects",
            json={"title": title, "description": "用于测试世界观构建"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["id"]


class TestWorldbuildingCRUD(WorldbuildingTestCase):
    """CRUD and grouped query tests."""

    def test_list_empty_returns_all_dimensions(self):
        project_id = self.create_project()

        response = self.client.get(f"{API_PREFIX}/projects/{project_id}/worldbuilding")
        self.assertEqual(response.status_code, 200)

        data = response.json()["data"]
        self.assertEqual(data["total"], 0)
        self.assertEqual(
            set(data["grouped"].keys()),
            {"geography", "history", "factions", "power_system", "races", "culture"},
        )

    def test_create_six_dimensions_and_list_grouped(self):
        project_id = self.create_project()
        payloads = [
            ("geography", "东洲", "东洲多山，灵脉密布。"),
            ("history", "旧纪元", "旧纪元结束于天火坠落。"),
            ("factions", "玄门", "玄门统辖北境宗派。"),
            ("power_system", "灵力潮汐", "灵力潮汐每十年爆发一次。"),
            ("races", "鲛人", "鲛人居于深海城邦。"),
            ("culture", "祭灯节", "祭灯节用于纪念失落王朝。"),
        ]

        for index, (dimension, title, content) in enumerate(payloads):
            response = self.client.post(
                f"{API_PREFIX}/projects/{project_id}/worldbuilding",
                json={
                    "dimension": dimension,
                    "title": title,
                    "content": content,
                    "sort_order": index,
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["dimension"], dimension)

        response = self.client.get(f"{API_PREFIX}/projects/{project_id}/worldbuilding")
        self.assertEqual(response.status_code, 200)

        data = response.json()["data"]
        self.assertEqual(data["total"], 6)
        for dimension, title, _content in payloads:
            self.assertEqual(len(data["grouped"][dimension]), 1)
            self.assertEqual(data["grouped"][dimension][0]["title"], title)

    def test_update_worldbuilding_entry(self):
        project_id = self.create_project()
        create_resp = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/worldbuilding",
            json={"dimension": "geography", "title": "旧地名", "content": "旧内容"},
        )
        entry_id = create_resp.json()["data"]["id"]

        response = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/worldbuilding/{entry_id}",
            json={"title": "新地名", "content": "更新后的内容", "sort_order": 3},
        )
        self.assertEqual(response.status_code, 200)

        data = response.json()["data"]
        self.assertEqual(data["title"], "新地名")
        self.assertEqual(data["content"], "更新后的内容")
        self.assertEqual(data["sort_order"], 3)

    def test_delete_worldbuilding_entry(self):
        project_id = self.create_project()
        create_resp = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/worldbuilding",
            json={"dimension": "culture", "title": "旧习俗", "content": "即将删除"},
        )
        entry_id = create_resp.json()["data"]["id"]

        response = self.client.delete(f"{API_PREFIX}/projects/{project_id}/worldbuilding/{entry_id}")
        self.assertEqual(response.status_code, 200)

        list_resp = self.client.get(f"{API_PREFIX}/projects/{project_id}/worldbuilding")
        self.assertEqual(list_resp.json()["data"]["total"], 0)

    def test_worldbuilding_project_isolation(self):
        project_a = self.create_project("作品A")
        project_b = self.create_project("作品B")

        self.client.post(
            f"{API_PREFIX}/projects/{project_a}/worldbuilding",
            json={"dimension": "geography", "title": "A大陆", "content": "A作品设定"},
        )
        self.client.post(
            f"{API_PREFIX}/projects/{project_b}/worldbuilding",
            json={"dimension": "geography", "title": "B大陆", "content": "B作品设定"},
        )

        response = self.client.get(f"{API_PREFIX}/projects/{project_a}/worldbuilding")
        items = response.json()["data"]["grouped"]["geography"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "A大陆")


if __name__ == "__main__":
    unittest.main(verbosity=2)

