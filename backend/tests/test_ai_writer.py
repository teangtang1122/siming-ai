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
    def test_workspace_stream_repairs_invalid_json_and_executes_create_chapter(self, mock_chat):
        project_id = self.create_project("Workspace Repair Project")
        outline_id = self.create_outline_node(project_id, "第152章 黑潮漫过石阶")
        bad_json = (
            '{"reply":"已创建第152章。","done":true,"actions":[{"tool":"create_chapter","arguments":'
            '{"title":"第152章 黑潮漫过石阶","content":"张虎听见有人喊："第二道防线！" 他握紧剑。",'
            f'"outline_node_id":"{outline_id}","summary":"青云宗第二道防线告破。","involved_characters":[]}}]}}'
        )
        repaired = {
            "reply": "已创建第152章。",
            "done": True,
            "actions": [{
                "tool": "create_chapter",
                "arguments": {
                    "title": "第152章 黑潮漫过石阶",
                    "content": "张虎听见有人喊：“第二道防线！” 他握紧剑。",
                    "outline_node_id": outline_id,
                    "summary": "青云宗第二道防线告破。",
                    "involved_characters": [],
                },
            }],
            "needs_confirmation": False,
        }
        mock_chat.side_effect = [
            {"content": bad_json},
            {"content": json.dumps(repaired, ensure_ascii=False)},
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "你没有成功创建第152章，请重新创建",
                "auto_apply": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("json_repair", response.text)
        self.assertIn("create_chapter", response.text)

        db = SessionLocal()
        try:
            chapter = db.query(Chapter).filter(Chapter.project_id == project_id).one()
            self.assertEqual(chapter.title, "第152章 黑潮漫过石阶")
            self.assertEqual(chapter.outline_node_id, outline_id)
            self.assertIn("第二道防线", chapter.content)
        finally:
            db.close()

    @patch("app.services.workspace.tools.analysis.LLMGateway.chat_completion", new_callable=AsyncMock)
    def test_workspace_character_change_detection_filters_foreign_ids_and_records_evolution(self, mock_chat):
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

        db = SessionLocal()
        try:
            result = asyncio.run(_execute_workspace_action(db, project_a, {
                "tool": "detect_character_changes",
                "arguments": {"chapter_id": chapter_id},
            }))
            db.commit()
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["data"]["total"], 1)
        finally:
            db.close()

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
