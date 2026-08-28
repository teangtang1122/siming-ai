from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from fastapi.testclient import TestClient

from app.database.models import Chapter, ChapterDraft, Character
from app.database.session import Base, SessionLocal, engine
from app.main import app
from app.routers.ai_writer import _execute_workspace_action
from app.services.workspace.generated_drafts import (
    ChapterDraftOutlineConflict,
    PendingChapterDraftConflict,
    store_chapter_draft,
)
from app.services.tool_category_state import replace_tool_categories


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
        try:
            os.remove("test_novel_agent.db")
        except OSError:
            pass

    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

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
        mock_execute.return_value = {
            "tool": "chapter_writer",
            "status": "ok",
            "detail": "章节草稿已生成，尚未保存",
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
            },
        }
        mock_stream.side_effect = [
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call-categories",
                    "name": "set_tool_categories",
                    "arguments_delta": json.dumps({
                        "enabled_categories": ["story_knowledge", "writing_context"],
                    }),
                },
                {"type": "done", "finish_reason": "tool_calls", "usage": None},
            ),
            async_dict_chunks(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call-2",
                    "name": "create_character",
                    "arguments_delta": json.dumps({"name": "不应创建"}, ensure_ascii=False),
                },
                {
                    "type": "tool_call_delta",
                    "index": 1,
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
        db = SessionLocal()
        try:
            self.assertEqual(db.query(Chapter).count(), 0)
            self.assertEqual(db.query(Character).count(), 0)
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
            db.add(Chapter(
                project_id=project_id,
                title="第一卷 错误旧标题",
                outline_node_id=first_outline,
                content="第一章已经保存并完成建档。",
                word_count=13,
                sort_order=1000,
                cataloging_required=False,
            ))
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
                    "arguments_delta": json.dumps({
                        "outline_node_id": second_outline,
                        "requirements": "把故事接到夜雨这一章",
                    }, ensure_ascii=False),
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
            result = asyncio.run(_execute_workspace_action(
                db,
                project_id,
                {"tool": "chapter_writer", "arguments": {"outline_node_id": volume_id}},
            ))
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
            result = asyncio.run(_execute_workspace_action(
                db,
                project_id,
                {"tool": "chapter_writer", "arguments": {"outline_node_id": outline_id}},
            ))
            db.refresh(chapter)
            self.assertEqual(chapter.content, "第一章正式正文，不得覆盖。")
        finally:
            db.close()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["data"]["existing_chapter_id"], chapter_id)
        self.assertIn("不能覆盖", result["detail"])

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

        def cli_stream(**kwargs):
            nonlocal stream_calls
            stream_calls += 1
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
        runtime = mock_stream.call_args_list[0].kwargs["extra_body"]
        self.assertEqual(runtime["local_cli_mcp_permission_pack"], "project_management")
        self.assertEqual(runtime["local_cli_terminal_draft_project_id"], project_id)
        self.assertEqual(runtime["local_cli_terminal_draft_excluded_ids"], [])
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

        def cli_stream(**kwargs):
            nonlocal stream_calls
            stream_calls += 1
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
        runtime = mock_stream.call_args_list[0].kwargs["extra_body"]
        self.assertEqual(runtime["local_cli_terminal_draft_project_id"], project_id)
        self.assertEqual(runtime["local_cli_terminal_draft_excluded_ids"], [])

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
        self.assertIn("没有调用临时 MCP 中唯一开放的 set_tool_categories", response.text)
        self.assertNotIn('"type": "complete"', response.text)

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
        self.assertIn("没有调用本步骤唯一开放的 set_tool_categories", response.text)
        self.assertNotIn('"type": "complete"', response.text)

    @patch("app.routers.ai_writer.LLMGateway.supports_tool_calling", return_value=True)
    @patch("app.routers.ai_writer.LLMGateway.stream_chat_completion_with_tools")
    def test_reasoning_deltas_are_streamed_immediately_and_persisted_in_completion(
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
        self.assertEqual([event["delta"] for event in reasoning_events], ["先核对", "作品资料"])
        self.assertLess(events.index(reasoning_events[0]), next(
            index for index, event in enumerate(events) if event.get("type") == "complete"
        ))
        complete = next(event for event in events if event.get("type") == "complete")
        self.assertEqual(complete["data"]["reply"], "资料检查完成。")
        self.assertEqual(complete["data"]["reasoning_content"], "先核对作品资料")

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

            pending = db.query(ChapterDraft).filter(
                ChapterDraft.project_id == project_id,
                ChapterDraft.status == "pending",
            ).all()
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
                generating_db.query(Chapter).filter(
                    Chapter.project_id == project_id,
                    Chapter.outline_node_id == outline_id,
                ).first()
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
                generating_db.query(ChapterDraft).filter(
                    ChapterDraft.project_id == project_id,
                ).count(),
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
            db.add(Chapter(
                project_id=project_id,
                title="第一章 归港",
                outline_node_id=outline_id,
                content="船已经归港，正文也已正式保存。",
                word_count=15,
                sort_order=1000,
                cataloging_required=False,
            ))
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

        self.assertEqual(response.status_code, 422)
        db = SessionLocal()
        try:
            self.assertEqual(db.get(Chapter, chapter_id).content, "这是已经保存的第一章。")
            self.assertEqual(db.get(ChapterDraft, draft_id).status, "pending")
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
            db.add(ChapterDraft(
                id=draft_id,
                project_id=project_id,
                title="第一章",
                content="这是一段等待作者确认的章节正文。",
                status="pending",
            ))
            db.commit()
        finally:
            db.close()
        job = MagicMock(id="job-1", operation_id="operation-1")
        mock_launch.return_value = (job, {
            "started": True,
            "job_id": "job-1",
            "operation_id": "operation-1",
            "status": "running",
        })

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
