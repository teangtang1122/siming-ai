"""Regression tests for AI writing engine project isolation."""

import asyncio
import json
import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from fastapi.testclient import TestClient

from app.core.exceptions import LLMError
from app.database.models import (
    Chapter,
    ChapterCharacter,
    ChapterSnapshot,
    Character,
    CharacterChangeLog,
    CharacterTimeline,
    CharacterVersion,
    AgentRun,
    AgentRunEvent,
    AgentPlan,
    AgentPlanStep,
    AssistantConversation,
    AssistantMessage,
    AssistantRun,
    AssistantRunStep,
    APIConfig,
    OutlineNode,
    OutlineNodeCharacter,
    Project,
    Skill,
)
from app.database.session import Base, SessionLocal, engine
from app.main import app
from app.routers.ai_writer import _execute_workspace_action

API_PREFIX = "/api/v1"


async def async_chunks(text: str):
    yield text


async def async_error_chunks(exc: Exception):
    if False:
        yield ""
    raise exc


async def async_dict_chunks(*chunks: dict):
    for chunk in chunks:
        yield chunk


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
            db.query(AssistantRunStep).delete()
            db.query(AssistantRun).delete()
            db.query(AgentRunEvent).delete()
            db.query(AgentRun).delete()
            db.query(AgentPlanStep).delete()
            db.query(AgentPlan).delete()
            db.query(AssistantMessage).delete()
            db.query(AssistantConversation).delete()
            db.query(APIConfig).delete()
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

    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion")
    def test_workspace_stream_selected_outline_with_links_does_not_detach(self, mock_stream):
        project_id = self.create_project("Workspace Project")
        outline_id = self.create_outline_node(project_id, "第151章 众生相")
        character_id = self.create_character(project_id, "特昂糖")
        mock_stream.return_value = async_chunks(json.dumps(
            {"reply": "已读取当前大纲。", "done": True, "actions": [], "needs_confirmation": False},
            ensure_ascii=False,
        ))

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

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=False)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion")
    def test_workspace_stream_local_cli_plain_text_does_not_require_json(self, mock_stream, mock_supports):
        project_id = self.create_project("CLI Chat Project")
        mock_stream.return_value = async_chunks("你好，我在。")

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "你好？",
                "model": "claude_cli:claude-code",
                "auto_apply": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("你好，我在。", response.text)
        self.assertIn("local_cli_mode", response.text)
        self.assertNotIn("json_repair", response.text)
        self.assertNotIn("模型返回的工具格式不合法", response.text)

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=False)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion")
    def test_workspace_stream_cli_quota_error_does_not_fall_back_to_i_am_here(self, mock_stream, mock_supports):
        project_id = self.create_project("CLI Quota Project")
        mock_stream.return_value = async_error_chunks(LLMError("本机 CLI 提供方额度/限额已耗尽或触发速率限制：Free usage exceeded"))

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "你好？",
                "model": "opencode_cli:opencode/deepseek-v4-flash-free",
                "auto_apply": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("stream_error", response.text)
        self.assertIn("额度/限额", response.text)
        self.assertIn("模型调用中断，未执行写入", response.text)
        self.assertNotIn("我在。", response.text)

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion")
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    def test_workspace_stream_empty_tool_call_response_falls_back_to_text(self, mock_tool_stream, mock_text_stream, mock_supports):
        project_id = self.create_project("Empty Reply Project")
        mock_tool_stream.return_value = async_dict_chunks({"type": "done", "finish_reason": "stop", "usage": None})
        mock_text_stream.return_value = async_chunks("你好，我在。")

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "你好？",
                "model": "openai:gpt-test",
                "auto_apply": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("empty_tool_stream_fallback", response.text)
        self.assertIn("你好，我在。", response.text)
        self.assertNotIn("已完成。", response.text)

    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion")
    def test_workspace_stream_local_runtime_uses_text_mode(self, mock_text_stream, mock_tool_stream):
        project_id = self.create_project("Local Runtime Chat Project")
        mock_text_stream.return_value = async_chunks("你好，我在。")

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "你好？",
                "model": "local_llama_cpp:qwen3-8b-q4",
                "auto_apply": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("当前模型不支持稳定工具调用", response.text)
        self.assertIn("你好，我在。", response.text)
        mock_tool_stream.assert_not_called()

    def test_workspace_chapter_plan_missing_outline_reports_preflight(self):
        project_id = self.create_project("Missing Outline Chapter Project")

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "帮我写第151章",
                "model": "local_llama_cpp:qwen3-8b-q4",
                "auto_apply": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("未找到第 151 章的大纲节点", response.text)
        self.assertIn("你希望这一章往哪个方向推进", response.text)
        self.assertIn("按当前剧情自动规划", response.text)
        self.assertIn("司命本地 AI", response.text)
        self.assertIn("plan_preflight", response.text)
        self.assertNotIn("plan_created", response.text)

    @patch("app.services.workspace.tools.outline_writer.LLMGateway.chat_completion", new_callable=AsyncMock)
    def test_workspace_outline_plan_infers_missing_chapter_and_creates_outline(self, mock_chat):
        project_id = self.create_project("Create Missing Outline Project")
        self.create_outline_node(project_id, "第150章 死线蔓延")
        outline_payload = {
            "nodes": [{
                "title": "第151章 抢网",
                "node_type": "chapter",
                "summary": "主角团在死线继续蔓延前抢占网络节点，准备切断病毒的下一轮扩散。",
                "character_names": [],
                "status": "pending",
            }],
            "design_notes": "承接第150章危机，先补大纲，不生成正文。",
        }
        mock_chat.return_value = {
            "content": "",
            "tool_calls": [{"function": {"arguments": json.dumps(outline_payload, ensure_ascii=False)}}],
        }

        first = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "帮我写第151章",
                "model": "opencode_cli:opencode/deepseek-v4-flash-free",
                "auto_apply": True,
            },
        )
        self.assertIn("未找到第 151 章的大纲节点", first.text)
        conversation_id = None
        for line in first.text.splitlines():
            if not line.startswith("data: ") or line.strip() == "data: [DONE]":
                continue
            event = json.loads(line[6:])
            if event.get("type") == "conversation":
                conversation_id = event["conversation"]["id"]
                break
        self.assertIsNotNone(conversation_id)

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "那就先帮我创建大纲",
                "conversation_id": conversation_id,
                "model": "opencode_cli:opencode/deepseek-v4-flash-free",
                "auto_apply": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("create_outline", response.text)
        self.assertIn("outline_writer", response.text)
        self.assertIn("create_outline_nodes", response.text)
        self.assertIn("第151章 抢网", response.text)

        db = SessionLocal()
        try:
            node = db.query(OutlineNode).filter(
                OutlineNode.project_id == project_id,
                OutlineNode.title == "第151章 抢网",
            ).one_or_none()
            self.assertIsNotNone(node)
            self.assertIn("抢占网络节点", node.summary)
        finally:
            db.close()

    @patch("app.services.workspace.tools.outline_writer.LLMGateway.chat_completion", new_callable=AsyncMock)
    def test_workspace_outline_direction_followup_creates_missing_chapter_outline(self, mock_chat):
        project_id = self.create_project("Outline Direction Followup Project")
        self.create_outline_node(project_id, "第150章 死线蔓延")
        outline_payload = {
            "nodes": [{
                "title": "第151章 抢网",
                "node_type": "chapter",
                "summary": "主角团承接第150章危机，按当前剧情抢占网络节点并阻断死线继续扩张。",
                "character_names": [],
                "status": "pending",
            }],
            "design_notes": "用户要求按当前剧情自动规划缺失章节大纲。",
        }
        mock_chat.return_value = {
            "content": "",
            "tool_calls": [{"function": {"arguments": json.dumps(outline_payload, ensure_ascii=False)}}],
        }

        first = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "帮我写第151章",
                "model": "opencode_cli:opencode/deepseek-v4-flash-free",
                "auto_apply": True,
            },
        )
        self.assertIn("按当前剧情自动规划", first.text)
        conversation_id = None
        for line in first.text.splitlines():
            if not line.startswith("data: ") or line.strip() == "data: [DONE]":
                continue
            event = json.loads(line[6:])
            if event.get("type") == "conversation":
                conversation_id = event["conversation"]["id"]
                break
        self.assertIsNotNone(conversation_id)

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "按当前剧情自动规划",
                "conversation_id": conversation_id,
                "model": "opencode_cli:opencode/deepseek-v4-flash-free",
                "auto_apply": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("create_outline", response.text)
        self.assertIn("outline_writer", response.text)
        self.assertIn("create_outline_nodes", response.text)
        self.assertNotIn("start_cataloging_job", response.text)

        db = SessionLocal()
        try:
            node = db.query(OutlineNode).filter(
                OutlineNode.project_id == project_id,
                OutlineNode.title == "第151章 抢网",
            ).one_or_none()
            self.assertIsNotNone(node)
            self.assertIn("抢占网络节点", node.summary)
        finally:
            db.close()

    @patch("app.services.workspace.tools.outline_writer.LLMGateway.chat_completion", new_callable=AsyncMock)
    def test_workspace_outline_plan_accepts_plain_json_content(self, mock_chat):
        project_id = self.create_project("Plain JSON Outline Project")
        self.create_outline_node(project_id, "Chapter 150 Dead Line")
        outline_payload = {
            "nodes": [{
                "title": "Chapter 151 Network Grab",
                "node_type": "chapter",
                "summary": "The team races to seize a failing network node before the infection line spreads again.",
                "character_names": [],
                "status": "pending",
            }],
            "design_notes": "Plain JSON fallback for local CLI models without tool calls.",
        }
        mock_chat.return_value = {
            "content": "```json\n" + json.dumps({
                "tool": "create_outline_nodes",
                "arguments": outline_payload,
            }, ensure_ascii=False) + "\n```",
            "tool_calls": None,
        }

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "\u5e2e\u6211\u521b\u5efa151\u7ae0\u5927\u7eb2",
                "model": "opencode_cli:opencode/deepseek-v4-flash-free",
                "auto_apply": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("create_outline", response.text)
        self.assertIn("create_outline_nodes", response.text)
        self.assertNotIn("\u5927\u7eb2\u751f\u6210\u7ed3\u679c\u89e3\u6790\u5931\u8d25", response.text)

        db = SessionLocal()
        try:
            node = db.query(OutlineNode).filter(
                OutlineNode.project_id == project_id,
                OutlineNode.title == "Chapter 151 Network Grab",
            ).one_or_none()
            self.assertIsNotNone(node)
            self.assertIn("network node", node.summary)
        finally:
            db.close()

    def test_workspace_chapter_plan_local_runtime_reports_preflight(self):
        project_id = self.create_project("Local Runtime Chapter Project")
        self.create_outline_node(project_id, "第151章 新的死线")

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "帮我写第151章",
                "model": "local_llama_cpp:qwen3-8b-q4",
                "auto_apply": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("司命本地 AI", response.text)
        self.assertNotIn("未找到第 151 章的大纲节点", response.text)
        self.assertNotIn("plan_created", response.text)

    def test_workspace_chapter_plan_bare_number_starts_local_cli_agent(self):
        project_id = self.create_project("Bare Chapter Local CLI Project")
        self.create_outline_node(project_id, "第151章 新的死线")
        db = SessionLocal()
        try:
            db.add(APIConfig(
                provider="opencode_cli",
                provider_type="local_cli",
                api_key_encrypted="",
                default_model="opencode/deepseek-v4-flash-free",
                cli_command="opencode",
                cli_args='["run","--pure","{prompt}"]',
                is_global_default=True,
            ))
            db.commit()
        finally:
            db.close()

        created_coroutines = []

        def capture_task(coroutine):
            created_coroutines.append(coroutine)

            class DummyTask:
                pass

            return DummyTask()

        with patch("app.services.local_cli_agent_worker.asyncio.create_task", side_effect=capture_task):
            response = self.client.post(
                f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
                json={
                    "scope": "project",
                    "message": "帮我写151章",
                    "model": "opencode_cli:opencode/deepseek-v4-flash-free",
                    "auto_apply": True,
                },
            )

        for coroutine in created_coroutines:
            coroutine.close()

        self.assertEqual(response.status_code, 200)
        self.assertIn("local_cli_writing", response.text)
        self.assertIn("start_local_cli_agent_run", response.text)
        self.assertNotIn("local_cli_mode", response.text)
        self.assertNotIn("只有 `read` 工具可用", response.text)

        db = SessionLocal()
        try:
            run = db.query(AgentRun).filter(
                AgentRun.project_id == project_id,
                AgentRun.source == "internal_cli",
                AgentRun.client_name == "opencode_cli",
            ).one_or_none()
            self.assertIsNotNone(run)
            self.assertIn("writing", run.title)
        finally:
            db.close()

    def test_chapter_writer_rejects_local_runtime_model(self):
        project_id = self.create_project("Local Runtime Writer Guard Project")
        outline_id = self.create_outline_node(project_id, "第151章 新的死线")
        db = SessionLocal()
        try:
            result = asyncio.run(_execute_workspace_action(db, project_id, {
                "tool": "chapter_writer",
                "arguments": {
                    "outline_node_id": outline_id,
                    "requirements": "写第151章",
                    "model": "local_llama_cpp:qwen3-8b-q4",
                },
            }))
        finally:
            db.close()

        self.assertEqual(result["status"], "error")
        self.assertIn("司命本地 AI", result["detail"])

    @patch("app.routers.ai_writer.LLMGateway.chat_completion", new_callable=AsyncMock)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion")
    def test_workspace_stream_plan_executes_create_chapter(self, mock_stream, mock_chat):
        project_id = self.create_project("Workspace Repair Project")
        outline_id = self.create_outline_node(project_id, "第152章 黑潮漫过石阶")
        db = SessionLocal()
        try:
            db.add(Skill(
                project_id=project_id,
                name="Plan Skill",
                description="Plan path skill injection regression marker",
                trigger_examples=json.dumps(["152"], ensure_ascii=False),
                system_prompt="PLAN_SKILL_MARKER",
                scope="writing",
                priority=999,
                enabled=True,
                is_builtin=False,
            ))
            db.commit()
        finally:
            db.close()
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
        mock_stream.return_value = async_chunks(bad_json)
        mock_chat.return_value = {"content": json.dumps(repaired, ensure_ascii=False)}

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "你没有成功创建第152章，请重新创建",
                "auto_apply": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("plan_created", response.text)
        self.assertIn("skills_matched", response.text)
        self.assertIn("Plan Skill", response.text)
        self.assertIn("chapter_writer", response.text)
        self.assertIn("create_chapter", response.text)
        self.assertNotIn("json_repair", response.text)

        db = SessionLocal()
        try:
            chapter = db.query(Chapter).filter(Chapter.project_id == project_id).one()
            self.assertEqual(chapter.title, "第152章 黑潮漫过石阶")
            self.assertEqual(chapter.outline_node_id, outline_id)
            self.assertIn("第二道防线", chapter.content)
            plan = db.query(AgentPlan).filter(AgentPlan.project_id == project_id).one()
            steps = db.query(AgentPlanStep).filter(AgentPlanStep.plan_id == plan.id).all()
            self.assertEqual(plan.status, "completed")
            self.assertTrue(any(step.tool == "chapter_writer" and step.status == "ok" for step in steps))
            self.assertTrue(any(step.tool == "create_chapter" and step.status == "ok" for step in steps))
            writer_step = next(step for step in steps if step.tool == "chapter_writer")
            self.assertIn("PLAN_SKILL_MARKER", writer_step.args_json)
        finally:
            db.close()

        runs_response = self.client.get(f"{API_PREFIX}/projects/{project_id}/ai/assistant/runs")
        self.assertEqual(runs_response.status_code, 200)

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
