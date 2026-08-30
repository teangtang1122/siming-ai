from __future__ import annotations

import asyncio
import hashlib
import json
import os
import unittest
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from fastapi.testclient import TestClient

from app.core.exceptions import LLMError
from app.database.models import (
    Chapter,
    ChapterDraft,
    ChapterSnapshot,
    Character,
    ContextManifest,
    ModelContextProfile,
    OutlineNode,
)
from app.database.session import Base, SessionLocal, engine
from app.main import app
from app.modules.assistant.infrastructure.models import (
    AssistantConversation,
    AssistantMessage,
    AssistantRun,
    AssistantRunStep,
    ConversationContextCheckpoint,
    ConversationContextState,
    SystemAssistantConversation,
    SystemAssistantMessage,
)
from app.routers.ai_writer import _execute_workspace_action
from app.services.conversation_context import (
    ConversationContextError,
    ConversationContextErrorCode,
)
from app.services.conversation_context import (
    prepare_conversation_context as real_prepare_conversation_context,
)
from app.services.conversation_context.canonical import canonical_sha256
from app.services.tool_category_state import replace_tool_categories
from app.services.workspace.assistant_public_projection import public_message_payload
from app.services.workspace.generated_drafts import (
    ChapterDraftOutlineConflict,
    PendingChapterDraftConflict,
    store_chapter_draft,
)

API_PREFIX = "/api/v1"


async def async_chunks(text: str):
    yield text


async def async_dict_chunks(*chunks: dict):
    for chunk in chunks:
        yield chunk


class AIChapterDraftFlowTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        with suppress(OSError):
            os.remove("test_novel_agent.db")

    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            db.add_all(
                [
                    ModelContextProfile(
                        provider="openai",
                        model_name="gpt-test",
                        context_window_tokens=2_000_000,
                        max_output_tokens=16_384,
                        safety_margin_tokens=512,
                    ),
                    ModelContextProfile(
                        provider="opencode_cli",
                        model_name="opencode/big-pickle",
                        context_window_tokens=2_000_000,
                        max_output_tokens=16_384,
                        safety_margin_tokens=512,
                    ),
                ]
            )
            db.commit()
        finally:
            db.close()

    def create_project(self, title: str) -> str:
        response = self.client.post(f"{API_PREFIX}/projects", json={"title": title})
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["id"]

    def create_outline(self, project_id: str, title: str) -> str:
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/outline",
            json={"title": title, "node_type": "chapter"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["id"]

    def test_embedded_run_projection_is_a_strict_allowlist(self):
        secret = "sk-legacy-run-payload-secret"
        projected = public_message_payload(
            {
                "reply": "公开回复",
                "assistant_error": {
                    "code": "workspace_assistant_server_error",
                    "message": secret,
                    "details": {"remediation": secret, "api_key": secret},
                },
                "run": {
                    "run_id": "run-1",
                    "id": "run-1",
                    "project_id": "project-1",
                    "status": "error",
                    "current_iteration": 2,
                    "error": f"provider failed with {secret}",
                    "request": {"api_key": secret},
                    "result": {"reasoning_content": secret},
                    "provider_state": secret,
                    "api_key": secret,
                },
            }
        )

        self.assertIsNotNone(projected)
        encoded = json.dumps(projected, ensure_ascii=False)
        self.assertNotIn(secret, encoded)
        self.assertEqual(
            set(projected["run"]),
            {
                "run_id",
                "id",
                "project_id",
                "status",
                "current_iteration",
                "error",
                "error_code",
            },
        )
        self.assertEqual(projected["run"]["error_code"], "workspace_assistant_failed")
        self.assertEqual(
            projected["assistant_error"]["message"],
            "工作台助手处理失败，请稍后重试。",
        )

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer._execute_workspace_action", new_callable=AsyncMock)
    def test_api_model_selects_writer_and_terminal_draft_stops_the_turn(
        self,
        mock_execute,
        mock_stream,
        _mock_supports,
    ):
        project_id = self.create_project("Draft terminal")
        outline_id = self.create_outline(project_id, "第一章 山门")
        draft_id = "draft-terminal-1"
        private_result_secret = "private-success-result-secret"
        mock_execute.return_value = {
            "tool": "chapter_writer",
            "status": "ok",
            "detail": f"章节草稿已生成 /srv/private {private_result_secret}",
            "turn_directive": "end_after_draft",
            "turn_terminal": True,
            "data": {
                "draft_id": draft_id,
                "project_id": project_id,
                "title": "第一章 山门",
                "outline_node_id": outline_id,
                "content": "山门在晨雾中开启。",
                "word_count": 9,
                "draft_status": "pending",
                "path": f"/srv/private/{private_result_secret}",
                "private": {
                    "folder_path": "/srv/private",
                    "accessToken": private_result_secret,
                    "apiKey": private_result_secret,
                },
            },
        }
        mock_stream.side_effect = [
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call-categories",
                    "name": "set_tool_categories",
                    "arguments_delta": json.dumps(
                        {
                            "enabled_categories": ["story_knowledge", "writing_context"],
                        }
                    ),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call-1",
                    "name": "chapter_writer",
                    "arguments_delta": json.dumps({"outline_node_id": outline_id}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "写第一章并自动更新角色",
                "model": "openai:gpt-test",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_stream.call_count, 2)
        self.assertEqual(mock_stream.call_args_list[0].kwargs["tool_choice"], "required")
        self.assertEqual(mock_stream.call_args_list[1].kwargs["tool_choice"], "auto")
        _mock_supports.assert_called_once()
        self.assertEqual(mock_execute.await_count, 1)
        self.assertIn("章节草稿已生成并载入正文编辑器", response.text)
        self.assertIn(draft_id, response.text)
        self.assertNotIn(private_result_secret, response.text)
        self.assertNotIn("/srv/private", response.text)
        db = SessionLocal()
        try:
            self.assertEqual(db.query(Chapter).count(), 0)
            self.assertEqual(db.query(Character).count(), 0)
        finally:
            db.close()

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer._execute_workspace_action", new_callable=AsyncMock)
    def test_outline_draft_is_the_only_business_operation_and_stops_before_formal_write(
        self,
        mock_execute,
        mock_stream,
        _mock_supports,
    ):
        project_id = self.create_project("Outline draft terminal")
        existing_outline_id = self.create_outline(project_id, "第一章 山门")
        draft_id = "outline-draft-terminal-1"
        mock_execute.return_value = {
            "tool": "outline_writer",
            "status": "ok",
            "detail": "大纲草稿已生成，等待作者确认",
            "turn_directive": "end_after_outline_draft",
            "turn_terminal": True,
            "data": {
                "draft_id": draft_id,
                "project_id": project_id,
                "insert_after_id": existing_outline_id,
                "draft_status": "pending",
                "nodes": [
                    {
                        "node_type": "chapter",
                        "title": "第二章 夜雨",
                        "summary": "夜雨袭城。",
                    }
                ],
            },
        }
        mock_stream.side_effect = [
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call-categories",
                    "name": "set_tool_categories",
                    "arguments_delta": json.dumps({"enabled_categories": ["writing_context"]}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call-draft",
                    "name": "outline_writer",
                    "arguments_delta": json.dumps(
                        {"insert_after_id": existing_outline_id},
                    ),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "规划下一章并让我先确认",
                "model": "openai:gpt-test",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_execute.await_count, 1)
        executed_action = mock_execute.await_args.args[2]
        self.assertEqual(executed_action["tool"], "outline_writer")
        self.assertIn("大纲草稿已生成并显示在大纲页", response.text)
        self.assertIn(draft_id, response.text)
        db = SessionLocal()
        try:
            self.assertEqual(db.query(OutlineNode).count(), 1)
        finally:
            db.close()

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer._execute_workspace_action", new_callable=AsyncMock)
    def test_model_selected_chapter_id_is_used_without_editor_target_context(
        self,
        mock_execute,
        mock_stream,
        _mock_supports,
    ):
        project_id = self.create_project("Next chapter target")
        first_outline = self.create_outline(project_id, "第1章 旧梦")
        second_outline = self.create_outline(project_id, "第2章 夜雨")
        db = SessionLocal()
        try:
            db.add(
                Chapter(
                    project_id=project_id,
                    title="第一卷 错误旧标题",
                    outline_node_id=first_outline,
                    content="第一章已经保存并完成建档。",
                    word_count=13,
                    sort_order=1000,
                    cataloging_required=False,
                )
            )
            db.commit()
        finally:
            db.close()
        mock_execute.return_value = {
            "tool": "chapter_writer",
            "status": "ok",
            "detail": "第二章草稿已生成",
            "turn_directive": "end_after_draft",
            "turn_terminal": True,
            "data": {
                "draft_id": "draft-chapter-2",
                "project_id": project_id,
                "title": "第2章 夜雨",
                "outline_node_id": second_outline,
                "content": "夜雨落在山门外。",
                "word_count": 9,
                "draft_status": "pending",
            },
        }
        mock_stream.side_effect = [
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call-categories",
                    "name": "set_tool_categories",
                    "arguments_delta": json.dumps({"enabled_categories": ["writing_context"]}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call-chapter-2",
                    "name": "chapter_writer",
                    "arguments_delta": json.dumps(
                        {
                            "outline_node_id": second_outline,
                            "requirements": "把故事接到夜雨这一章",
                        },
                        ensure_ascii=False,
                    ),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "把故事接到夜雨这一章",
                "model": "openai:gpt-test",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_execute.await_count, 1)
        action = mock_execute.await_args.args[2]
        self.assertEqual(action["tool"], "chapter_writer")
        self.assertEqual(action["arguments"]["outline_node_id"], second_outline)
        self.assertEqual(mock_stream.call_count, 2)
        _mock_supports.assert_called_once()
        initial_messages = mock_stream.call_args_list[0].kwargs["messages"]
        self.assertNotIn(first_outline, initial_messages[1]["content"])
        self.assertIn("把故事接到夜雨这一章", initial_messages[1]["content"])

    def test_chapter_writer_rejects_volume_selected_by_model(self):
        project_id = self.create_project("Chapter target type")
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/outline",
            json={"title": "第一卷 山门", "node_type": "volume"},
        )
        self.assertEqual(response.status_code, 200)
        volume_id = response.json()["data"]["id"]

        db = SessionLocal()
        try:
            result = asyncio.run(
                _execute_workspace_action(
                    db,
                    project_id,
                    {"tool": "chapter_writer", "arguments": {"outline_node_id": volume_id}},
                )
            )
        finally:
            db.close()

        self.assertEqual(result["status"], "skipped")
        self.assertIn("章级节点", result["detail"])

    def test_chapter_writer_rejects_outline_already_linked_to_saved_chapter(self):
        project_id = self.create_project("No AI overwrite")
        outline_id = self.create_outline(project_id, "第一章 山门")
        db = SessionLocal()
        try:
            chapter = Chapter(
                project_id=project_id,
                title="第一章 山门",
                outline_node_id=outline_id,
                content="第一章正式正文，不得覆盖。",
                word_count=13,
                sort_order=1000,
                cataloging_required=False,
            )
            db.add(chapter)
            db.commit()
            chapter_id = chapter.id
            result = asyncio.run(
                _execute_workspace_action(
                    db,
                    project_id,
                    {"tool": "chapter_writer", "arguments": {"outline_node_id": outline_id}},
                )
            )
            db.refresh(chapter)
            self.assertEqual(chapter.content, "第一章正式正文，不得覆盖。")
        finally:
            db.close()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["data"]["existing_chapter_id"], chapter_id)
        self.assertIn("不能覆盖", result["detail"])

    @patch(
        "app.services.workspace.tools.chapter_writer.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_chapter_writer_creates_revision_candidate_for_explicit_target(
        self,
        mock_completion,
    ):
        from app.services.context_orchestrator import ContextOrchestrator

        project_id = self.create_project("Reviewable AI revision")
        outline_id = self.create_outline(project_id, "第一章 山门")
        db = SessionLocal()
        try:
            chapter = Chapter(
                project_id=project_id,
                title="第一章 山门",
                outline_node_id=outline_id,
                content="作者保存的正式正文 v1。",
                word_count=12,
                sort_order=1000,
                current_version=1,
                cataloging_required=False,
            )
            db.add(chapter)
            db.commit()
            chapter_id = str(chapter.id)
            orchestrator = ContextOrchestrator(db)
            manifest = orchestrator.prepare(
                project_id=project_id,
                task_type="writing",
                model="openai:gpt-test",
                arguments={"outline_node_id": outline_id},
            )
            selection = orchestrator.submit_evidence(manifest, [])
            mock_completion.return_value = {
                "content": "AI 独立生成、等待作者审阅的正文候选。",
                "model": "gpt-test",
            }

            result = asyncio.run(_execute_workspace_action(
                db,
                project_id,
                {
                    "tool": "chapter_writer",
                    "arguments": {
                        "outline_node_id": outline_id,
                        "target_chapter_id": chapter_id,
                        "context_manifest_id": manifest.id,
                        "context_selection_token": selection["context_selection_token"],
                    },
                },
            ))

            db.refresh(chapter)
            draft = db.query(ChapterDraft).filter(
                ChapterDraft.project_id == project_id,
                ChapterDraft.status == "pending",
            ).one()
            self.assertEqual(chapter.content, "作者保存的正式正文 v1。")
            self.assertEqual(draft.draft_kind, "revision")
            self.assertEqual(draft.target_chapter_id, chapter_id)
            self.assertEqual(draft.base_chapter_version, 1)
            self.assertEqual(draft.content, "AI 独立生成、等待作者审阅的正文候选。")
        finally:
            db.close()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["draft_kind"], "revision")
        self.assertEqual(result["data"]["target_chapter_id"], chapter_id)
        mock_completion.assert_awaited_once()

    @patch(
        "app.services.workspace.tools.chapter_writer.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_chapter_writer_requires_prior_context_selection_token(self, mock_completion):
        from app.services.context_orchestrator import ContextOrchestrator

        project_id = self.create_project("Focused chapter context")
        outline_id = self.create_outline(project_id, "第一章 城门")
        db = SessionLocal()
        try:
            db.add(
                Character(
                    project_id=project_id,
                    name="不应自动注入的角色",
                    background="这是一张很长但与本章无关的角色卡。",
                )
            )
            db.commit()
            orchestrator = ContextOrchestrator(db)
            manifest = orchestrator.prepare(
                project_id=project_id,
                task_type="writing",
                model="openai:gpt-test",
                arguments={"outline_node_id": outline_id},
            )
            selection = orchestrator.submit_evidence(manifest, [])

            denied = asyncio.run(
                _execute_workspace_action(
                    db,
                    project_id,
                    {
                        "tool": "chapter_writer",
                        "arguments": {
                            "outline_node_id": outline_id,
                            "context_manifest_id": manifest.id,
                            "context_selection_token": "wrong-token",
                        },
                    },
                )
            )
            self.assertEqual(denied["status"], "needs_confirmation")
            mock_completion.assert_not_awaited()

            mock_completion.return_value = {
                "content": "城门在晨雾中缓缓打开。",
                "model": "gpt-test",
            }
            generated = asyncio.run(
                _execute_workspace_action(
                    db,
                    project_id,
                    {
                        "tool": "chapter_writer",
                        "arguments": {
                            "outline_node_id": outline_id,
                            "context_manifest_id": manifest.id,
                            "context_selection_token": selection["context_selection_token"],
                        },
                    },
                )
            )
        finally:
            db.close()

        self.assertEqual(generated["status"], "ok")
        mock_completion.assert_awaited_once()
        prompt = json.dumps(
            mock_completion.await_args.kwargs["messages"],
            ensure_ascii=False,
        )
        self.assertNotIn("不应自动注入的角色", prompt)

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=False)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion")
    def test_cli_chapter_turn_uses_unified_agent_pack_and_terminal_probe(
        self,
        mock_stream,
        _mock_supports,
    ):
        project_id = self.create_project("CLI draft")
        self.create_outline(project_id, "第一章 夜航")
        stream_calls = 0
        runtime_snapshots: list[dict] = []

        def cli_stream(**kwargs):
            nonlocal stream_calls
            stream_calls += 1
            runtime_snapshots.append(dict(kwargs["extra_body"]))
            if stream_calls == 1:
                replace_tool_categories(
                    kwargs["extra_body"]["local_cli_mcp_tool_category_state_file"],
                    ["writing_context"],
                )
                return async_chunks("")
            return async_chunks("已完成项目资料检查")

        mock_stream.side_effect = cli_stream

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "写第一章正文",
                "model": "opencode_cli:opencode/big-pickle",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_stream.call_count, 2)
        runtime = runtime_snapshots[0]
        self.assertEqual(runtime["local_cli_mcp_permission_pack"], "project_management")
        self.assertEqual(runtime["local_cli_terminal_draft_project_id"], project_id)
        self.assertTrue(runtime["local_cli_terminal_draft_run_id"])
        self.assertEqual(runtime["local_cli_terminal_draft_iteration"], 1)
        self.assertTrue(runtime["local_cli_mcp_lease_token"])
        self.assertEqual(runtime["local_cli_retry_attempts"], 1)
        self.assertEqual(runtime_snapshots[1]["local_cli_terminal_draft_iteration"], 2)
        self.assertNotEqual(
            runtime["local_cli_mcp_lease_token"],
            runtime_snapshots[1]["local_cli_mcp_lease_token"],
        )
        self.assertNotIn("local_cli_terminal_draft_excluded_ids", runtime)
        self.assertEqual(mock_stream.call_args_list[0].kwargs["retry"], 0)
        self.assertEqual(mock_stream.call_args_list[0].kwargs["resume"], 0)

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=False)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion")
    def test_cli_natural_language_target_still_has_draft_terminal_probe(
        self,
        mock_stream,
        _mock_supports,
    ):
        project_id = self.create_project("CLI natural target")
        self.create_outline(project_id, "第二章 夜雨")
        stream_calls = 0
        runtime_snapshots: list[dict] = []

        def cli_stream(**kwargs):
            nonlocal stream_calls
            stream_calls += 1
            runtime_snapshots.append(dict(kwargs["extra_body"]))
            if stream_calls == 1:
                replace_tool_categories(
                    kwargs["extra_body"]["local_cli_mcp_tool_category_state_file"],
                    ["writing_context"],
                )
                return async_chunks("")
            return async_chunks("已完成目标读取")

        mock_stream.side_effect = cli_stream

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "把故事接到夜雨里的山门冲突",
                "model": "opencode_cli:opencode/big-pickle",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_stream.call_count, 2)
        runtime = runtime_snapshots[0]
        self.assertEqual(runtime["local_cli_terminal_draft_project_id"], project_id)
        self.assertTrue(runtime["local_cli_terminal_draft_run_id"])
        self.assertEqual(runtime["local_cli_terminal_draft_iteration"], 1)
        self.assertEqual(runtime_snapshots[1]["local_cli_terminal_draft_iteration"], 2)
        self.assertNotEqual(
            runtime["local_cli_mcp_lease_token"],
            runtime_snapshots[1]["local_cli_mcp_lease_token"],
        )
        self.assertNotIn("local_cli_terminal_draft_excluded_ids", runtime)

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=False)
    @patch(
        "app.services.workspace.assistant_direct_mcp_turn.issue_workspace_direct_mcp_lease"
    )
    def test_cli_prestream_lease_failure_log_does_not_emit_secret(
        self,
        mock_issue_lease,
        _mock_supports,
    ):
        project_id = self.create_project("CLI safe prestream log")
        secret = "database secret /private/project/state.json"
        mock_issue_lease.side_effect = RuntimeError(secret)

        with self.assertLogs(
            "app.services.workspace.assistant_turn_runner",
            level="ERROR",
        ) as captured:
            response = self.client.post(
                f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
                json={
                    "scope": "project",
                    "message": "读取作品资料",
                    "model": "opencode_cli:opencode/big-pickle",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(secret, response.text)
        rendered = "\n".join(captured.output)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("/private/project", rendered)
        self.assertIn("RuntimeError", rendered)

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=False)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion")
    def test_cli_text_before_category_controller_is_rejected(self, mock_stream, _mock_supports):
        project_id = self.create_project("CLI controller boundary")
        mock_stream.return_value = async_chunks("工具暂不可用，我先等待。")

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "读取作品资料",
                "model": "opencode_cli:opencode/big-pickle",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(ConversationContextErrorCode.PROTOCOL_INVALID.value, response.text)
        self.assertNotIn(
            "没有调用临时 MCP 中唯一开放的 set_tool_categories",
            response.text,
        )
        self.assertNotIn('"type": "complete"', response.text)

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=False)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion")
    def test_direct_mcp_schemas_are_budgeted_but_never_sent_as_native_tools(
        self,
        mock_stream,
        _mock_supports,
    ):
        project_id = self.create_project("Direct MCP budget")
        stream_calls = 0
        prepared_steps = []

        def cli_stream(**kwargs):
            nonlocal stream_calls
            stream_calls += 1
            if stream_calls == 1:
                replace_tool_categories(
                    kwargs["extra_body"]["local_cli_mcp_tool_category_state_file"],
                    ["story_knowledge"],
                )
                return async_chunks("")
            return async_chunks("已完成安全预算检查。")

        async def prepare_spy(**kwargs):
            prepared = await real_prepare_conversation_context(**kwargs)
            prepared_steps.append((kwargs, prepared))
            return prepared

        mock_stream.side_effect = cli_stream
        with patch(
            "app.routers.ai_writer.prepare_conversation_context",
            new=AsyncMock(side_effect=prepare_spy),
        ):
            response = self.client.post(
                f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
                json={
                    "scope": "project",
                    "message": "读取角色资料",
                    "model": "opencode_cli:opencode/big-pickle",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(prepared_steps), 2)
        for kwargs, prepared in prepared_steps:
            budget_tools = list(kwargs["current_tools"])
            self.assertTrue(budget_tools)
            self.assertEqual(kwargs["protocol"], "direct_mcp")
            self.assertFalse(kwargs["model_capability"].supports_native_tool_calling)
            self.assertTrue(kwargs["model_capability"].direct_mcp_validated)
            self.assertEqual(
                prepared.frame.model_binding.tool_schema_hash,
                canonical_sha256(budget_tools),
            )
            self.assertGreater(prepared.budget.tool_schema_tokens, 0)
            self.assertGreater(
                prepared.budget.max_model_visible_result_tokens_for_open_tools,
                0,
            )
            self.assertGreater(prepared.budget.next_step_wrapper_tokens, 0)
        for call in mock_stream.call_args_list:
            self.assertNotIn("tools", call.kwargs)
            self.assertNotIn("tool_choice", call.kwargs)
        first_schema = json.dumps(
            list(prepared_steps[0][0]["current_tools"]),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertFalse(
            any(
                first_schema == str(message.get("content") or "")
                for message in mock_stream.call_args_list[0].kwargs["messages"]
            )
        )

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    def test_api_text_before_category_controller_is_rejected(self, mock_stream, _mock_supports):
        project_id = self.create_project("API controller boundary")
        mock_stream.return_value = async_dict_chunks(
            {"type": "content_delta", "delta": "工具还没开放，我先等待。"},
            {"type": "done", "finish_reason": "stop", "usage": None},
        )

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "读取作品资料",
                "model": "openai:gpt-test",
            },
        )

        self.assertEqual(mock_stream.call_args.kwargs["tool_choice"], "required")
        self.assertIn(ConversationContextErrorCode.PROTOCOL_INVALID.value, response.text)
        self.assertNotIn(
            "没有调用本步骤唯一开放的 set_tool_categories",
            response.text,
        )
        self.assertNotIn('"type": "complete"', response.text)

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=False)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion")
    @patch("app.routers.ai_writer._execute_workspace_action", new_callable=AsyncMock)
    def test_model_without_native_tools_or_direct_mcp_fails_before_run_or_model(
        self,
        mock_execute,
        mock_text_stream,
        mock_tool_stream,
        _mock_supports,
    ):
        project_id = self.create_project("Unavailable tool capability")

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "读取并修改作品",
                "model": "openai:gpt-test",
            },
        )

        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: {")
        ]
        error = next(event for event in events if event.get("type") == "error")
        self.assertEqual(
            error["code"],
            ConversationContextErrorCode.TOOL_CAPABILITY_UNAVAILABLE.value,
        )
        self.assertEqual(error["details"]["provider"], "openai")
        mock_tool_stream.assert_not_called()
        mock_text_stream.assert_not_called()
        mock_execute.assert_not_awaited()
        db = SessionLocal()
        try:
            self.assertEqual(
                db.query(AssistantRun).filter(AssistantRun.project_id == project_id).count(),
                0,
            )
        finally:
            db.close()

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    def test_hidden_reasoning_is_only_kept_for_provider_continuation(
        self,
        mock_stream,
        _mock_supports,
    ):
        project_id = self.create_project("Reasoning stream")
        mock_stream.side_effect = [
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call-categories",
                    "name": "set_tool_categories",
                    "arguments_delta": json.dumps({"enabled_categories": ["story_knowledge"]}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {"type": "reasoning_delta", "delta": "先核对"},
                {"type": "reasoning_delta", "delta": "作品资料"},
                {"type": "content_delta", "delta": "资料检查完成。"},
                {"type": "done", "finish_reason": "stop", "usage": None},
            ),
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "检查作品资料",
                "model": "openai:gpt-test",
            },
        )

        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: {")
        ]
        reasoning_events = [event for event in events if event.get("type") == "reasoning_delta"]
        self.assertEqual(reasoning_events, [])
        complete = next(event for event in events if event.get("type") == "complete")
        self.assertEqual(complete["data"]["reply"], "资料检查完成。")
        self.assertNotIn("reasoning_content", complete["data"])
        self.assertNotIn("先核对作品资料", response.text)

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer._execute_workspace_action", new_callable=AsyncMock)
    @patch("app.routers.ai_writer.prepare_conversation_context", new_callable=AsyncMock)
    def test_context_preflight_failure_has_stable_sse_and_closes_durable_run(
        self,
        mock_prepare,
        mock_execute,
        mock_stream,
        _mock_supports,
    ):
        for code in (
            ConversationContextErrorCode.CAPACITY_UNKNOWN,
            ConversationContextErrorCode.CHECKPOINT_FAILED,
        ):
            with self.subTest(code=code.value):
                project_id = self.create_project(f"Context failure {code.value}")
                mock_prepare.reset_mock()
                mock_execute.reset_mock()
                mock_stream.reset_mock()
                mock_prepare.side_effect = ConversationContextError(
                    code,
                    f"context preflight blocked: {code.value}",
                    details={"stage": "preflight"},
                )

                response = self.client.post(
                    f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
                    json={
                        "scope": "project",
                        "message": "只在上下文安全时继续",
                        "model": "openai:gpt-test",
                    },
                )

                events = [
                    json.loads(line.removeprefix("data: "))
                    for line in response.text.splitlines()
                    if line.startswith("data: {")
                ]
                error = next(event for event in events if event.get("type") == "error")
                conversation_event = next(
                    event for event in events if event.get("type") == "conversation"
                )
                run_event = next(event for event in events if event.get("type") == "run")
                self.assertEqual(error["code"], code.value)
                self.assertNotIn("stage", error["details"])
                self.assertTrue(error["details"]["retryable"])
                self.assertIn("remediation", error["details"])
                self.assertNotIn("服务器错误", error["message"])
                mock_stream.assert_not_called()
                mock_execute.assert_not_awaited()

                db = SessionLocal()
                try:
                    message = db.get(
                        AssistantMessage,
                        conversation_event["assistant_message"]["id"],
                    )
                    run = db.get(AssistantRun, run_event["run"]["run_id"])
                    self.assertEqual(message.status, "error")
                    self.assertEqual(run.status, "error")
                    self.assertEqual(run.phase, "conversation_context_error")
                    self.assertIn(code.value, run.error)
                finally:
                    db.close()

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer._execute_workspace_action", new_callable=AsyncMock)
    @patch("app.routers.ai_writer.prepare_conversation_context", new_callable=AsyncMock)
    def test_context_error_secret_is_absent_from_sse_and_public_records(
        self,
        mock_prepare,
        mock_execute,
        mock_stream,
        _mock_supports,
    ):
        secret = "sk-context-secret-marker"
        project_id = self.create_project("Context error privacy")
        mock_prepare.side_effect = ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_FAILED,
            f"provider error exposed {secret}",
            details={"provider_raw": secret, "stage": "checkpoint"},
        )

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "安全整理后再继续",
                "model": "openai:gpt-test",
            },
        )

        self.assertNotIn(secret, response.text)
        mock_stream.assert_not_called()
        mock_execute.assert_not_awaited()
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: {")
        ]
        run_id = next(event["run"]["run_id"] for event in events if event["type"] == "run")
        conversation_id = next(
            event["conversation"]["id"] for event in events if event["type"] == "conversation"
        )
        run_detail = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/ai/assistant/runs/{run_id}"
        )
        conversation = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/ai/assistant/conversations/{conversation_id}"
        )
        self.assertNotIn(secret, run_detail.text)
        self.assertNotIn(secret, conversation.text)

        db = SessionLocal()
        try:
            run = db.get(AssistantRun, run_id)
            assistant_message = db.get(AssistantMessage, run.assistant_message_id)
            self.assertNotIn(secret, run.error or "")
            self.assertNotIn(secret, assistant_message.content or "")
            self.assertNotIn(secret, assistant_message.payload_json or "")
            legacy_payload = json.loads(assistant_message.payload_json)
            legacy_payload.update(
                {
                    "reply": f"legacy provider reply {secret}",
                    "run": {
                        "run_id": run.id,
                        "id": run.id,
                        "project_id": project_id,
                        "status": "error",
                        "error": f"provider raw {secret}",
                        "request": {"api_key": secret},
                        "result": {"reasoning_content": secret},
                        "provider_state": secret,
                    },
                }
            )
            assistant_message.payload_json = json.dumps(legacy_payload, ensure_ascii=False)
            db.commit()
        finally:
            db.close()

        legacy_conversation = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/ai/assistant/conversations/{conversation_id}"
        )
        self.assertEqual(legacy_conversation.status_code, 200)
        self.assertNotIn(secret, legacy_conversation.text)

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer._execute_workspace_action", new_callable=AsyncMock)
    def test_model_and_server_exception_secrets_are_not_public_or_persisted(
        self,
        mock_execute,
        mock_stream,
        _mock_supports,
    ):
        cases = (
            (LLMError, "sk-model-exception-secret"),
            (RuntimeError, "sk-server-exception-secret"),
        )
        for exception_type, secret in cases:
            with self.subTest(exception_type=exception_type.__name__):
                project_id = self.create_project(f"Private {exception_type.__name__}")
                mock_stream.reset_mock()
                mock_execute.reset_mock()
                mock_stream.side_effect = exception_type(
                    f"provider request failed api_key={secret}"
                )

                response = self.client.post(
                    f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
                    json={
                        "scope": "project",
                        "message": "安全调用模型",
                        "model": "openai:gpt-test",
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertNotIn(secret, response.text)
                events = [
                    json.loads(line.removeprefix("data: "))
                    for line in response.text.splitlines()
                    if line.startswith("data: {")
                ]
                run_id = next(
                    event["run"]["run_id"] for event in events if event["type"] == "run"
                )
                conversation_id = next(
                    event["conversation"]["id"]
                    for event in events
                    if event["type"] == "conversation"
                )
                self.assertNotIn(
                    secret,
                    self.client.get(
                        f"{API_PREFIX}/projects/{project_id}/ai/assistant/runs/{run_id}"
                    ).text,
                )
                self.assertNotIn(
                    secret,
                    self.client.get(
                        f"{API_PREFIX}/projects/{project_id}/ai/assistant/conversations/"
                        f"{conversation_id}"
                    ).text,
                )
                db = SessionLocal()
                try:
                    run = db.get(AssistantRun, run_id)
                    message = db.get(AssistantMessage, run.assistant_message_id)
                    self.assertNotIn(secret, run.error or "")
                    self.assertNotIn(secret, message.content or "")
                    self.assertNotIn(secret, message.payload_json or "")
                finally:
                    db.close()
                mock_execute.assert_not_awaited()

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer._execute_workspace_action", new_callable=AsyncMock)
    def test_tool_exception_and_arguments_are_absent_from_sse_and_run_api(
        self,
        mock_execute,
        mock_stream,
        _mock_supports,
    ):
        secret = "sk-tool-secret-marker"
        project_id = self.create_project("Tool error privacy")
        mock_execute.side_effect = RuntimeError(f"provider handler exposed {secret}")
        mock_stream.side_effect = [
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "privacy-categories",
                    "name": "set_tool_categories",
                    "arguments_delta": json.dumps({"enabled_categories": ["story_knowledge"]}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "privacy-search",
                    "name": "search_characters",
                    "arguments_delta": json.dumps({"query": secret}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {"type": "content_delta", "delta": "工具失败，已安全停止。"},
                {"type": "done", "finish_reason": "stop", "usage": None},
            ),
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "查询角色",
                "model": "openai:gpt-test",
            },
        )

        self.assertNotIn(secret, response.text)
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: {")
        ]
        self.assertTrue(all("args" not in event for event in events))
        run_id = next(event["run"]["run_id"] for event in events if event["type"] == "run")
        run_detail = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/ai/assistant/runs/{run_id}"
        )
        self.assertEqual(run_detail.status_code, 200)
        self.assertNotIn(secret, run_detail.text)
        detail_payload = run_detail.json()["data"]
        self.assertTrue(all(step.get("request") is None for step in detail_payload["steps"]))
        self.assertTrue(all(step.get("result") is None for step in detail_payload["steps"]))

        db = SessionLocal()
        try:
            steps = (
                db.query(AssistantRunStep)
                .filter(AssistantRunStep.run_id == run_id)
                .order_by(AssistantRunStep.iteration.asc())
                .all()
            )
            failed = next(step for step in steps if step.tool == "search_characters")
            self.assertIn(secret, failed.request_json or "")
            self.assertNotIn(secret, failed.result_json or "")
            self.assertNotIn(secret, failed.detail or "")
            self.assertNotIn(secret, failed.error or "")
        finally:
            db.close()

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer._execute_workspace_action", new_callable=AsyncMock)
    def test_missing_native_call_id_is_protocol_error_before_any_handler(
        self,
        mock_execute,
        mock_stream,
        _mock_supports,
    ):
        project_id = self.create_project("Missing native call id")
        mock_stream.return_value = async_dict_chunks(
            {
                "type": "tool_call_delta",
                "index": 0,
                "name": "set_tool_categories",
                "arguments_delta": json.dumps({"enabled_categories": ["story_knowledge"]}),
            },
            {"type": "done", "finish_reason": "tool_calls", "usage": None},
        )

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "检查资料",
                "model": "openai:gpt-test",
            },
        )

        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: {")
        ]
        error = next(event for event in events if event.get("type") == "error")
        self.assertEqual(
            error["code"],
            ConversationContextErrorCode.PROTOCOL_INVALID.value,
        )
        self.assertEqual(error["details"]["tool"], "set_tool_categories")
        mock_execute.assert_not_awaited()
        db = SessionLocal()
        try:
            self.assertEqual(
                db.query(AssistantRunStep)
                .filter(AssistantRunStep.project_id == project_id)
                .count(),
                0,
            )
        finally:
            db.close()

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer._execute_workspace_action", new_callable=AsyncMock)
    def test_invalid_native_batches_fail_atomically_before_business_handlers(
        self,
        mock_execute,
        mock_stream,
        _mock_supports,
    ):
        valid_category_step = async_dict_chunks(
            {
                "type": "tool_call_delta",
                "index": 0,
                "id": "valid-category",
                "name": "set_tool_categories",
                "arguments_delta": json.dumps(
                    {"enabled_categories": ["story_knowledge", "writing_context"]}
                ),
            },
            {"type": "done", "finish_reason": "tool_calls", "usage": None},
        )
        cases = {
            "empty_arguments": [
                async_dict_chunks(
                    {
                        "type": "tool_call_delta",
                        "index": 0,
                        "id": "empty-arguments",
                        "name": "set_tool_categories",
                    },
                    {"type": "done", "finish_reason": "tool_calls", "usage": None},
                )
            ],
            "invalid_arguments": [
                async_dict_chunks(
                    {
                        "type": "tool_call_delta",
                        "index": 0,
                        "id": "bad-json",
                        "name": "set_tool_categories",
                        "arguments_delta": "{not-json",
                    },
                    {"type": "done", "finish_reason": "tool_calls", "usage": None},
                )
            ],
            "non_object_arguments": [
                async_dict_chunks(
                    {
                        "type": "tool_call_delta",
                        "index": 0,
                        "id": "array-arguments",
                        "name": "set_tool_categories",
                        "arguments_delta": "[]",
                    },
                    {"type": "done", "finish_reason": "tool_calls", "usage": None},
                )
            ],
            "missing_name": [
                async_dict_chunks(
                    {
                        "type": "tool_call_delta",
                        "index": 0,
                        "id": "missing-name",
                        "arguments_delta": "{}",
                    },
                    {"type": "done", "finish_reason": "tool_calls", "usage": None},
                )
            ],
            "mixed_controller": [
                async_dict_chunks(
                    {
                        "type": "tool_call_delta",
                        "index": 0,
                        "id": "controller",
                        "name": "set_tool_categories",
                        "arguments_delta": json.dumps({"enabled_categories": []}),
                    },
                    {
                        "type": "tool_call_delta",
                        "index": 1,
                        "id": "other",
                        "name": "create_character",
                        "arguments_delta": json.dumps({"name": "不应执行"}),
                    },
                    {"type": "done", "finish_reason": "tool_calls", "usage": None},
                )
            ],
            "too_many_calls": [
                async_dict_chunks(
                    *[
                        {
                            "type": "tool_call_delta",
                            "index": index,
                            "id": f"call-{index}",
                            "name": "set_tool_categories",
                            "arguments_delta": json.dumps({"enabled_categories": []}),
                        }
                        for index in range(13)
                    ],
                    {"type": "done", "finish_reason": "tool_calls", "usage": None},
                )
            ],
            "mixed_draft": [
                valid_category_step,
                async_dict_chunks(
                    {
                        "type": "tool_call_delta",
                        "index": 0,
                        "id": "draft-call",
                        "name": "outline_writer",
                        "arguments_delta": "{}",
                    },
                    {
                        "type": "tool_call_delta",
                        "index": 1,
                        "id": "search-call",
                        "name": "search_outlines",
                        "arguments_delta": "{}",
                    },
                    {"type": "done", "finish_reason": "tool_calls", "usage": None},
                ),
            ],
            "mixed_external_outline_draft": [
                async_dict_chunks(
                    {
                        "type": "tool_call_delta",
                        "index": 0,
                        "id": "valid-external-category",
                        "name": "set_tool_categories",
                        "arguments_delta": json.dumps(
                            {"enabled_categories": ["story_knowledge", "writing_context"]}
                        ),
                    },
                    {"type": "done", "finish_reason": "tool_calls", "usage": None},
                ),
                async_dict_chunks(
                    {
                        "type": "tool_call_delta",
                        "index": 0,
                        "id": "external-outline-draft-call",
                        "name": "save_external_outline_draft",
                        "arguments_delta": json.dumps(
                            {
                                "nodes": [{"title": "不应保存"}],
                                "context_manifest_id": "manifest-1",
                                "context_selection_token": "token-1",
                            }
                        ),
                    },
                    {
                        "type": "tool_call_delta",
                        "index": 1,
                        "id": "search-call",
                        "name": "search_context",
                        "arguments_delta": "{}",
                    },
                    {"type": "done", "finish_reason": "tool_calls", "usage": None},
                ),
            ],
        }

        for index, (case, streams) in enumerate(cases.items()):
            with self.subTest(case=case):
                project_id = self.create_project(f"Invalid batch {index}")
                mock_execute.reset_mock()
                mock_stream.reset_mock()
                mock_stream.side_effect = streams

                response = self.client.post(
                    f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
                    json={
                        "scope": "project",
                        "message": "不得执行被篡改的工具批次",
                        "model": "openai:gpt-test",
                    },
                )

                events = [
                    json.loads(line.removeprefix("data: "))
                    for line in response.text.splitlines()
                    if line.startswith("data: {")
                ]
                error = next(event for event in events if event.get("type") == "error")
                self.assertEqual(
                    error["code"],
                    ConversationContextErrorCode.PROTOCOL_INVALID.value,
                )
                mock_execute.assert_not_awaited()
                db = SessionLocal()
                try:
                    step_count = (
                        db.query(AssistantRunStep)
                        .filter(AssistantRunStep.project_id == project_id)
                        .count()
                    )
                    self.assertEqual(
                        step_count,
                        1 if case in {"mixed_draft", "mixed_external_outline_draft"} else 0,
                    )
                finally:
                    db.close()

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer._execute_workspace_action", new_callable=AsyncMock)
    def test_oversized_declared_tool_result_batch_is_rejected_before_any_handler(
        self,
        mock_execute,
        mock_stream,
        _mock_supports,
    ):
        project_id = self.create_project("Tool result batch admission")
        mock_stream.side_effect = [
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "category-call",
                    "name": "set_tool_categories",
                    "arguments_delta": json.dumps({"enabled_categories": ["story_knowledge"]}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "search-chapters-call",
                    "name": "search_chapters",
                    "arguments_delta": json.dumps({"query": "山门"}),
                },
                {
                    "type": "tool_call_delta",
                    "index": 1,
                    "id": "search-outline-call",
                    "name": "search_outline",
                    "arguments_delta": json.dumps({"query": "山门"}),
                },
                {
                    "type": "tool_call_delta",
                    "index": 2,
                    "id": "search-characters-call",
                    "name": "search_characters",
                    "arguments_delta": json.dumps({"query": "守门人"}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {"type": "content_delta", "delta": "已改为分步读取。"},
                {"type": "done", "finish_reason": "stop", "usage": None},
            ),
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "同时读取两类大结果",
                "model": "openai:gpt-test",
            },
        )

        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: {")
        ]
        rejected = [event for event in events if event.get("type") == "tool_result_batch_rejected"]
        self.assertEqual(
            [event["tool"] for event in rejected],
            ["search_chapters", "search_outline", "search_characters"],
        )
        self.assertTrue(
            all(
                event["result"]
                == {
                    "tool": event["tool"],
                    "status": "error",
                    "detail": f"{event['tool']} 执行失败",
                }
                for event in rejected
            )
        )
        self.assertFalse(any(event.get("type") == "error" for event in events))
        self.assertTrue(any(event.get("type") == "complete" for event in events))
        mock_execute.assert_not_awaited()
        third_messages = mock_stream.call_args_list[2].kwargs["messages"]
        result_call_ids = {
            message.get("tool_call_id")
            for message in third_messages
            if message.get("role") == "tool"
        }
        self.assertEqual(
            result_call_ids,
            {
                "search-chapters-call",
                "search-outline-call",
                "search-characters-call",
            },
        )
        db = SessionLocal()
        try:
            steps = (
                db.query(AssistantRunStep).filter(AssistantRunStep.project_id == project_id).all()
            )
            self.assertEqual(
                [step.tool for step in steps],
                [
                    "set_tool_categories",
                    "search_chapters",
                    "search_outline",
                    "search_characters",
                ],
            )
            self.assertEqual(
                [step.status for step in steps],
                ["ok", "error", "error", "error"],
            )
            self.assertEqual(
                [step.step_type for step in steps],
                ["control", "search", "search", "search"],
            )
        finally:
            db.close()

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer._execute_workspace_action", new_callable=AsyncMock)
    def test_two_common_search_results_are_admitted_and_delivered_atomically(
        self,
        mock_execute,
        mock_stream,
        _mock_supports,
    ):
        project_id = self.create_project("Two search result admission")
        mock_execute.side_effect = [
            {
                "tool": "search_chapters",
                "status": "ok",
                "detail": "章节检索完成",
                "data": {"items": [], "query": "山门"},
            },
            {
                "tool": "search_outline",
                "status": "ok",
                "detail": "大纲检索完成",
                "data": {"items": [], "query": "山门"},
            },
        ]
        mock_stream.side_effect = [
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "two-search-categories",
                    "name": "set_tool_categories",
                    "arguments_delta": json.dumps({"enabled_categories": ["story_knowledge"]}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "two-search-chapters",
                    "name": "search_chapters",
                    "arguments_delta": '{ "query" : "山门" }',
                },
                {
                    "type": "tool_call_delta",
                    "index": 1,
                    "id": "two-search-outline",
                    "name": "search_outline",
                    "arguments_delta": json.dumps({"query": "山门"}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {"type": "content_delta", "delta": "两类资料已核对。"},
                {"type": "done", "finish_reason": "stop", "usage": None},
            ),
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "同时核对章节和大纲",
                "model": "openai:gpt-test",
            },
        )

        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: {")
        ]
        self.assertFalse(any(event.get("type") == "tool_result_batch_rejected" for event in events))
        self.assertFalse(any(event.get("type") == "error" for event in events))
        self.assertTrue(any(event.get("type") == "complete" for event in events))
        self.assertEqual(mock_execute.await_count, 2)
        delivered_messages = mock_stream.call_args_list[2].kwargs["messages"]
        native_assistant = next(
            message for message in delivered_messages if message.get("tool_calls")
        )
        self.assertEqual(
            native_assistant["tool_calls"][0]["function"]["arguments"],
            '{ "query" : "山门" }',
        )
        self.assertEqual(
            {
                message.get("tool_call_id")
                for message in delivered_messages
                if message.get("role") == "tool"
            },
            {"two-search-chapters", "two-search-outline"},
        )
        db = SessionLocal()
        try:
            steps = (
                db.query(AssistantRunStep)
                .filter(AssistantRunStep.project_id == project_id)
                .order_by(AssistantRunStep.iteration.asc(), AssistantRunStep.created_at.asc())
                .all()
            )
            self.assertEqual(
                [step.tool for step in steps],
                ["set_tool_categories", "search_chapters", "search_outline"],
            )
            self.assertTrue(all(step.status == "ok" for step in steps))
        finally:
            db.close()

    @patch(
        "app.services.conversation_context.preparation._default_checkpoint_completion"
    )
    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer._execute_workspace_action", new_callable=AsyncMock)
    def test_long_history_checkpoint_then_native_category_and_read_stay_structured(
        self,
        mock_execute,
        mock_stream,
        _mock_supports,
        mock_checkpoint_factory,
    ):
        project_id = self.create_project("Checkpoint native chain")
        db = SessionLocal()
        try:
            profile = (
                db.query(ModelContextProfile)
                .filter(
                    ModelContextProfile.provider == "openai",
                    ModelContextProfile.model_name == "gpt-test",
                )
                .one()
            )
            profile.context_window_tokens = 300_000
            profile.max_output_tokens = 4_096
            conversation = AssistantConversation(
                project_id=project_id,
                title="Long native transcript",
                scope="project",
                model="openai:gpt-test",
            )
            db.add(conversation)
            db.flush()
            for turn_index in range(120):
                sequence = turn_index * 2 + 1
                db.add_all(
                    [
                        AssistantMessage(
                            conversation_id=conversation.id,
                            sequence_no=sequence,
                            role="user",
                            content=f"old-user-{turn_index}:" + "u" * 1_400,
                            status="completed",
                        ),
                        AssistantMessage(
                            conversation_id=conversation.id,
                            sequence_no=sequence + 1,
                            role="assistant",
                            content=f"old-assistant-{turn_index}:" + "a" * 1_400,
                            status="completed",
                        ),
                    ]
                )
            db.commit()
            conversation_id = str(conversation.id)
        finally:
            db.close()

        checkpoint_requests: list[dict] = []

        async def checkpoint_completion(**kwargs):
            checkpoint_requests.append(kwargs)
            return {
                "content": json.dumps(
                    {
                        "schema": "conversation_checkpoint_navigation.v1",
                        "semantic_navigation": {
                            "authority": "non_authoritative_navigation",
                            "current_objectives": ["核对山门大纲"],
                            "resolved_decisions": [],
                            "superseded_directions": [],
                            "unresolved_questions": [],
                            "next_context_needed": ["读取真实大纲"],
                        },
                        "author_quote_positions": [],
                        "prior_author_quote_states": [],
                    },
                    ensure_ascii=False,
                )
            }

        mock_checkpoint_factory.return_value = checkpoint_completion
        mock_execute.return_value = {
            "tool": "search_outline",
            "status": "ok",
            "detail": "大纲检索完成",
            "data": {"items": [], "query": "山门"},
        }
        mock_stream.side_effect = [
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "checkpoint-category",
                    "name": "set_tool_categories",
                    "arguments_delta": json.dumps(
                        {"enabled_categories": ["story_knowledge"]}
                    ),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "checkpoint-read",
                    "name": "search_outline",
                    "arguments_delta": json.dumps({"query": "山门"}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {"type": "content_delta", "delta": "已依据真实大纲核对。"},
                {"type": "done", "finish_reason": "stop", "usage": None},
            ),
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "conversation_id": conversation_id,
                "message": "逐字保留：请核对山门大纲",
                "model": "openai:gpt-test",
                "max_tokens": 4_096,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('"type": "error"', response.text)
        self.assertTrue(checkpoint_requests)
        self.assertTrue(
            all(request["tools"] == [] and request["tool_choice"] == "none"
                for request in checkpoint_requests)
        )
        self.assertEqual(mock_stream.call_count, 3)
        self.assertEqual(mock_execute.await_count, 1)
        self.assertEqual(mock_execute.call_args.args[2]["tool"], "search_outline")
        first_messages = mock_stream.call_args_list[0].kwargs["messages"]
        self.assertEqual(first_messages[-1]["role"], "user")
        self.assertEqual(first_messages[-1]["content"], "逐字保留：请核对山门大纲")
        self.assertTrue(
            any("[HISTORICAL_REFERENCE_DATA]" in str(item.get("content") or "")
                for item in first_messages)
        )
        delivered = mock_stream.call_args_list[2].kwargs["messages"]
        native_call = next(item for item in delivered if item.get("tool_calls"))
        self.assertEqual(
            native_call["tool_calls"][0]["function"]["name"],
            "search_outline",
        )
        self.assertTrue(
            any(item.get("tool_call_id") == "checkpoint-read" for item in delivered)
        )
        db = SessionLocal()
        try:
            checkpoints = (
                db.query(ConversationContextCheckpoint)
                .filter(
                    ConversationContextCheckpoint.assistant_conversation_id
                    == conversation_id
                )
                .all()
            )
            self.assertTrue(checkpoints)
            self.assertTrue(any(item.status == "ready" for item in checkpoints))
        finally:
            db.close()

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer._execute_workspace_action", new_callable=AsyncMock)
    def test_oversized_native_assistant_transaction_persists_denials_then_stops(
        self,
        mock_execute,
        mock_stream,
        _mock_supports,
    ):
        project_id = self.create_project("Native assistant transaction capacity")
        mock_stream.side_effect = [
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "capacity-categories",
                    "name": "set_tool_categories",
                    "arguments_delta": json.dumps({"enabled_categories": ["story_knowledge"]}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "oversized-search-call",
                    "name": "search_outline",
                    "arguments_delta": json.dumps({"query": "山" * 20_000}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "执行一个过大的原生调用",
                "model": "openai:gpt-test",
            },
        )

        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: {")
        ]
        rejected = next(
            event for event in events if event.get("type") == "tool_result_batch_rejected"
        )
        self.assertEqual(
            rejected["result"],
            {
                "tool": "search_outline",
                "status": "error",
                "detail": "search_outline 执行失败",
            },
        )
        error = next(event for event in events if event.get("type") == "error")
        self.assertEqual(
            error["code"],
            ConversationContextErrorCode.PROTOCOL_INVALID.value,
        )
        self.assertEqual(
            error["details"]["reason"],
            "native_assistant_transaction_over_capacity",
        )
        self.assertFalse(any(event.get("type") == "complete" for event in events))
        self.assertEqual(mock_stream.call_count, 2)
        mock_execute.assert_not_awaited()
        db = SessionLocal()
        try:
            steps = (
                db.query(AssistantRunStep)
                .filter(AssistantRunStep.project_id == project_id)
                .order_by(AssistantRunStep.iteration.asc())
                .all()
            )
            self.assertEqual(
                [step.tool for step in steps],
                ["set_tool_categories", "search_outline"],
            )
            self.assertEqual([step.status for step in steps], ["ok", "error"])
            self.assertEqual([step.step_type for step in steps], ["control", "search"])
            denied_request = json.loads(steps[1].request_json)
            self.assertEqual(
                denied_request["native_call_id"],
                "oversized-search-call",
            )
        finally:
            db.close()

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    def test_native_transactions_preserve_provider_continuation_then_compact_to_receipt(
        self,
        mock_stream,
        _mock_supports,
    ):
        project_id = self.create_project("Provider continuation")
        current_user = "只按这条原文继续，不要复述成另一条指令。"
        selected_text = "作者选中的正文片段。"
        first_provider_state = {"type": "responses_state", "id": "state-1"}
        second_provider_state = {"type": "responses_state", "id": "state-2"}
        mock_stream.side_effect = [
            async_dict_chunks(
                {"type": "reasoning_delta", "delta": "先选择资料能力"},
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call-categories-1",
                    "name": "set_tool_categories",
                    "arguments_delta": json.dumps({"enabled_categories": ["story_knowledge"]}),
                },
                {
                    "type": "done",
                    "finish_reason": "tool_calls",
                    "usage": None,
                    "provider_state": [first_provider_state],
                },
            ),
            async_dict_chunks(
                {"type": "reasoning_delta", "delta": "再切换写作能力"},
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call-categories-2",
                    "name": "set_tool_categories",
                    "arguments_delta": json.dumps({"enabled_categories": ["writing_context"]}),
                },
                {
                    "type": "done",
                    "finish_reason": "tool_calls",
                    "usage": None,
                    "provider_state": [second_provider_state],
                },
            ),
            async_dict_chunks(
                {"type": "content_delta", "delta": "已按当前要求完成检查。"},
                {"type": "done", "finish_reason": "stop", "usage": None},
            ),
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": current_user,
                "selected_text": selected_text,
                "outline_batch_count": 5,
                "model": "openai:gpt-test",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_stream.call_count, 3)
        first_messages = mock_stream.call_args_list[0].kwargs["messages"]
        second_messages = mock_stream.call_args_list[1].kwargs["messages"]
        third_messages = mock_stream.call_args_list[2].kwargs["messages"]
        self.assertEqual(
            [message["content"] for message in first_messages if message["role"] == "user"],
            [current_user],
        )
        self.assertIn("[SERVER_WORKSPACE_RUNTIME_DATA]", first_messages[0]["content"])
        self.assertIn(project_id, first_messages[0]["content"])
        self.assertIn(selected_text, first_messages[0]["content"])
        self.assertIn('"outline_batch_count":5', first_messages[0]["content"])

        first_native_assistant = next(
            message
            for message in second_messages
            if (message.get("tool_calls") or [{}])[0].get("id") == "call-categories-1"
        )
        self.assertEqual(first_native_assistant["reasoning_content"], "先选择资料能力")
        self.assertEqual(first_native_assistant["provider_state"], [first_provider_state])

        self.assertFalse(
            any(
                (message.get("tool_calls") or [{}])[0].get("id") == "call-categories-1"
                for message in third_messages
            )
        )
        receipt = next(
            message
            for message in third_messages
            if "[SERVER_VERIFIED_EXECUTION_RECEIPTS]" in message.get("content", "")
        )
        self.assertIn("set_tool_categories", receipt["content"])
        second_native_assistant = next(
            message
            for message in third_messages
            if (message.get("tool_calls") or [{}])[0].get("id") == "call-categories-2"
        )
        self.assertEqual(second_native_assistant["reasoning_content"], "再切换写作能力")
        self.assertEqual(second_native_assistant["provider_state"], [second_provider_state])

        db = SessionLocal()
        try:
            steps = (
                db.query(AssistantRunStep)
                .filter(AssistantRunStep.project_id == project_id)
                .order_by(AssistantRunStep.iteration.asc())
                .all()
            )
            self.assertEqual(len(steps), 2)
            self.assertTrue(all(step.status == "ok" for step in steps))
            self.assertTrue(all(json.loads(step.result_json)["data"] for step in steps))
        finally:
            db.close()

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    @patch("app.routers.ai_writer._execute_workspace_action", new_callable=AsyncMock)
    def test_reference_audit_change_stops_before_any_business_handler(
        self,
        mock_execute,
        mock_stream,
        _mock_supports,
    ):
        project_id = self.create_project("Reference audit consistency")
        current_user = "依据附件检查项目"
        original_reference = "作者附加的原始资料"
        changed_reference = "并发篡改后的资料"

        async def category_then_change_durable_reference():
            yield {
                "type": "tool_call_delta",
                "index": 0,
                "id": "reference-categories",
                "name": "set_tool_categories",
                "arguments_delta": json.dumps({"enabled_categories": ["story_knowledge"]}),
            }
            yield {"type": "done", "finish_reason": "tool_calls", "usage": None}
            tamper_db = SessionLocal()
            try:
                user_message = (
                    tamper_db.query(AssistantMessage)
                    .filter(
                        AssistantMessage.role == "user",
                        AssistantMessage.content == current_user,
                    )
                    .one()
                )
                user_message.payload_json = json.dumps(
                    {
                        "reference_context": {
                            "source_kind": "attachment",
                            "source_name": "evidence.txt",
                            "content": changed_reference,
                            "coverage": "full",
                            "source_chars": len(changed_reference),
                            "content_sha256": hashlib.sha256(
                                changed_reference.encode("utf-8")
                            ).hexdigest(),
                        }
                    },
                    ensure_ascii=False,
                )
                tamper_db.commit()
            finally:
                tamper_db.close()

        mock_stream.return_value = category_then_change_durable_reference()
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": current_user,
                "reference_context": {
                    "source_kind": "attachment",
                    "source_name": "evidence.txt",
                    "content": original_reference,
                    "coverage": "full",
                    "source_chars": len(original_reference),
                },
                "model": "openai:gpt-test",
            },
        )

        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: {")
        ]
        error = next(event for event in events if event.get("type") == "error")
        self.assertEqual(
            error["code"],
            ConversationContextErrorCode.SOURCE_CHANGED.value,
        )
        self.assertFalse(any(event.get("type") == "complete" for event in events))
        self.assertEqual(mock_stream.call_count, 1)
        mock_execute.assert_not_awaited()
        db = SessionLocal()
        try:
            run = db.query(AssistantRun).filter(AssistantRun.project_id == project_id).one()
            self.assertEqual(run.status, "error")
            self.assertEqual(run.phase, "conversation_context_error")
            steps = db.query(AssistantRunStep).filter(AssistantRunStep.run_id == run.id).all()
            self.assertEqual(
                [(step.tool, step.step_type, step.status) for step in steps],
                [("set_tool_categories", "control", "ok")],
            )
        finally:
            db.close()

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    def test_canonical_system_conversation_bootstraps_exact_prior_transcript(
        self,
        mock_stream,
        _mock_supports,
    ):
        project_id = self.create_project("Canonical bootstrap")
        canonical_id = "canonical-workspace-thread"
        current_user = "当前原文任务"
        reference_context = "仅作为资料的数据块：" + "甲乙丙" * 200
        db = SessionLocal()
        try:
            db.add(
                SystemAssistantConversation(
                    id=canonical_id,
                    title="Canonical project chat",
                    scope_type="project",
                    scope_id=project_id,
                    project_id=project_id,
                )
            )
            db.add_all(
                [
                    SystemAssistantMessage(
                        id="canonical-user-1",
                        conversation_id=canonical_id,
                        sequence_no=1,
                        role="user",
                        content="历史作者原文",
                        status="completed",
                    ),
                    SystemAssistantMessage(
                        id="canonical-assistant-1",
                        conversation_id=canonical_id,
                        sequence_no=2,
                        role="assistant",
                        content="历史助手原文",
                        status="completed",
                    ),
                    SystemAssistantMessage(
                        id="canonical-user-current",
                        conversation_id=canonical_id,
                        sequence_no=3,
                        role="user",
                        content=current_user,
                        status="completed",
                    ),
                    SystemAssistantMessage(
                        id="canonical-assistant-current",
                        conversation_id=canonical_id,
                        sequence_no=4,
                        role="assistant",
                        content="",
                        status="running",
                    ),
                ]
            )
            db.commit()
        finally:
            db.close()
        mock_stream.side_effect = [
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "canonical-categories",
                    "name": "set_tool_categories",
                    "arguments_delta": json.dumps({"enabled_categories": []}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {"type": "content_delta", "delta": "当前任务已完成。"},
                {"type": "done", "finish_reason": "stop", "usage": None},
            ),
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "canonical_conversation_id": canonical_id,
                "message": current_user,
                "reference_context": {
                    "source_kind": "long_text",
                    "source_name": "聊天长文本.txt",
                    "content": reference_context,
                    "coverage": "full",
                    "source_chars": len(reference_context),
                },
                "model": "openai:gpt-test",
            },
        )

        self.assertEqual(response.status_code, 200)
        first_messages = mock_stream.call_args_list[0].kwargs["messages"]
        self.assertIn("[CURRENT_TURN_REFERENCE_DATA]", first_messages[0]["content"])
        self.assertIn("authority: untrusted_data_only", first_messages[0]["content"])
        self.assertIn(reference_context, first_messages[0]["content"])
        self.assertIn("instruction_priority: none", first_messages[0]["content"])
        self.assertEqual(
            [
                (message["role"], message["content"])
                for message in first_messages
                if message["role"] != "system"
            ],
            [
                ("user", "历史作者原文"),
                ("assistant", "历史助手原文"),
                ("user", current_user),
            ],
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: {")
        ]
        conversation_event = next(event for event in events if event.get("type") == "conversation")
        execution_conversation_id = conversation_event["conversation"]["id"]
        db = SessionLocal()
        try:
            rows = (
                db.query(AssistantMessage)
                .filter(AssistantMessage.conversation_id == execution_conversation_id)
                .order_by(AssistantMessage.sequence_no.asc())
                .all()
            )
            self.assertEqual(
                [(row.role, row.content) for row in rows],
                [
                    ("user", "历史作者原文"),
                    ("assistant", "历史助手原文"),
                    ("user", current_user),
                    ("assistant", "当前任务已完成。"),
                ],
            )
            expected_reference_hash = hashlib.sha256(reference_context.encode("utf-8")).hexdigest()
            durable_reference = json.loads(rows[2].payload_json)["reference_context"]
            self.assertEqual(
                durable_reference,
                {
                    "source_kind": "long_text",
                    "source_name": "聊天长文本.txt",
                    "content": reference_context,
                    "coverage": "full",
                    "source_chars": len(reference_context),
                    "content_sha256": expected_reference_hash,
                },
            )
            assistant_audit = json.loads(rows[3].payload_json)["reference_context_audit"]
            self.assertEqual(
                assistant_audit,
                {
                    "source_kind": "long_text",
                    "source_name": "聊天长文本.txt",
                    "coverage": "full",
                    "source_chars": len(reference_context),
                    "content_sha256": expected_reference_hash,
                },
            )
            run = (
                db.query(AssistantRun)
                .filter(AssistantRun.conversation_id == execution_conversation_id)
                .one()
            )
            self.assertEqual(run.user_message_id, rows[2].id)
            self.assertEqual(run.assistant_message_id, rows[3].id)
            context_state = (
                db.query(ConversationContextState)
                .filter(
                    ConversationContextState.assistant_conversation_id == execution_conversation_id
                )
                .one()
            )
            self.assertGreaterEqual(
                context_state.last_budget_json["system_prompt_tokens"],
                len(reference_context.encode("utf-8")),
            )
        finally:
            db.close()

        mock_stream.reset_mock()
        mock_stream.side_effect = [
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "next-categories",
                    "name": "set_tool_categories",
                    "arguments_delta": json.dumps({"enabled_categories": []}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {"type": "content_delta", "delta": "下一任务已完成。"},
                {"type": "done", "finish_reason": "stop", "usage": None},
            ),
        ]
        follow_up = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "canonical_conversation_id": canonical_id,
                "conversation_id": execution_conversation_id,
                "message": "继续下一任务",
                "model": "openai:gpt-test",
            },
        )
        self.assertEqual(follow_up.status_code, 200)
        follow_up_messages = mock_stream.call_args_list[0].kwargs["messages"]
        self.assertFalse(
            any(
                reference_context in str(message.get("content") or "")
                for message in follow_up_messages
            )
        )

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.chat_completion", new_callable=AsyncMock)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    def test_saved_uncataloged_chapter_blocks_writer_before_generation_call(
        self,
        mock_stream,
        mock_chat,
        _mock_supports,
    ):
        project_id = self.create_project("Cataloging gate")
        first_outline = self.create_outline(project_id, "第一章 山门")
        second_outline = self.create_outline(project_id, "第二章 夜雨")
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters",
            json={
                "title": "第一章 山门",
                "outline_node_id": first_outline,
                "content": "山门在雨中开启。",
                "cataloging_mode": "save_only",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["cataloging_required"])
        mock_stream.side_effect = [
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call-categories",
                    "name": "set_tool_categories",
                    "arguments_delta": json.dumps({"enabled_categories": ["writing_context"]}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call-blocked-writer",
                    "name": "chapter_writer",
                    "arguments_delta": json.dumps({"outline_node_id": second_outline}),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/ai/workspace-assistant/stream",
            json={
                "scope": "project",
                "message": "写第二章正文",
                "model": "openai:gpt-test",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("已保存但尚未完成建档", response.text)
        self.assertEqual(mock_stream.call_count, 2)
        mock_chat.assert_not_awaited()

    def test_pending_draft_is_restored_and_only_author_save_creates_chapter(self):
        project_id = self.create_project("Author save")
        first_outline_id = self.create_outline(project_id, "第一章 旧井")
        outline_id = self.create_outline(project_id, "第二章 潮声")
        db = SessionLocal()
        try:
            first_chapter = Chapter(
                project_id=project_id,
                title="第一章 旧井",
                outline_node_id=first_outline_id,
                content="旧井边的第一章正式正文。",
                word_count=13,
                sort_order=1000,
                cataloging_required=False,
            )
            db.add(first_chapter)
            draft = ChapterDraft(
                project_id=project_id,
                title="第二章 潮声",
                outline_node_id=outline_id,
                content="潮声从空井里漫出来。",
                status="pending",
            )
            db.add(draft)
            db.commit()
            draft_id = draft.id
        finally:
            db.close()

        restored = self.client.get(f"{API_PREFIX}/projects/{project_id}/chapter-drafts/pending")
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["data"]["draft_id"], draft_id)
        db = SessionLocal()
        try:
            self.assertEqual(db.query(Chapter).count(), 1)
        finally:
            db.close()

        saved = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters",
            json={
                "title": "第二章 潮声",
                "outline_node_id": outline_id,
                "content": "潮声从空井里漫出来。",
                "draft_id": draft_id,
                "cataloging_mode": "save_only",
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["data"]["cataloging_required"])
        db = SessionLocal()
        try:
            self.assertEqual(db.query(Chapter).count(), 2)
            first = db.query(Chapter).filter(Chapter.outline_node_id == first_outline_id).one()
            self.assertEqual(first.content, "旧井边的第一章正式正文。")
            self.assertEqual(db.get(ChapterDraft, draft_id).status, "saved")
        finally:
            db.close()

    def test_reviewed_draft_can_be_saved_after_its_manifest_becomes_stale(self):
        project_id = self.create_project("Stale provenance save")
        outline_id = self.create_outline(project_id, "第二章 晨光")
        db = SessionLocal()
        try:
            manifest = ContextManifest(
                project_id=project_id,
                task_type="writing",
                execution_route="internal_api",
                status="stale",
                stale_reason="Source changed: character:character-1",
            )
            db.add(manifest)
            db.flush()
            draft = ChapterDraft(
                project_id=project_id,
                title="第二章 晨光",
                outline_node_id=outline_id,
                context_manifest_id=manifest.id,
                content="这是作者已经审阅过的草稿正文。",
                status="pending",
            )
            db.add(draft)
            db.commit()
            draft_id = str(draft.id)
            manifest_id = str(manifest.id)
        finally:
            db.close()

        saved = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters",
            json={
                "title": "第二章 晨光",
                "outline_node_id": outline_id,
                "content": "这是作者已经审阅过的草稿正文。",
                "context_manifest_id": manifest_id,
                "draft_id": draft_id,
                "cataloging_mode": "save_only",
            },
        )

        self.assertEqual(saved.status_code, 200, saved.text)
        db = SessionLocal()
        try:
            stored_draft = db.get(ChapterDraft, draft_id)
            self.assertEqual(stored_draft.status, "saved")
            chapter = db.get(Chapter, stored_draft.saved_chapter_id)
            self.assertEqual(chapter.context_manifest_id, manifest_id)
        finally:
            db.close()

    def test_draft_manifest_provenance_cannot_be_replaced_during_save(self):
        project_id = self.create_project("Draft provenance")
        db = SessionLocal()
        try:
            original = ContextManifest(project_id=project_id, task_type="writing")
            replacement = ContextManifest(project_id=project_id, task_type="writing")
            db.add_all([original, replacement])
            db.flush()
            draft = ChapterDraft(
                project_id=project_id,
                title="第一章",
                context_manifest_id=original.id,
                content="草稿正文。",
                status="pending",
            )
            db.add(draft)
            db.commit()
            draft_id = str(draft.id)
            replacement_id = str(replacement.id)
        finally:
            db.close()

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters",
            json={
                "title": "第一章",
                "content": "草稿正文。",
                "context_manifest_id": replacement_id,
                "draft_id": draft_id,
                "cataloging_mode": "save_only",
            },
        )

        self.assertEqual(response.status_code, 400)
        db = SessionLocal()
        try:
            self.assertEqual(db.query(Chapter).count(), 0)
            self.assertEqual(db.get(ChapterDraft, draft_id).status, "pending")
        finally:
            db.close()

    def test_author_can_discard_pending_draft_and_reuse_editor_slot(self):
        project_id = self.create_project("Discard draft")
        db = SessionLocal()
        try:
            draft = ChapterDraft(
                project_id=project_id,
                title="不再需要的草稿",
                content="不会进入正式正文。",
                status="pending",
            )
            db.add(draft)
            db.commit()
            draft_id = str(draft.id)
        finally:
            db.close()

        discarded = self.client.delete(
            f"{API_PREFIX}/projects/{project_id}/chapter-drafts/{draft_id}"
        )
        repeated = self.client.delete(
            f"{API_PREFIX}/projects/{project_id}/chapter-drafts/{draft_id}"
        )
        resurrected = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters",
            json={
                "title": "不应复活的草稿",
                "content": "这次保存必须被拒绝。",
                "draft_id": draft_id,
                "cataloging_mode": "save_only",
            },
        )
        restored = self.client.get(
            f"{API_PREFIX}/projects/{project_id}/chapter-drafts/pending"
        )

        self.assertEqual(discarded.status_code, 200, discarded.text)
        self.assertEqual(discarded.json()["data"]["draft_status"], "discarded")
        self.assertEqual(discarded.json()["data"]["next_actions"], [])
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(resurrected.status_code, 400, resurrected.text)
        self.assertIsNone(restored.json()["data"])
        db = SessionLocal()
        try:
            self.assertEqual(db.get(ChapterDraft, draft_id).status, "discarded")
            next_id = store_chapter_draft(
                project_id=project_id,
                title="新的草稿",
                content="可以继续写作。",
                db=db,
            )
            self.assertEqual(db.get(ChapterDraft, next_id).status, "pending")
            self.assertEqual(db.query(Chapter).count(), 0)
        finally:
            db.close()

    def test_first_completed_draft_keeps_the_only_pending_editor_slot(self):
        project_id = self.create_project("Concurrent draft completion")
        outline_id = self.create_outline(project_id, "第一章 山雨")
        db = SessionLocal()
        try:
            first_id = store_chapter_draft(
                project_id=project_id,
                title="第一章 山雨",
                outline_node_id=outline_id,
                content="第一份先完成的草稿。",
                db=db,
            )
            with self.assertRaises(PendingChapterDraftConflict) as raised:
                store_chapter_draft(
                    project_id=project_id,
                    title="第一章 山雨",
                    outline_node_id=outline_id,
                    content="第二份迟到的草稿。",
                    db=db,
                )

            pending = (
                db.query(ChapterDraft)
                .filter(
                    ChapterDraft.project_id == project_id,
                    ChapterDraft.status == "pending",
                )
                .all()
            )
            self.assertEqual([str(row.id) for row in pending], [first_id])
            self.assertEqual(pending[0].content, "第一份先完成的草稿。")
            self.assertEqual(str(raised.exception.draft.id), first_id)
            self.assertEqual(
                db.query(ChapterDraft).filter(ChapterDraft.status == "superseded").count(),
                0,
            )
        finally:
            db.close()

    def test_late_generation_result_is_discarded_after_outline_is_saved(self):
        project_id = self.create_project("Late completion")
        outline_id = self.create_outline(project_id, "第一章 潮汐")
        generating_db = SessionLocal()
        saving_db = SessionLocal()
        try:
            # Start the generation-side read transaction before another request
            # saves the target outline as formal prose.
            self.assertIsNone(
                generating_db.query(Chapter)
                .filter(
                    Chapter.project_id == project_id,
                    Chapter.outline_node_id == outline_id,
                )
                .first()
            )
            chapter = Chapter(
                project_id=project_id,
                title="第一章 潮汐",
                outline_node_id=outline_id,
                content="已经由先完成的请求保存为正式正文。",
                word_count=17,
                sort_order=1000,
                cataloging_required=False,
            )
            saving_db.add(chapter)
            saving_db.commit()

            with self.assertRaises(ChapterDraftOutlineConflict) as raised:
                store_chapter_draft(
                    project_id=project_id,
                    title="第一章 潮汐",
                    outline_node_id=outline_id,
                    content="不应再变成待保存草稿的迟到结果。",
                    db=generating_db,
                )

            self.assertEqual(str(raised.exception.chapter.id), str(chapter.id))
            self.assertEqual(
                generating_db.query(ChapterDraft)
                .filter(
                    ChapterDraft.project_id == project_id,
                )
                .count(),
                0,
            )
        finally:
            saving_db.close()
            generating_db.close()

    def test_pending_restore_releases_legacy_draft_for_a_used_outline(self):
        project_id = self.create_project("Legacy stale draft")
        outline_id = self.create_outline(project_id, "第一章 归港")
        db = SessionLocal()
        try:
            db.add(
                Chapter(
                    project_id=project_id,
                    title="第一章 归港",
                    outline_node_id=outline_id,
                    content="船已经归港，正文也已正式保存。",
                    word_count=15,
                    sort_order=1000,
                    cataloging_required=False,
                )
            )
            draft = ChapterDraft(
                project_id=project_id,
                title="迟到草稿",
                outline_node_id=outline_id,
                content="这是升级前留下的无效待保存草稿。",
                status="pending",
            )
            db.add(draft)
            db.commit()
            draft_id = str(draft.id)
        finally:
            db.close()

        restored = self.client.get(f"{API_PREFIX}/projects/{project_id}/chapter-drafts/pending")

        self.assertEqual(restored.status_code, 200)
        self.assertIsNone(restored.json()["data"])
        db = SessionLocal()
        try:
            self.assertEqual(db.get(ChapterDraft, draft_id).status, "superseded")
        finally:
            db.close()

    def test_generated_draft_cannot_be_saved_through_chapter_update(self):
        project_id = self.create_project("PUT draft guard")
        outline_id = self.create_outline(project_id, "第一章 原文")
        created = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters",
            json={
                "title": "第一章 原文",
                "outline_node_id": outline_id,
                "content": "这是已经保存的第一章。",
                "cataloging_mode": "save_only",
            },
        )
        chapter_id = created.json()["data"]["id"]
        db = SessionLocal()
        try:
            draft = ChapterDraft(
                project_id=project_id,
                title="第二章",
                content="这是新生成的第二章草稿。",
                status="pending",
            )
            db.add(draft)
            db.commit()
            draft_id = draft.id
        finally:
            db.close()

        response = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter_id}",
            json={
                "title": "第二章",
                "content": "这是新生成的第二章草稿。",
                "draft_id": draft_id,
            },
        )

        self.assertEqual(response.status_code, 400)
        db = SessionLocal()
        try:
            self.assertEqual(db.get(Chapter, chapter_id).content, "这是已经保存的第一章。")
            self.assertEqual(db.get(ChapterDraft, draft_id).status, "pending")
        finally:
            db.close()

    def test_reviewed_revision_draft_updates_its_target_and_creates_ai_snapshot(self):
        project_id = self.create_project("Revision candidate")
        outline_id = self.create_outline(project_id, "第一章 原文")
        created = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters",
            json={
                "title": "第一章 原文",
                "outline_node_id": outline_id,
                "content": "正式正文 v1。",
                "cataloging_mode": "save_only",
            },
        )
        chapter_id = created.json()["data"]["id"]
        db = SessionLocal()
        try:
            draft = ChapterDraft(
                project_id=project_id,
                title="第一章 原文",
                outline_node_id=outline_id,
                draft_kind="revision",
                target_chapter_id=chapter_id,
                base_chapter_version=1,
                content="作者审阅后的修订正文 v2。",
                status="pending",
            )
            db.add(draft)
            db.commit()
            draft_id = str(draft.id)
        finally:
            db.close()

        pending = self.client.get(f"{API_PREFIX}/projects/{project_id}/chapter-drafts/pending")
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json()["data"]["draft_kind"], "revision")
        self.assertEqual(pending.json()["data"]["target_chapter_current_version"], 1)
        self.assertFalse(pending.json()["data"]["version_conflict"])

        saved = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter_id}",
            json={
                "title": "第一章 原文",
                "outline_node_id": outline_id,
                "content": "作者审阅后的修订正文 v2。",
                "draft_id": draft_id,
                "expected_version": 1,
                "cataloging_mode": "save_only",
            },
        )

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["data"]["id"], chapter_id)
        self.assertEqual(saved.json()["data"]["current_version"], 2)
        db = SessionLocal()
        try:
            self.assertEqual(db.get(Chapter, chapter_id).content, "作者审阅后的修订正文 v2。")
            stored_draft = db.get(ChapterDraft, draft_id)
            self.assertEqual(stored_draft.status, "saved")
            self.assertEqual(stored_draft.saved_chapter_id, chapter_id)
            latest = db.query(ChapterSnapshot).filter(
                ChapterSnapshot.chapter_id == chapter_id,
                ChapterSnapshot.version_number == 2,
            ).one()
            self.assertEqual(latest.trigger_type, "ai_revision")
        finally:
            db.close()

    def test_stale_revision_draft_returns_conflict_and_preserves_both_texts(self):
        project_id = self.create_project("Revision conflict")
        outline_id = self.create_outline(project_id, "第一章 原文")
        created = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters",
            json={
                "title": "第一章 原文",
                "outline_node_id": outline_id,
                "content": "正式正文 v1。",
                "cataloging_mode": "save_only",
            },
        )
        chapter_id = created.json()["data"]["id"]
        db = SessionLocal()
        try:
            draft = ChapterDraft(
                project_id=project_id,
                title="第一章 原文",
                outline_node_id=outline_id,
                draft_kind="revision",
                target_chapter_id=chapter_id,
                base_chapter_version=1,
                content="AI 基于 v1 的候选。",
                status="pending",
            )
            db.add(draft)
            db.commit()
            draft_id = str(draft.id)
        finally:
            db.close()

        manual = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter_id}",
            json={"content": "作者先保存了另一版 v2。", "expected_version": 1},
        )
        self.assertEqual(manual.status_code, 200)
        conflict = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter_id}",
            json={
                "title": "第一章 原文",
                "outline_node_id": outline_id,
                "content": "AI 基于 v1 的候选。",
                "draft_id": draft_id,
                "expected_version": 1,
            },
        )

        self.assertEqual(conflict.status_code, 409)
        self.assertIn("v1", conflict.json()["message"])
        db = SessionLocal()
        try:
            self.assertEqual(db.get(Chapter, chapter_id).content, "作者先保存了另一版 v2。")
            stored_draft = db.get(ChapterDraft, draft_id)
            self.assertEqual(stored_draft.status, "pending")
            self.assertEqual(stored_draft.content, "AI 基于 v1 的候选。")
        finally:
            db.close()

    def test_generated_draft_cannot_reuse_an_outline_with_formal_prose(self):
        project_id = self.create_project("Outline overwrite guard")
        outline_id = self.create_outline(project_id, "第一章 原文")
        created = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters",
            json={
                "title": "第一章 原文",
                "outline_node_id": outline_id,
                "content": "这是已经保存的第一章。",
                "cataloging_mode": "save_only",
            },
        )
        chapter_id = created.json()["data"]["id"]
        db = SessionLocal()
        try:
            draft = ChapterDraft(
                project_id=project_id,
                title="错误草稿",
                outline_node_id=outline_id,
                content="不应覆盖第一章。",
                status="pending",
            )
            db.add(draft)
            db.commit()
            draft_id = draft.id
        finally:
            db.close()

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters",
            json={
                "title": "错误草稿",
                "outline_node_id": outline_id,
                "content": "不应覆盖第一章。",
                "draft_id": draft_id,
                "cataloging_mode": "save_only",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("迟到草稿已释放", response.json()["message"])
        db = SessionLocal()
        try:
            self.assertEqual(db.query(Chapter).count(), 1)
            self.assertEqual(db.get(Chapter, chapter_id).content, "这是已经保存的第一章。")
            self.assertEqual(db.get(ChapterDraft, draft_id).status, "superseded")
        finally:
            db.close()

        restored = self.client.get(f"{API_PREFIX}/projects/{project_id}/chapter-drafts/pending")
        self.assertEqual(restored.status_code, 200)
        self.assertIsNone(restored.json()["data"])

    @patch("app.routers.chapters.preview_de_ai_revision", new_callable=AsyncMock)
    @patch("app.routers.chapters.preview_chapter_quality", new_callable=AsyncMock)
    def test_draft_review_actions_use_current_editor_content(self, mock_quality, mock_de_ai):
        project_id = self.create_project("Draft review")
        db = SessionLocal()
        try:
            draft = ChapterDraft(
                project_id=project_id,
                title="未保存",
                content="数据库中的初稿内容。",
                status="pending",
            )
            db.add(draft)
            db.commit()
            draft_id = draft.id
        finally:
            db.close()
        editor_content = "这是作者在前端刚刚修改、但尚未保存的完整正文。"
        mock_quality.return_value = {"total_score": 70}
        mock_de_ai.return_value = {"rewritten": editor_content, "warnings": []}

        quality = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapter-drafts/{draft_id}/quality-score-preview",
            json={"content": editor_content, "title": "当前标题", "model": "openai:gpt-test"},
        )
        de_ai = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapter-drafts/{draft_id}/de-ai-preview",
            json={"content": editor_content, "model": "openai:gpt-test"},
        )

        self.assertEqual(quality.status_code, 200)
        self.assertEqual(de_ai.status_code, 200)
        self.assertEqual(mock_quality.await_args.kwargs["content"], editor_content)
        self.assertIsNone(mock_quality.await_args.args[2])
        self.assertEqual(mock_de_ai.await_args.kwargs["content"], editor_content)
        self.assertIsNone(mock_de_ai.await_args.args[2])

    @patch("app.routers.chapters.create_and_queue_cataloging_job")
    def test_author_save_and_catalog_starts_one_job(self, mock_launch):
        project_id = self.create_project("Explicit cataloging")
        draft_id = "draft-explicit"
        db = SessionLocal()
        try:
            db.add(
                ChapterDraft(
                    id=draft_id,
                    project_id=project_id,
                    title="第一章",
                    content="这是一段等待作者确认的章节正文。",
                    status="pending",
                )
            )
            db.commit()
        finally:
            db.close()
        job = MagicMock(id="job-1", operation_id="operation-1")
        mock_launch.return_value = (
            job,
            {
                "started": True,
                "job_id": "job-1",
                "operation_id": "operation-1",
                "status": "running",
            },
        )

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters",
            json={
                "title": "第一章",
                "content": "这是一段等待作者确认的章节正文。",
                "draft_id": draft_id,
                "cataloging_mode": "save_and_catalog",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["cataloging_job"]["started"])
        mock_launch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
