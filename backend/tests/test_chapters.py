"""
Test cases for chapter management and version control.

Covers:
  - Chapter CRUD and outline-ordered list
  - Save-time snapshot creation
  - Snapshot history and restore
  - Line-based diff between snapshots
"""

import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from fastapi.testclient import TestClient

from app.database.models import Chapter, ChapterSnapshot, OutlineNode, Project
from app.database.session import Base, SessionLocal, engine
from app.main import app

API_PREFIX = "/api/v1"


class ChapterTestCase(unittest.TestCase):
    """Shared setup for chapter API tests."""

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
            db.query(ChapterSnapshot).delete()
            db.query(Chapter).delete()
            db.query(OutlineNode).delete()
            db.query(Project).delete()
            db.commit()
        finally:
            db.close()

    def create_project(self, title: str = "Chapter Test Novel") -> str:
        response = self.client.post(f"{API_PREFIX}/projects", json={"title": title})
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["id"]

    def create_outline_node(
        self,
        project_id: str,
        title: str,
        node_type: str = "chapter",
        parent_id: str | None = None,
        sort_order: int = 0,
    ) -> dict:
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/outline",
            json={
                "title": title,
                "node_type": node_type,
                "parent_id": parent_id,
                "sort_order": sort_order,
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]

    def create_chapter(
        self,
        project_id: str,
        title: str = "Chapter One",
        outline_node_id: str | None = None,
        content: str = "",
    ) -> dict:
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters",
            json={"title": title, "outline_node_id": outline_node_id, "content": content},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]


class TestChapterCRUD(ChapterTestCase):
    """Chapter CRUD tests."""

    def test_create_and_get_chapter_detail(self):
        project_id = self.create_project()
        outline = self.create_outline_node(project_id, "Opening Outline")

        chapter = self.create_chapter(
            project_id,
            title="Opening Chapter",
            outline_node_id=outline["id"],
            content="林澈推开城门。",
        )

        self.assertEqual(chapter["title"], "Opening Chapter")
        self.assertEqual(chapter["outline_title"], "Opening Outline")
        self.assertEqual(chapter["word_count"], 7)  # 6 CJK + 1 punctuation
        self.assertEqual(chapter["current_version"], 1)
        self.assertEqual(chapter["snapshot_count"], 1)

        response = self.client.get(f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}")
        self.assertEqual(response.status_code, 200)
        detail = response.json()["data"]
        self.assertEqual(detail["content"], "林澈推开城门。")

    def test_list_chapters_ordered_by_outline_tree(self):
        project_id = self.create_project()
        volume = self.create_outline_node(project_id, "Volume One", "volume")
        second_outline = self.create_outline_node(
            project_id,
            "Second Outline",
            "chapter",
            parent_id=volume["id"],
            sort_order=1,
        )
        first_outline = self.create_outline_node(
            project_id,
            "First Outline",
            "chapter",
            parent_id=volume["id"],
            sort_order=0,
        )
        self.create_chapter(project_id, "Second Chapter", second_outline["id"])
        self.create_chapter(project_id, "Unlinked Chapter")
        self.create_chapter(project_id, "First Chapter", first_outline["id"])

        response = self.client.get(f"{API_PREFIX}/projects/{project_id}/chapters")
        self.assertEqual(response.status_code, 200)

        titles = [item["title"] for item in response.json()["data"]["items"]]
        self.assertEqual(titles, ["First Chapter", "Second Chapter", "Unlinked Chapter"])
        first = response.json()["data"]["items"][0]
        self.assertEqual(first["outline_path"], ["Volume One", "First Outline"])

    def test_delete_chapter_removes_snapshots(self):
        project_id = self.create_project()
        chapter = self.create_chapter(project_id, content="Old content")
        self.client.put(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}",
            json={"content": "New content"},
        )

        response = self.client.delete(f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}")
        self.assertEqual(response.status_code, 200)

        db = SessionLocal()
        try:
            self.assertEqual(db.query(Chapter).filter(Chapter.id == chapter["id"]).count(), 0)
            self.assertEqual(db.query(ChapterSnapshot).filter(ChapterSnapshot.chapter_id == chapter["id"]).count(), 0)
        finally:
            db.close()


