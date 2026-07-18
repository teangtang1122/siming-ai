"""Tests for prompt-pack GUI contribution exports."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test_prompt_contribution_api.db"

from fastapi.testclient import TestClient

from app.database.models import Project, PublicPromptPack
from app.database.session import Base, SessionLocal, engine
from app.main import app


API_PREFIX = "/api/v1"


class PromptContributionApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("test_prompt_contribution_api.db")
        except OSError:
            pass

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SessionLocal()
        self.db.query(PublicPromptPack).delete()
        self.db.query(Project).delete()
        self.project = Project(title="Prompt Contribution Test", folder_path=self.tmp.name)
        self.db.add(self.project)
        self.db.commit()
        self.db.refresh(self.project)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_prompt_pack_list_detail_and_export(self):
        list_resp = self.client.get(f"{API_PREFIX}/projects/{self.project.id}/prompt-packs")
        self.assertEqual(list_resp.status_code, 200)
        items = list_resp.json()["data"]["items"]
        self.assertTrue(any(item["pack_id"] == "chapter_writing_quality" for item in items))

        detail_resp = self.client.get(
            f"{API_PREFIX}/projects/{self.project.id}/prompt-packs/chapter_writing_quality"
        )
        self.assertEqual(detail_resp.status_code, 200)
        detail = detail_resp.json()["data"]
        self.assertEqual(detail["pack_id"], "chapter_writing_quality")
        self.assertIn("system_prompt", detail)
        self.assertEqual(
            detail["prompt_spec"]["prompt_spec_id"],
            "assistant.chapter.quality",
        )

        edited = detail["system_prompt"] + "\n\n【投稿测试】请更重视角色阶段性目标。"
        export_resp = self.client.post(
            f"{API_PREFIX}/projects/{self.project.id}/prompt-contributions/export",
            json={
                "pack_id": "chapter_writing_quality",
                "base_version": detail["version"],
                "edited_system_prompt": edited,
                "change_summary": "增加角色阶段性目标检查，减少章节推进时目标漂移。",
                "expected_effect": "作者测试时更容易看到角色当前目标，章节动作更聚焦。",
                "test_notes": "用第十章提示词做了 A/B 对比。",
                "contributor_name": "作者A",
            },
        )
        self.assertEqual(export_resp.status_code, 200)
        data = export_resp.json()["data"]
        self.assertIn("github.com/teangtang1122/siming-ai/issues/new", data["github_issue_url"])
        markdown_path = Path(data["markdown_path"])
        json_path = Path(data["json_path"])
        self.assertTrue(markdown_path.exists())
        self.assertTrue(json_path.exists())

        package = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(package["schema_version"], "siming.prompt_contribution.v1")
        self.assertEqual(package["base_pack"]["pack_id"], "chapter_writing_quality")
        self.assertEqual(
            package["base_pack"]["prompt_spec_id"],
            "assistant.chapter.quality",
        )
        self.assertEqual(len(package["base_pack"]["base_hash"]), 64)
        self.assertIn("角色阶段性目标", package["after_prompt"])
        self.assertGreaterEqual(package["diff_stats"]["added_lines"], 1)
