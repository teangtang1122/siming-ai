"""Regression tests for persisted deconstruct reports and imports."""

import json
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from fastapi.testclient import TestClient

from app.database.models import Character, DeconstructionReport, OutlineNode, Project
from app.database.session import Base, SessionLocal, engine
from app.main import app

API_PREFIX = "/api/v1"


class DeconstructTestCase(unittest.TestCase):
    """Deconstruct analysis should persist reports and import selected sections."""

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
            db.query(DeconstructionReport).delete()
            db.query(OutlineNode).delete()
            db.query(Character).delete()
            db.query(Project).delete()
            db.commit()
        finally:
            db.close()

    def create_project(self) -> str:
        response = self.client.post(f"{API_PREFIX}/projects", json={"title": "Deconstruct Project"})
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["id"]

    @patch("app.services.deconstruct.map_reduce.LLMGateway.chat_completion", new_callable=AsyncMock)
    def test_deconstruct_report_can_be_queried_and_imported(self, mock_chat):
        project_id = self.create_project()
        map_result = {
            "characters": [{"name": "林澈", "role": "protagonist", "mentions": 8}],
            "events": [{"description": "主角获得线索", "type": "intro"}],
            "pacing": "fast",
            "highlights": [{"type": "reveal", "description": "秘密揭开", "intensity": "high"}],
        }
        reduce_result = {
            "structure": {
                "volumes": [
                    {"title": "第一卷", "chapters": [{"title": "第一章", "summary": "主角获得线索"}]}
                ],
                "total_estimated_chapters": 1,
            },
            "plot_nodes": [
                {"description": "主角获得线索", "type": "intro", "position_pct": 10, "importance": "high"}
            ],
            "characters": [
                {
                    "name": "林澈",
                    "role": "protagonist",
                    "mention_count": 8,
                    "importance": "high",
                    "arc_description": "从迷茫到开始行动",
                }
            ],
            "highlights": [
                {"type": "reveal", "description": "秘密揭开", "position_pct": 30, "intensity": "high"}
            ],
            "rhythm_curve": [{"position_pct": 10, "pace": "fast", "label": "开局推进"}],
            "patterns": [{"type": "structure", "description": "悬念开局", "frequency": "frequent", "examples": []}],
        }
        mock_chat.side_effect = [
            {"content": json.dumps(map_result, ensure_ascii=False)},
            {"content": json.dumps(reduce_result, ensure_ascii=False)},
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/deconstruct",
            json={"text": "主角获得线索。" * 80, "title": "样本文本"},
        )
        self.assertEqual(response.status_code, 200)
        report = response.json()["data"]
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["title"], "样本文本")

        db = SessionLocal()
        try:
            stored = db.query(DeconstructionReport).filter(DeconstructionReport.id == report["id"]).first()
            self.assertIsNotNone(stored)
            self.assertEqual(stored.status, "completed")
        finally:
            db.close()

        detail = self.client.get(f"{API_PREFIX}/projects/{project_id}/deconstruct/{report['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["characters"][0]["name"], "林澈")

        status = self.client.get(f"{API_PREFIX}/projects/{project_id}/deconstruct/{report['id']}/status")
        self.assertEqual(status.status_code, 200)
        self.assertIn('"status":"completed"', status.text)

        imported = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/deconstruct/{report['id']}/import",
            json={"import_outline": True, "import_characters": True},
        )
        self.assertEqual(imported.status_code, 200)
        data = imported.json()["data"]
        self.assertEqual(data["outline_count"], 2)
        self.assertEqual(data["character_count"], 1)

        db = SessionLocal()
        try:
            self.assertEqual(db.query(OutlineNode).filter(OutlineNode.project_id == project_id).count(), 2)
            self.assertEqual(db.query(Character).filter(Character.project_id == project_id).count(), 1)
        finally:
            db.close()