class TestChapterSnapshots(ChapterTestCase):
    """Snapshot and restore tests."""

    def test_save_chapter_creates_snapshot_with_new_content(self):
        project_id = self.create_project()
        chapter = self.create_chapter(project_id, content="旧内容")

        response = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}",
            json={"content": "新内容\n第二行", "title": "Saved Chapter"},
        )
        self.assertEqual(response.status_code, 200)

        saved = response.json()["data"]
        self.assertEqual(saved["title"], "Saved Chapter")
        self.assertEqual(saved["content"], "新内容\n第二行")
        self.assertEqual(saved["word_count"], 6)
        self.assertEqual(saved["current_version"], 2)
        self.assertEqual(saved["snapshot_count"], 2)

        snapshots_resp = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}/snapshots"
        )
        snapshots = snapshots_resp.json()["data"]["items"]
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0]["version_number"], 2)
        self.assertEqual(snapshots[0]["trigger_type"], "manual_save")

        detail_resp = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}/snapshots/{snapshots[0]['id']}"
        )
        self.assertEqual(detail_resp.json()["data"]["content"], "新内容\n第二行")

    def test_today_stats_based_on_chapter_creation_date(self):
        project_id = self.create_project()
        chapter = self.create_chapter(project_id, content="一二三四")  # 4 chars

        # Chapter created today counts its word_count
        stats_resp = self.client.get(f"{API_PREFIX}/projects/{project_id}/stats/today")
        self.assertEqual(stats_resp.status_code, 200)
        self.assertEqual(stats_resp.json()["data"]["total_words"], 4)
        self.assertEqual(stats_resp.json()["data"]["chapters_written"], 1)

        # Editing the chapter updates today's total (still based on created_at today)
        self.client.put(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}",
            json={"content": "一二三四五六七八"},  # 8 chars
        )
        stats_resp = self.client.get(f"{API_PREFIX}/projects/{project_id}/stats/today")
        self.assertEqual(stats_resp.status_code, 200)
        self.assertEqual(stats_resp.json()["data"]["total_words"], 8)

    def test_restore_snapshot_creates_restore_snapshot(self):
        project_id = self.create_project()
        chapter = self.create_chapter(project_id, content="初稿")
        first_save = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}",
            json={"content": "第一版内容"},
        ).json()["data"]
        self.client.put(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}",
            json={"content": "第二版内容"},
        )
        snapshots = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}/snapshots"
        ).json()["data"]["items"]
        first_snapshot = next(item for item in snapshots if item["version_number"] == first_save["current_version"])

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}/restore/{first_snapshot['id']}"
        )
        self.assertEqual(response.status_code, 200)

        restored = response.json()["data"]
        self.assertEqual(restored["content"], "第一版内容")
        self.assertEqual(restored["current_version"], 4)
        self.assertEqual(restored["snapshot_count"], 4)

        new_snapshots = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}/snapshots"
        ).json()["data"]["items"]
        self.assertEqual(new_snapshots[0]["version_number"], 4)
        self.assertEqual(new_snapshots[0]["trigger_type"], "restore")

    def test_diff_between_two_snapshots(self):
        project_id = self.create_project()
        chapter = self.create_chapter(project_id, content="")
        self.client.put(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}",
            json={"content": "旧句子\n保留行"},
        )
        self.client.put(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}",
            json={"content": "新句子\n保留行\n新增行"},
        )
        snapshots = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}/snapshots"
        ).json()["data"]["items"]
        by_version = {item["version_number"]: item for item in snapshots}

        response = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}/snapshots/diff",
            params={
                "from_snapshot_id": by_version[2]["id"],
                "to_snapshot_id": by_version[3]["id"],
            },
        )
        self.assertEqual(response.status_code, 200)

        diff = response.json()["data"]
        change_types = [item["type"] for item in diff["changes"]]
        self.assertIn("replace", change_types)
        self.assertIn("insert", change_types)
        self.assertEqual(diff["total_changes"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
