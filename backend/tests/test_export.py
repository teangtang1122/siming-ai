"""Regression tests for project export ordering."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from fastapi.testclient import TestClient

from app.database.models import Chapter, OutlineNode, Project
from app.database.session import Base, SessionLocal, engine
from app.main import app

API_PREFIX = "/api/v1"


class ExportTestCase(unittest.TestCase):
    """Exports should follow canonical chapter reading order, not outline order."""

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
            db.query(Chapter).delete()
            db.query(OutlineNode).delete()
            db.query(Project).delete()
            db.commit()
        finally:
            db.close()

    def create_project(self) -> str:
        response = self.client.post(f"{API_PREFIX}/projects", json={"title": "Export Project"})
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["id"]

    def create_outline_node(self, project_id: str, title: str, sort_order: int) -> str:
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/outline",
            json={"title": title, "node_type": "chapter", "sort_order": sort_order},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["id"]

    def create_chapter(self, project_id: str, title: str, outline_node_id: str | None = None) -> str:
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters",
            json={"title": title, "outline_node_id": outline_node_id, "content": title},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["id"]

    def test_export_uses_chapter_reading_order_independent_from_outline(self):
        project_id = self.create_project()
        second_outline = self.create_outline_node(project_id, "Second Outline", 1)
        first_outline = self.create_outline_node(project_id, "First Outline", 0)
        second_id = self.create_chapter(project_id, "Second Chapter", second_outline)
        unlinked_id = self.create_chapter(project_id, "Unlinked Chapter")
        first_id = self.create_chapter(project_id, "First Chapter", first_outline)

        report = self.client.get(f"{API_PREFIX}/projects/{project_id}/export/word-count")
        self.assertEqual(report.status_code, 200)
        titles = [item["title"] for item in report.json()["data"]["chapters"]]
        self.assertEqual(titles, ["Second Chapter", "Unlinked Chapter", "First Chapter"])

        reordered = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/chapters/reorder",
            json={"ids": [first_id, second_id, unlinked_id]},
        )
        self.assertEqual(reordered.status_code, 200, reordered.text)

        # Deliberately move the linked outline nodes in the opposite direction.
        # Export order must remain the正文 reading order established above.
        first_update = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/outline/{first_outline}",
            json={"sort_order": 9},
        )
        second_update = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/outline/{second_outline}",
            json={"sort_order": 0},
        )
        self.assertEqual(first_update.status_code, 200, first_update.text)
        self.assertEqual(second_update.status_code, 200, second_update.text)

        report = self.client.get(f"{API_PREFIX}/projects/{project_id}/export/word-count")
        self.assertEqual(report.status_code, 200)
        titles = [item["title"] for item in report.json()["data"]["chapters"]]
        self.assertEqual(titles, ["First Chapter", "Second Chapter", "Unlinked Chapter"])

        exported = self.client.post(f"{API_PREFIX}/projects/{project_id}/export?scope=chapters&format=txt")
        self.assertEqual(exported.status_code, 200)
        export_data = exported.json()["data"]
        self.assertIn("file_id", export_data)
        self.assertTrue(export_data["download_url"].endswith(f"/export/download/{export_data['file_id']}"))

        downloaded = self.client.get(export_data["download_url"])
        self.assertEqual(downloaded.status_code, 200)
        text = downloaded.content.decode("utf-8")
        self.assertLess(text.index("First Chapter"), text.index("Second Chapter"))
        self.assertLess(text.index("Second Chapter"), text.index("Unlinked Chapter"))

    def test_export_selected_chapters_downloads_by_file_id(self):
        project_id = self.create_project()
        first_id = self.create_chapter(project_id, "Selected Chapter")
        self.create_chapter(project_id, "Skipped Chapter")

        exported = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/export",
            json={"scope": "selected", "format": "txt", "chapter_ids": [first_id]},
        )
        self.assertEqual(exported.status_code, 200)
        export_data = exported.json()["data"]
        self.assertEqual(export_data["format"], "txt")
        self.assertGreater(export_data["size"], 0)

        downloaded = self.client.get(f"{API_PREFIX}/projects/{project_id}/export/download/{export_data['file_id']}")
        self.assertEqual(downloaded.status_code, 200)
        text = downloaded.content.decode("utf-8")
        self.assertIn("Selected Chapter", text)
        self.assertNotIn("Skipped Chapter", text)

    def test_export_writes_to_selected_directory(self):
        project_id = self.create_project()
        self.create_chapter(project_id, "Saved Chapter")

        with tempfile.TemporaryDirectory() as temp_dir:
            exported = self.client.post(
                f"{API_PREFIX}/projects/{project_id}/export",
                json={
                    "scope": "chapters",
                    "format": "txt",
                    "output_directory": temp_dir,
                },
            )

            self.assertEqual(exported.status_code, 200)
            saved_path = Path(exported.json()["data"]["saved_path"])
            self.assertEqual(saved_path.parent, Path(temp_dir).resolve())
            self.assertTrue(saved_path.exists())
            self.assertIn("Saved Chapter", saved_path.read_text(encoding="utf-8"))

    def test_export_rejects_missing_output_directory(self):
        project_id = self.create_project()
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_dir = Path(temp_dir) / "missing"
            exported = self.client.post(
                f"{API_PREFIX}/projects/{project_id}/export",
                json={
                    "scope": "chapters",
                    "format": "txt",
                    "output_directory": str(missing_dir),
                },
            )

        self.assertEqual(exported.status_code, 400)
        self.assertIn("导出目录不存在", exported.json()["message"])
