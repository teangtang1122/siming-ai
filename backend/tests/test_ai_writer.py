"""Regression tests for AI writing engine project isolation."""

import asyncio
import json
import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from fastapi.testclient import TestClient

from app.database.models import (
    Chapter,
    ChapterCharacter,
    ChapterSnapshot,
    Character,
    CharacterChangeLog,
    CharacterTimeline,
    CharacterVersion,
    AssistantConversation,
    AssistantMessage,
    OutlineNode,
    OutlineNodeCharacter,
    Project,
)
from app.database.session import Base, SessionLocal, engine
from app.main import app
from app.routers.ai_writer import _execute_workspace_action

API_PREFIX = "/api/v1"


class AIWriterIsolationTestCase(unittest.TestCase):
    """AI writer endpoints must not accept outline nodes from another project."""

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
            db.query(AssistantMessage).delete()
            db.query(AssistantConversation).delete()
            db.query(CharacterTimeline).delete()
            db.query(CharacterChangeLog).delete()
            db.query(ChapterCharacter).delete()
            db.query(OutlineNodeCharacter).delete()
            db.query(CharacterVersion).delete()
            db.query(ChapterSnapshot).delete()
            db.query(Chapter).delete()
            db.query(Character).delete()
            db.query(OutlineNode).delete()
            db.query(Project).delete()
            db.commit()
        finally:
            db.close()

    def create_project(self, title: str) -> str:
        response = self.client.post(f"{API_PREFIX}/projects", json={"title": title})
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["id"]

    def create_outline_node(self, project_id: str, title: str) -> str:
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/outline",
            json={"title": title, "node_type": "chapter"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["id"]

    def create_character(self, project_id: str, name: str) -> str:
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/characters",
            json={
                "name": name,
                "personality": "沉稳",
                "abilities": [],
                "is_evolution_tracked": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["id"]

    def create_chapter(self, project_id: str, content: str) -> str:
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters",
            json={"title": "Chapter", "content": content},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["id"]

    @patch("app.routers.ai_writer.LLMGateway.chat_completion", new_callable=AsyncMock)
    def test_continue_rejects_cross_project_outline_node(self, mock_chat):
        project_a = self.create_project("Project A")
        project_b = self.create_project("Project B")
        foreign_outline_id = self.create_outline_node(project_b, "Foreign Outline")

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_a}/ai/continue",
            json={
                "text": "one two",
                "outline_node_id": foreign_outline_id,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("当前作品", response.json()["message"])
        mock_chat.assert_not_awaited()

    def test_conflict_adopt_creates_child_outline_node_and_links_characters(self):
        project_id = self.create_project("Conflict Project")
        parent_id = self.create_outline_node(project_id, "第一章")
        character_id = self.create_character(project_id, "林澈")

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/conflict-adopt",
            json={
                "outline_node_id": parent_id,
                "type": "personality",
                "title": "师徒反目",
                "description": "林澈发现师父隐瞒旧案。",
                "suggested_outcome": "二人暂时决裂，留下复合伏笔。",
                "involved_characters": ["林澈"],
            },
        )

        self.assertEqual(response.status_code, 200)
        node = response.json()["data"]["outline_node"]
        self.assertEqual(node["parent_id"], parent_id)
        self.assertEqual(node["node_type"], "section")
        self.assertIn("林澈发现师父", node["summary"])
        self.assertEqual(node["linked_characters"][0]["id"], character_id)

        db = SessionLocal()
        try:
            created = db.query(OutlineNode).filter(OutlineNode.id == node["id"]).first()
            self.assertIsNotNone(created)
            self.assertEqual(created.parent_id, parent_id)
            self.assertEqual(
                db.query(OutlineNodeCharacter)
                .filter(
                    OutlineNodeCharacter.outline_node_id == node["id"],
                    OutlineNodeCharacter.character_id == character_id,
                )
                .count(),
                1,
            )
        finally:
            db.close()

    def test_conflict_adopt_rejects_cross_project_outline_node(self):
        project_a = self.create_project("Project A")
        project_b = self.create_project("Project B")
        foreign_outline_id = self.create_outline_node(project_b, "Foreign Outline")

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_a}/ai/conflict-adopt",
            json={
                "outline_node_id": foreign_outline_id,
                "title": "不应采纳",
                "description": "跨作品大纲节点不能被使用。",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("当前作品", response.json()["message"])

    def test_workspace_update_outline_uses_current_project_title_when_id_is_foreign(self):
        project_a = self.create_project("Project A")
        project_b = self.create_project("Project B")
        current_outline_id = self.create_outline_node(project_a, "第151章 众生相")
        foreign_outline_id = self.create_outline_node(project_b, "第151章 众生相")

        db = SessionLocal()
        try:
            result = asyncio.run(_execute_workspace_action(db, project_a, {
                "tool": "update_outline_node",
                "arguments": {
                    "id": foreign_outline_id,
                    "title": "第151章 众生相",
                    "summary": "从归寂谷外的小人物视角展现死线回收。",
                },
            }))
            db.commit()
            self.assertEqual(result["status"], "ok")
            current = db.query(OutlineNode).filter(OutlineNode.id == current_outline_id).first()
            foreign = db.query(OutlineNode).filter(OutlineNode.id == foreign_outline_id).first()
            self.assertIn("死线回收", current.summary)
            self.assertNotIn("死线回收", foreign.summary or "")
        finally:
            db.close()

    def test_workspace_create_chapter_falls_back_to_current_outline_title(self):
        project_a = self.create_project("Project A")
        project_b = self.create_project("Project B")
        current_outline_id = self.create_outline_node(project_a, "第151章 众生相")
        foreign_outline_id = self.create_outline_node(project_b, "第151章 众生相")

        db = SessionLocal()
        try:
            result = asyncio.run(_execute_workspace_action(db, project_a, {
                "tool": "create_chapter",
                "arguments": {
                    "title": "第151章 众生相",
                    "content": "青石村外，死线沿着井沿缓缓收束。",
                    "outline_node_id": foreign_outline_id,
                    "outline_node_title": "第151章 众生相",
                    "summary": "外界视角展现末日景象。",
                },
            }))
            db.commit()
            self.assertEqual(result["status"], "ok")
            chapter = db.query(Chapter).filter(Chapter.project_id == project_a).first()
            self.assertIsNotNone(chapter)
            self.assertEqual(chapter.outline_node_id, current_outline_id)
        finally:
            db.close()

    def test_assistant_history_orders_user_before_assistant_for_same_timestamp(self):
        project_id = self.create_project("History Project")
        same_time = datetime.utcnow()

        db = SessionLocal()
        try:
            conversation = AssistantConversation(
                project_id=project_id,
                title="顺序测试",
                scope="project",
                created_at=same_time,
                updated_at=same_time,
            )
            db.add(conversation)
            db.flush()
            db.add(AssistantMessage(
                conversation_id=conversation.id,
                role="assistant",
                content="先插入的助手消息",
                status="completed",
                created_at=same_time,
                updated_at=same_time,
            ))
            db.add(AssistantMessage(
                conversation_id=conversation.id,
                role="user",
                content="后插入的用户消息",
                status="completed",
                created_at=same_time,
                updated_at=same_time,
            ))
            db.commit()
            conversation_id = conversation.id
        finally:
            db.close()

        response = self.client.get(f"{API_PREFIX}/projects/{project_id}/ai/assistant/conversations/{conversation_id}")
        self.assertEqual(response.status_code, 200)
        roles = [item["role"] for item in response.json()["data"]["messages"]]
        self.assertEqual(roles, ["user", "assistant"])

    @patch("app.routers.ai_writer.LLMGateway.chat_completion", new_callable=AsyncMock)
    def test_workspace_stream_selected_outline_with_links_does_not_detach(self, mock_chat):
        project_id = self.create_project("Workspace Project")
        outline_id = self.create_outline_node(project_id, "第151章 众生相")
        character_id = self.create_character(project_id, "特昂糖")
        mock_chat.return_value = {
            "content": json.dumps({"reply": "已读取当前大纲。", "actions": [], "needs_confirmation": False}, ensure_ascii=False)
        }

        db = SessionLocal()
        try:
            db.add(OutlineNodeCharacter(
                outline_node_id=outline_id,
                character_id=character_id,
                role_in_scene="主视角",
            ))
            db.commit()
        finally:
            db.close()

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "检查当前大纲",
                "selected_outline_node_id": outline_id,
                "auto_apply": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("已读取当前大纲", response.text)
        self.assertNotIn("not bound to a Session", response.text)

    @patch("app.routers.ai_writer.LLMGateway.chat_completion", new_callable=AsyncMock)
    def test_character_change_detection_filters_foreign_ids_and_records_evolution(self, mock_chat):
        project_a = self.create_project("Project A")
        project_b = self.create_project("Project B")
        chapter_id = self.create_chapter(project_a, "林澈在风中悟出御风术。")
        character_id = self.create_character(project_a, "林澈")
        foreign_character_id = self.create_character(project_b, "外部角色")
        mock_chat.return_value = {
            "content": json.dumps([
                {
                    "character_id": character_id,
                    "character_name": "林澈",
                    "change_type": "skill",
                    "field_name": "abilities",
                    "old_value": "",
                    "new_value": "御风术",
                    "confidence": "high",
                },
                {
                    "character_id": foreign_character_id,
                    "character_name": "外部角色",
                    "change_type": "skill",
                    "field_name": "abilities",
                    "old_value": "",
                    "new_value": "不应写入",
                    "confidence": "high",
                },
            ], ensure_ascii=False)
        }

        response = self.client.post(f"{API_PREFIX}/projects/{project_a}/ai/character-changes/{chapter_id}", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["total"], 1)

        db = SessionLocal()
        try:
            logs = db.query(CharacterChangeLog).all()
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].character_id, character_id)
            self.assertEqual(db.query(ChapterCharacter).filter(ChapterCharacter.character_id == character_id).count(), 1)
            self.assertEqual(db.query(CharacterTimeline).filter(CharacterTimeline.character_id == character_id).count(), 1)
            log_id = logs[0].id
        finally:
            db.close()

        confirm = self.client.put(f"{API_PREFIX}/projects/{project_a}/characters/change-logs/{log_id}/confirm")
        self.assertEqual(confirm.status_code, 200)

        db = SessionLocal()
        try:
            character = db.query(Character).filter(Character.id == character_id).first()
            self.assertIn("御风术", json.loads(character.abilities))
            versions = db.query(CharacterVersion).filter(CharacterVersion.character_id == character_id).all()
            self.assertEqual(len(versions), 1)
            self.assertEqual(versions[0].source_chapter_id, chapter_id)
        finally:
            db.close()
