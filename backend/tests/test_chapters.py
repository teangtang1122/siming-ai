"""
Test cases for chapter management and version control.

Covers:
  - Chapter CRUD and independently ordered chapter list
  - Save-time snapshot creation
  - Snapshot history and restore
  - Line-based diff between snapshots
"""

import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from fastapi.testclient import TestClient

from app.database.models import (
    Chapter,
    ChapterGovernanceReview,
    ChapterQualityMetric,
    ChapterSnapshot,
    Foreshadowing,
    OutlineNode,
    Project,
)
from app.database.session import Base, SessionLocal, engine
from app.main import app
from app.services.narrative_governance import (
    record_chapter_governance_review,
    upsert_foreshadowing,
)

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
            db.query(ChapterGovernanceReview).delete()
            db.query(Foreshadowing).delete()
            db.query(ChapterQualityMetric).delete()
            db.query(ChapterSnapshot).delete()
            db.query(Chapter).delete()
            db.query(OutlineNode).delete()
            db.query(Project).delete()
            db.commit()
        finally:
            db.close()

    def create_project(self, title: str = "Chapter Test Novel") -> str:
        response = self.client.post(f"{API_PREFIX}/projects", json={"title": title})
        self.assertEqual(response.status_code, 200, response.text)
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
        self.assertEqual(chapter["sort_order"], 1000)
        self.assertEqual(chapter["snapshot_count"], 1)

        response = self.client.get(f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}")
        self.assertEqual(response.status_code, 200)
        detail = response.json()["data"]
        self.assertEqual(detail["content"], "林澈推开城门。")

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_de_ai_preview_supports_local_cli_without_mutating_chapter(self, mock_chat):
        project_id = self.create_project()
        source = "他站在门边，心中不由得涌起一阵复杂的情绪。值得注意的是，这一切都说明命运已经改变。"
        chapter = self.create_chapter(project_id, content=source)
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        before = self.client.get(detail_url).json()["data"]
        mock_chat.side_effect = [
            {
                "content": (
                    "叙事约束：第三人称。\n"
                    "01 [硬] 他站在门边；复杂情绪涌起。\n"
                    "02 [硬] 眼前一切说明命运已经改变。"
                ),
                "model": "test-model",
                "request_meta": {"provider": "opencode_cli", "model": "test-model"},
            },
            {
                "content": (
                    "```text\n他站在门边，心里的情绪搅在一起，很难说清。"
                    "眼前这一切只指向一件事——他的命运已经改变。\n```"
                ),
                "model": "test-model",
                "request_meta": {"provider": "opencode_cli", "model": "test-model"},
            },
            {
                "content": '{"passed":true,"issues":[]}',
                "model": "test-model",
                "request_meta": {"provider": "opencode_cli", "model": "test-model"},
            },
            {
                "content": '{"passed":true,"issues":[]}',
                "model": "test-model",
                "request_meta": {"provider": "opencode_cli", "model": "test-model"},
            },
        ]

        response = self.client.post(
            f"{detail_url}/de-ai-preview",
            json={"content": source, "model": "opencode_cli:test-model"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()["data"]
        self.assertFalse(preview["mutated"])
        self.assertEqual(preview["provider"], "opencode_cli")
        self.assertNotIn("```", preview["rewritten"])
        self.assertTrue(preview["fidelity_audit"]["passed"])
        self.assertTrue(preview["style_audit"]["passed"])
        call = mock_chat.await_args_list[1].kwargs
        self.assertEqual(call["model"], "opencode_cli:test-model")
        self.assertTrue(call["extra_body"]["local_cli_isolated"])
        self.assertIn("连续片段", call["messages"][0]["content"])
        audit_call = mock_chat.await_args_list[2].kwargs
        self.assertEqual(audit_call["temperature"], 0)
        self.assertIn("故事保真审计", audit_call["messages"][1]["content"])
        style_audit_call = mock_chat.await_args_list[3].kwargs
        self.assertIn("表达结构审计", style_audit_call["messages"][0]["content"])

        after = self.client.get(detail_url).json()["data"]
        self.assertEqual(after["content"], before["content"])
        self.assertEqual(after["current_version"], before["current_version"])
        self.assertEqual(after["snapshot_count"], before["snapshot_count"])

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_de_ai_preview_supports_api_provider_without_mutating_chapter(self, mock_chat):
        project_id = self.create_project()
        source = (
            "他站在门边，心中不由得涌起一阵复杂的情绪。"
            "值得注意的是，这一切都说明命运已经改变。"
        )
        chapter = self.create_chapter(project_id, content=source)
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        before = self.client.get(detail_url).json()["data"]
        common_meta = {
            "model": "deepseek-chat",
            "request_meta": {"provider": "deepseek", "model": "deepseek-chat"},
        }
        mock_chat.side_effect = [
            {
                "content": (
                    "叙事约束：第三人称。\n"
                    "01 [硬] 他站在门边；复杂情绪涌起。\n"
                    "02 [硬] 眼前一切说明命运已经改变。"
                ),
                **common_meta,
            },
            {
                "content": (
                    "他站在门边，心里的情绪搅在一起，很难说清。"
                    "眼前这一切只指向一件事——他的命运已经改变。"
                ),
                **common_meta,
            },
            {"content": '{"passed":true,"issues":[]}', **common_meta},
            {"content": '{"passed":true,"issues":[]}', **common_meta},
        ]

        response = self.client.post(
            f"{detail_url}/de-ai-preview",
            json={"content": source, "model": "deepseek:deepseek-chat"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()["data"]
        self.assertEqual(preview["provider"], "deepseek")
        self.assertEqual(preview["model"], "deepseek-chat")
        self.assertFalse(preview["mutated"])
        self.assertFalse(preview["persisted"])
        self.assertFalse(preview["auto_adopted"])
        rewrite_call = mock_chat.await_args_list[1].kwargs
        self.assertEqual(rewrite_call["model"], "deepseek:deepseek-chat")
        self.assertNotIn("local_cli_isolated", rewrite_call["extra_body"])

        after = self.client.get(detail_url).json()["data"]
        self.assertEqual(after["content"], before["content"])
        self.assertEqual(after["current_version"], before["current_version"])
        self.assertEqual(after["snapshot_count"], before["snapshot_count"])

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_de_ai_follow_up_round_keeps_initial_original_as_fidelity_authority(
        self,
        mock_chat,
    ):
        project_id = self.create_project()
        original = (
            "陈禾说，三天内若没有消息，周砚就把账页交到城南邮局三号信箱。"
            "周砚复述了一遍，把账页收好，留在原地等消息。"
        )
        previous_candidate = (
            "陈禾把期限说死：三天里一直等不到消息，账页便由周砚送往"
            "城南邮局三号信箱。周砚重复一遍条件，收好账页，没有离开。"
        )
        next_candidate = (
            "“三天。”陈禾说，“一直没有消息，就把账页投进城南邮局三号信箱。”"
            "周砚照着复述，随后将账页收起，仍在原地等候。"
        )
        chapter = self.create_chapter(project_id, content=original)
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        common_meta = {
            "model": "test-model",
            "request_meta": {"provider": "deepseek", "model": "test-model"},
        }
        mock_chat.side_effect = [
            {
                "content": (
                    "叙事约束：第三人称。\n"
                    "01 [硬] 陈禾规定三天无消息时由周砚递交账页。\n"
                    "02 [硬] 地点为城南邮局三号信箱。\n"
                    "03 [硬] 周砚复述、收好账页并留在原地。"
                ),
                **common_meta,
            },
            {"content": next_candidate, **common_meta},
            {"content": '{"passed":true,"issues":[]}', **common_meta},
            {"content": '{"passed":true,"issues":[]}', **common_meta},
        ]

        response = self.client.post(
            f"{detail_url}/de-ai-preview",
            json={
                "content": previous_candidate,
                "original_content": original,
                "revision_round": 2,
                "model": "deepseek:test-model",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()["data"]
        self.assertEqual(preview["original"], original)
        self.assertEqual(preview["input"], previous_candidate)
        self.assertEqual(preview["rewritten"], next_candidate)
        self.assertEqual(preview["revision_round"], 2)
        self.assertEqual(preview["max_revision_rounds"], 3)
        self.assertTrue(preview["can_continue"])
        fidelity_prompt = mock_chat.await_args_list[2].kwargs["messages"][1]["content"]
        self.assertIn(original, fidelity_prompt)
        self.assertNotIn(previous_candidate, fidelity_prompt)
        rewrite_prompt = mock_chat.await_args_list[1].kwargs["messages"][1]["content"]
        required_literals = rewrite_prompt.split(
            "【本段必须原字出现的源文标记】",
            1,
        )[1].split("【本段账本拍点】", 1)[0]
        self.assertNotIn("候选稿新增标记", required_literals)

        after = self.client.get(detail_url).json()["data"]
        self.assertEqual(after["content"], original)
        self.assertEqual(after["current_version"], 1)
        self.assertEqual(after["snapshot_count"], 1)

    def test_de_ai_follow_up_round_requires_initial_original(self):
        project_id = self.create_project()
        source = "这是一段足够长的候选正文，用来验证连续处理必须携带最初原文，避免故事事实逐轮漂移。"
        chapter = self.create_chapter(project_id, content=source)

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}/de-ai-preview",
            json={
                "content": source,
                "revision_round": 2,
                "model": "deepseek:test-model",
            },
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("必须同时提交最初原文", response.text)

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_de_ai_preview_retries_two_empty_local_cli_scenes(self, mock_chat):
        project_id = self.create_project()
        source = "他站在门边，心中不由得涌起一阵复杂的情绪。值得注意的是，这一切都说明命运已经改变。"
        chapter = self.create_chapter(project_id, content=source)
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        common_meta = {
            "model": "test-model",
            "request_meta": {"provider": "opencode_cli", "model": "test-model"},
        }
        mock_chat.side_effect = [
            {
                "content": (
                    "叙事约束：第三人称。\n"
                    "01 [硬] 他站在门边；复杂情绪涌起。\n"
                    "02 [硬] 眼前一切说明命运已经改变。"
                ),
                **common_meta,
            },
            {"content": "", **common_meta},
            {"content": "   ", **common_meta},
            {
                "content": (
                    "他站在门边，心里的情绪搅在一起，很难说清。"
                    "眼前这一切只指向一件事——他的命运已经改变。"
                ),
                **common_meta,
            },
            {"content": '{"passed":true,"issues":[]}', **common_meta},
            {"content": '{"passed":true,"issues":[]}', **common_meta},
        ]

        response = self.client.post(
            f"{detail_url}/de-ai-preview",
            json={"content": source, "model": "opencode_cli:test-model"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(mock_chat.await_count, 6)
        self.assertIn("命运已经改变", response.json()["data"]["rewritten"])
        second_retry = mock_chat.await_args_list[3].kwargs
        self.assertIn("上一稿未通过故事保真审计", second_retry["messages"][1]["content"])

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_de_ai_preview_starts_local_length_retry_window_after_first_candidate(
        self,
        mock_chat,
    ):
        project_id = self.create_project()
        source = ("周砚沿旧路走向仓库门口，陈禾在那里等他。" * 12)[:220]
        chapter = self.create_chapter(project_id, content=source)
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        short_candidate = "周砚沿旧路走向仓库门口" * 4
        target_candidate = ("周砚沿旧路走向仓库门口" * 20)[:110]
        rewrite_calls = 0
        common_meta = {
            "model": "test-model",
            "request_meta": {"provider": "codex_cli", "model": "test-model"},
        }

        async def respond(*, messages, **_kwargs):
            nonlocal rewrite_calls
            system = str(messages[0].get("content") or "")
            if "事实记录员" in system:
                return {
                    "content": (
                        "叙事约束：第三人称。\n"
                        "01 [硬] 周砚沿旧路走向仓库门口。\n"
                        "02 [硬] 陈禾在仓库等待周砚。\n"
                        "03 [硬] 两人在仓库门口碰面后继续处理原有事务。"
                    ),
                    **common_meta,
                }
            if "重写编辑" in system:
                rewrite_calls += 1
                if rewrite_calls == 1:
                    # Longer than the patched retry window.  The old global
                    # deadline expired here and incorrectly hid the retry.
                    await asyncio.sleep(0.1)
                    return {"content": short_candidate, **common_meta}
                return {"content": target_candidate, **common_meta}
            if "校对员" in system or "审计员" in system:
                return {"content": '{"passed":true,"issues":[]}', **common_meta}
            raise AssertionError(f"unexpected prompt: {system}")

        mock_chat.side_effect = respond
        chunk_prompt = (
            "本段目标为100至120个可见字符。\n"
            "【本段账本拍点】\n"
            "01 [硬] 周砚沿旧路走向仓库门口。\n"
            "02 [硬] 陈禾在仓库等待周砚。"
        )
        with (
            patch(
                "app.services.chapter_revision.build_de_ai_chunked_rewrite_prompts",
                return_value=[chunk_prompt],
            ),
            patch(
                "app.services.chapter_revision."
                "_DE_AI_LOCAL_CLI_OPTIONAL_LENGTH_RETRY_SECONDS",
                0.05,
            ),
        ):
            response = self.client.post(
                f"{detail_url}/de-ai-preview",
                json={"content": source, "model": "codex_cli:test-model"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(rewrite_calls, 2)
        self.assertEqual(response.json()["data"]["rewritten"], target_candidate)

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_de_ai_preview_regenerates_a_scene_rejected_by_fidelity_audit(self, mock_chat):
        project_id = self.create_project()
        source = (
            "陈禾说，三天内若没有消息，周砚就把账页交到城南邮局三号信箱。"
            "周砚把这句话重复一遍，随后收好账页，等陈禾的消息。"
        )
        chapter = self.create_chapter(project_id, content=source)
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        common_meta = {
            "model": "test-model",
            "request_meta": {"provider": "codex_cli", "model": "test-model"},
        }
        mock_chat.side_effect = [
            {
                "content": (
                    "叙事约束：第三人称。\n"
                    "01 [硬] 陈禾：三天内没有消息→周砚把账页交到城南邮局三号信箱。\n"
                    "02 [硬] 周砚复述要求→收好账页→等待陈禾消息。"
                ),
                **common_meta,
            },
            {
                "content": (
                    "陈禾交代，三天内只要收到消息，周砚就得把账页送到城南邮局三号信箱。"
                    "周砚照着复述一遍，收好账页，留下来等陈禾的消息。"
                ),
                **common_meta,
            },
            {
                "content": (
                    '{"passed":false,"issues":[{"chunk":1,'
                    '"kind":"contradiction","detail":"把没有消息才投递写成收到消息就投递"}]}'
                ),
                **common_meta,
            },
            {
                "content": (
                    "陈禾交代：三天内若一直没有消息，周砚就把账页送到城南邮局三号信箱。"
                    "周砚原样复述，收好账页，等着陈禾的消息。"
                ),
                **common_meta,
            },
            {
                "content": '{"passed":true,"issues":[]}',
                **common_meta,
            },
            {
                "content": '{"passed":true,"issues":[]}',
                **common_meta,
            },
        ]

        response = self.client.post(
            f"{detail_url}/de-ai-preview",
            json={"content": source, "model": "codex_cli:test-model"},
        )

        self.assertEqual(response.status_code, 200)
        preview = response.json()["data"]
        self.assertIn("若一直没有消息", preview["rewritten"])
        self.assertNotIn("只要收到消息", preview["rewritten"])
        self.assertTrue(preview["fidelity_audit"]["passed"])
        self.assertTrue(preview["style_audit"]["passed"])
        self.assertEqual(mock_chat.await_count, 6)
        repair_call = mock_chat.await_args_list[3].kwargs
        self.assertIn("【待校正候选】", repair_call["messages"][1]["content"])
        self.assertIn("只修正上面列出的事实错误", repair_call["messages"][1]["content"])
        self.assertIn("其余已正确的叙述", repair_call["messages"][1]["content"])

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_de_ai_preview_keeps_fact_guard_through_style_repair(self, mock_chat):
        project_id = self.create_project()
        source = (
            "陈禾说，三天内若没有消息，周砚就把账页交到城南邮局三号信箱。"
            "周砚把这句话重复一遍，随后收好账页，等陈禾的消息。"
        )
        chapter = self.create_chapter(project_id, content=source)
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        common_meta = {
            "model": "test-model",
            "request_meta": {"provider": "codex_cli", "model": "test-model"},
        }
        bad_condition = (
            "陈禾交代，三天内只要收到消息，周砚就把账页送到城南邮局三号信箱。"
            "周砚复述一遍，把账页收好，留下来等陈禾的消息。"
        )
        corrected = (
            "陈禾交代，三天里若一直没有消息，周砚就把账页送到城南邮局三号信箱。"
            "周砚照着复述一遍，收好账页，等陈禾的消息。"
        )
        fact_detail = "把没有消息才投递写成收到消息就投递"
        mock_chat.side_effect = [
            {
                "content": (
                    "叙事约束：第三人称。\n"
                    "01 [硬] 三天内无消息→周砚把账页交到城南邮局三号信箱。\n"
                    "02 [硬] 周砚复述→收好账页→等待陈禾消息。"
                ),
                **common_meta,
            },
            {"content": bad_condition, **common_meta},
            {
                "content": (
                    '{"passed":false,"issues":[{"chunk":1,'
                    f'"kind":"contradiction","detail":"{fact_detail}"}}]}}'
                ),
                **common_meta,
            },
            {"content": corrected, **common_meta},
            {"content": '{"passed":true,"issues":[]}', **common_meta},
            {
                "content": (
                    '{"passed":false,"issues":[{"chunk":1,"kind":"staged",'
                    '"detail":"条件、复述和等待被铺成完整说明链"}]}'
                ),
                **common_meta,
            },
            {"content": bad_condition, **common_meta},
            {
                "content": (
                    '{"passed":false,"issues":[{"chunk":1,'
                    f'"kind":"contradiction","detail":"{fact_detail}"}}]}}'
                ),
                **common_meta,
            },
            {"content": corrected, **common_meta},
            {"content": '{"passed":true,"issues":[]}', **common_meta},
            {"content": '{"passed":true,"issues":[]}', **common_meta},
        ]

        response = self.client.post(
            f"{detail_url}/de-ai-preview",
            json={"content": source, "model": "codex_cli:test-model"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()["data"]
        self.assertIn("若一直没有消息", preview["rewritten"])
        self.assertNotIn("只要收到消息", preview["rewritten"])
        self.assertEqual(mock_chat.await_count, 11)
        first_reaudit = mock_chat.await_args_list[4].kwargs["messages"][1]["content"]
        self.assertIn("历史问题仅作复核线索", first_reaudit)
        self.assertIn("只有当前候选仍存在同一事实错误时才报告", first_reaudit)
        self.assertIn(fact_detail, first_reaudit)
        style_repair = mock_chat.await_args_list[6].kwargs["messages"][1]["content"]
        self.assertNotIn(fact_detail, style_repair)
        regressed_reaudit = mock_chat.await_args_list[7].kwargs["messages"][1]["content"]
        self.assertIn("历史问题仅作复核线索", regressed_reaudit)
        self.assertIn(fact_detail, regressed_reaudit)

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_de_ai_preview_repairs_structural_recap_and_reaudits_facts(self, mock_chat):
        project_id = self.create_project()
        source = (
            "周砚在7月12日晚抵达A17仓库，把3封信交给陈禾。"
            "回家后，他从车后座捡到一枚A17-07储物柜钥匙。"
        )
        chapter = self.create_chapter(project_id, content=source)
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        common_meta = {
            "model": "test-model",
            "request_meta": {"provider": "codex_cli", "model": "test-model"},
        }
        mock_chat.side_effect = [
            {
                "content": (
                    "叙事约束：第三人称。\n"
                    "01 [硬] 7月12日晚；周砚；3封信→A17仓库→陈禾。\n"
                    "02 [硬] 回家后；车后座→A17-07储物柜钥匙。"
                ),
                **common_meta,
            },
            {
                "content": (
                    "7月12日晚，周砚到了A17仓库，把3封信交给陈禾。"
                    "回家后，他在车后座发现A17-07储物柜钥匙。"
                    "从仓库到钥匙，这些线索终于都对上了。"
                ),
                **common_meta,
            },
            {"content": '{"passed":true,"issues":[]}', **common_meta},
            {
                "content": (
                    '{"passed":false,"issues":[{"chunk":1,"kind":"recap",'
                    '"detail":"结尾把仓库和钥匙成组复盘并替读者下结论"}]}'
                ),
                **common_meta,
            },
            {
                "content": (
                    "7月12日晚，周砚把3封信送进A17仓库，交到陈禾手里。"
                    "回家以后，他拿A17-07钥匙试了试车后座的锁。"
                ),
                **common_meta,
            },
            {
                "content": (
                    '{"passed":false,"issues":[{"chunk":1,"kind":"added",'
                    '"detail":"新增了拿钥匙试锁的动作"}]}'
                ),
                **common_meta,
            },
            {
                "content": (
                    "7月12日晚，周砚把3封信送进A17仓库，交到陈禾手里。"
                    "回家以后，他伸手去拿车后座的外套，指尖先碰到一枚"
                    "A17-07储物柜钥匙。"
                ),
                **common_meta,
            },
            {"content": '{"passed":true,"issues":[]}', **common_meta},
            {"content": '{"passed":true,"issues":[]}', **common_meta},
        ]

        response = self.client.post(
            f"{detail_url}/de-ai-preview",
            json={"content": source, "model": "codex_cli:test-model"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()["data"]
        self.assertNotIn("线索终于都对上", preview["rewritten"])
        self.assertNotIn("试了试", preview["rewritten"])
        self.assertTrue(preview["fidelity_audit"]["passed"])
        self.assertTrue(preview["style_audit"]["passed"])
        self.assertEqual(mock_chat.await_count, 9)
        repair_call = mock_chat.await_args_list[4].kwargs
        self.assertIn("表达结构重生", repair_call["messages"][1]["content"])
        fact_repair_call = mock_chat.await_args_list[6].kwargs
        self.assertIn("只修正上面列出的事实错误", fact_repair_call["messages"][1]["content"])
        self.assertIn("新增了拿钥匙试锁的动作", fact_repair_call["messages"][1]["content"])
        self.assertNotIn("本次命中 recap", fact_repair_call["messages"][1]["content"])

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_de_ai_preview_returns_candidate_when_audit_remains_rejected(self, mock_chat):
        project_id = self.create_project()
        source = (
            "陈禾说，三天内若没有消息，周砚就把账页交到城南邮局三号信箱。"
            "周砚把这句话重复一遍，随后收好账页，等陈禾的消息。"
        )
        candidate = (
            "陈禾把话说清：三天里一直收不到消息，周砚就得将账页送进"
            "城南邮局三号信箱。周砚复述了一次，把账页收好，留下等陈禾的回信。"
        )
        chapter = self.create_chapter(project_id, content=source)
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        before = self.client.get(detail_url).json()["data"]
        common_meta = {
            "model": "test-model",
            "request_meta": {"provider": "codex_cli", "model": "test-model"},
        }

        def respond(*, messages, **_kwargs):
            system = str(messages[0].get("content") or "")
            if "事实记录员" in system:
                return {
                    "content": (
                        "叙事约束：第三人称。\n"
                        "01 [硬] 三天内无消息→周砚把账页交到城南邮局三号信箱。\n"
                        "02 [硬] 周砚复述→收好账页→等待陈禾消息。"
                    ),
                    **common_meta,
                }
            if "事实校对员" in system:
                return {
                    "content": (
                        '{"passed":false,"issues":[{"chunk":1,'
                        '"kind":"contradiction","detail":"条件关系仍需作者确认"}]}'
                    ),
                    **common_meta,
                }
            if "表达结构审计员" in system:
                return {"content": '{"passed":true,"issues":[]}', **common_meta}
            return {"content": candidate, **common_meta}

        mock_chat.side_effect = respond
        response = self.client.post(
            f"{detail_url}/de-ai-preview",
            json={"content": source, "model": "codex_cli:test-model"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()["data"]
        self.assertEqual(preview["original"], source)
        self.assertEqual(preview["rewritten"], candidate)
        self.assertFalse(preview["audit_passed"])
        self.assertEqual(preview["candidate_status"], "review_with_warnings")
        self.assertFalse(preview["auto_adopted"])
        self.assertFalse(preview["persisted"])
        self.assertTrue(preview["review_required"])
        self.assertTrue(any(
            item["source"] == "fidelity_audit"
            and "条件关系仍需作者确认" in item["detail"]
            for item in preview["warnings"]
        ))
        self.assertIn("审核提醒", response.json()["message"])

        after = self.client.get(detail_url).json()["data"]
        self.assertEqual(after["content"], before["content"])
        self.assertEqual(after["current_version"], before["current_version"])
        self.assertEqual(after["snapshot_count"], before["snapshot_count"])

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_de_ai_preview_returns_candidate_when_optional_review_times_out(
        self,
        mock_chat,
    ):
        project_id = self.create_project()
        source = (
            "陈禾说，三天内若没有消息，周砚就把账页交到城南邮局三号信箱。"
            "周砚复述一遍，收好账页，留在原地等消息。"
        )
        candidate = (
            "陈禾把期限说死：三天里一直等不到消息，账页便由周砚送往"
            "城南邮局三号信箱。周砚重复一遍条件，收好账页，没有离开。"
        )
        chapter = self.create_chapter(project_id, content=source)
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        before = self.client.get(detail_url).json()["data"]
        common_meta = {
            "model": "test-model",
            "request_meta": {"provider": "deepseek", "model": "test-model"},
        }

        async def respond(*, messages, **_kwargs):
            system = str(messages[0].get("content") or "")
            if "事实记录员" in system:
                return {
                    "content": (
                        "叙事约束：第三人称。\n"
                        "01 [硬] 三天无消息→周砚把账页交到城南邮局三号信箱。\n"
                        "02 [硬] 周砚复述→收好账页→留在原地。"
                    ),
                    **common_meta,
                }
            if "事实校对员" in system:
                await asyncio.sleep(10)
            if "表达结构审计员" in system:
                await asyncio.sleep(10)
            return {"content": candidate, **common_meta}

        mock_chat.side_effect = respond
        with patch(
            "app.services.chapter_revision._DE_AI_API_REVIEW_TIMEOUT_SECONDS",
            0.01,
        ):
            response = self.client.post(
                f"{detail_url}/de-ai-preview",
                json={"content": source, "model": "deepseek:test-model"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()["data"]
        self.assertEqual(preview["original"], source)
        self.assertEqual(preview["rewritten"], candidate)
        self.assertFalse(preview["audit_passed"])
        self.assertEqual(preview["candidate_status"], "review_with_warnings")
        self.assertFalse(preview["auto_adopted"])
        self.assertTrue(any(
            item["code"] == "audit_unavailable"
            and "审核达到本轮时限" in item["detail"]
            for item in preview["warnings"]
        ))

        after = self.client.get(detail_url).json()["data"]
        self.assertEqual(after["content"], before["content"])
        self.assertEqual(after["current_version"], before["current_version"])
        self.assertEqual(after["snapshot_count"], before["snapshot_count"])

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_long_de_ai_preview_uses_one_macro_call_per_chunk_before_source_audit(
        self,
        mock_chat,
    ):
        project_id = self.create_project()
        source = (
            "周砚陈禾旧信账页北门灯光仓库等待交接"
            * 200
        )[:2100]
        chapter = self.create_chapter(project_id, content=source)
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        chunk_prompts = [
            (
                "本段目标为900至1050个可见字符。\n"
                "【本段账本拍点】\n"
                "01 [硬] 周砚把旧信交给陈禾。\n"
                "02 [硬] 两人在仓库内遭遇威胁。"
            ),
            (
                "本段目标为900至1050个可见字符。\n"
                "【本段账本拍点】\n"
                "01 [硬] 周砚与陈禾从南门离开。\n"
                "02 [硬] 周砚回家后发现异常。"
            ),
        ]
        candidate_chunk = (
            "周砚陈禾南门晚风脚步纸页钥匙屋门窗影"
            * 200
        )[:1020]
        common_meta = {
            "model": "deepseek-chat",
            "request_meta": {"provider": "deepseek", "model": "deepseek-chat"},
        }

        async def respond(*, messages, **_kwargs):
            system = str(messages[0].get("content") or "")
            if "事实账本压缩员" in system:
                return {
                    "content": (
                        "01 [硬] 周砚与陈禾面对仓库内的威胁。\n"
                        "02 [硬] 两人作出离开的选择。\n"
                        "03 [硬] 周砚回家发现异常线索。"
                    ),
                    **common_meta,
                }
            if "重写编辑" in system:
                return {"content": candidate_chunk, **common_meta}
            if "审计" in system:
                return {"content": '{"passed":true,"issues":[]}', **common_meta}
            return {
                "content": (
                    "叙事约束：第三人称。\n"
                    "01 [硬] 周砚把旧信交给陈禾。\n"
                    "02 [硬] 陈禾确认旧信出现异常。\n"
                    "03 [硬] 两人在仓库内遭遇威胁。\n"
                    "04 [硬] 周砚与陈禾从南门离开仓库。\n"
                    "05 [硬] 陈禾向周砚交代后续条件。\n"
                    "06 [硬] 周砚回家后发现来源不明的异常线索。"
                ),
                **common_meta,
            }

        mock_chat.side_effect = respond
        with patch(
            "app.services.chapter_revision.build_de_ai_chunked_rewrite_prompts",
            return_value=chunk_prompts,
        ):
            response = self.client.post(
                f"{detail_url}/de-ai-preview",
                json={"content": source, "model": "deepseek:deepseek-chat"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()["data"]
        self.assertFalse(preview["mutated"])
        self.assertFalse(preview["auto_adopted"])
        compression_calls = [
            call
            for call in mock_chat.await_args_list
            if "事实账本压缩员"
            in str(call.kwargs["messages"][0].get("content") or "")
        ]
        self.assertEqual(len(compression_calls), 2)
        self.assertFalse(any(
            "【待审宏观账本】"
            in str(call.kwargs["messages"][-1].get("content") or "")
            for call in mock_chat.await_args_list
        ))

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_long_de_ai_fidelity_repair_reaudits_first_usable_length_variant(
        self,
        mock_chat,
    ):
        project_id = self.create_project()
        source = "原" * 2100
        chapter = self.create_chapter(project_id, content=source)
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        chunk_prompts = [
            (
                "本段目标为1000至1050个可见字符。\n"
                "【本段账本拍点】\n"
                "01 [硬] 周砚把旧信交给陈禾。\n"
                "02 [硬] 两人在仓库内遭遇威胁。"
            ),
            (
                "本段目标为1000至1050个可见字符。\n"
                "【本段账本拍点】\n"
                "01 [硬] 周砚与陈禾从南门离开。\n"
                "02 [硬] 周砚回家后发现异常。"
            ),
        ]
        initial_chunks = ["甲" * 1020, "乙" * 1020]
        repaired_second_chunk = "丙" * 990
        rewrite_calls = 0
        fidelity_calls = 0
        common_meta = {
            "model": "test-model",
            "request_meta": {"provider": "codex_cli", "model": "test-model"},
        }

        async def respond(*, messages, **_kwargs):
            nonlocal rewrite_calls, fidelity_calls
            system = str(messages[0].get("content") or "")
            if "事实记录员" in system:
                return {
                    "content": (
                        "叙事约束：第三人称。\n"
                        "01 [硬] 周砚把旧信交给陈禾。\n"
                        "02 [硬] 两人在仓库内遭遇威胁。\n"
                        "03 [硬] 两人从南门离开。\n"
                        "04 [硬] 周砚回家后发现异常。"
                    ),
                    **common_meta,
                }
            if "事实账本压缩员" in system:
                return {
                    "content": (
                        "01 [硬] 周砚与陈禾面对仓库内的威胁。\n"
                        "02 [硬] 两人作出离开的选择。\n"
                        "03 [硬] 周砚回家发现异常线索。"
                    ),
                    **common_meta,
                }
            if "重写编辑" in system:
                rewrite_calls += 1
                if "【待校正候选】" in str(messages[-1].get("content") or ""):
                    return {"content": repaired_second_chunk, **common_meta}
                return {
                    "content": initial_chunks[min(rewrite_calls - 1, 1)],
                    **common_meta,
                }
            if "事实校对员" in system:
                fidelity_calls += 1
                if fidelity_calls == 1:
                    return {
                        "content": (
                            '{"passed":false,"issues":[{"chunk":2,'
                            '"kind":"order","detail":"揭示与递交顺序颠倒"}]}'
                        ),
                        **common_meta,
                    }
                return {"content": '{"passed":true,"issues":[]}', **common_meta}
            if "表达结构审计员" in system:
                return {"content": '{"passed":true,"issues":[]}', **common_meta}
            raise AssertionError(f"unexpected prompt: {system}")

        mock_chat.side_effect = respond
        with patch(
            "app.services.chapter_revision.build_de_ai_chunked_rewrite_prompts",
            return_value=chunk_prompts,
        ):
            response = self.client.post(
                f"{detail_url}/de-ai-preview",
                json={"content": source, "model": "codex_cli:test-model"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()["data"]
        self.assertEqual(rewrite_calls, 3)
        self.assertEqual(fidelity_calls, 2)
        self.assertTrue(preview["fidelity_audit"]["passed"])
        self.assertEqual(
            preview["rewritten"],
            initial_chunks[0] + "\n\n" + repaired_second_chunk,
        )
        self.assertTrue(preview["revision_quality"]["accepted"])

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_de_ai_preview_retries_malformed_audit_json_without_hiding_candidate(
        self,
        mock_chat,
    ):
        project_id = self.create_project()
        source = (
            "7月12日，周砚把3封信带到A17仓库。陈禾查出第三封封口换过，"
            "两人约定在21点前处理完。"
        )
        candidate = (
            "7月12日，周砚带着3封信进了A17仓库。陈禾翻到第三封，封口"
            "已经换过；21点前，他们得把这件事处理完。"
        )
        chapter = self.create_chapter(project_id, content=source)
        common_meta = {
            "model": "test-model",
            "request_meta": {"provider": "deepseek", "model": "test-model"},
        }
        mock_chat.side_effect = [
            {
                "content": (
                    "叙事约束：第三人称。\n"
                    "01 [硬] 7月12日；周砚；3封信→A17仓库。\n"
                    "02 [硬] 陈禾确认第三封封口换过；期限21点。"
                ),
                **common_meta,
            },
            {"content": candidate, **common_meta},
            {"content": "审计通过，没有问题。", **common_meta},
            {"content": '{"passed":true,"issues":[]}', **common_meta},
            {"content": '{"passed":true,"issues":[]}', **common_meta},
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}/de-ai-preview",
            json={"content": source, "model": "deepseek:test-model"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()["data"]
        self.assertEqual(preview["rewritten"], candidate)
        self.assertTrue(preview["audit_passed"])
        retry_messages = mock_chat.await_args_list[3].kwargs["messages"]
        self.assertEqual(retry_messages[-2]["role"], "assistant")
        self.assertIn("唯一一个合法 JSON", retry_messages[-1]["content"])

    @patch(
        "app.services.chapter_quality.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_manual_quality_score_preserves_chapter_and_records_curve(self, mock_chat):
        project_id = self.create_project()
        source = "雨撞在窗纸上。林澈压低声音问：你昨夜究竟看见了谁？门外忽然传来第三下叩门声。"
        chapter = self.create_chapter(project_id, title="叩门", content=source)
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        before = self.client.get(detail_url).json()["data"]
        mock_chat.return_value = {
            "content": "```json\n"
            + json.dumps(
                {
                    "total_score": 80,
                    "scores": [
                        {"dimension": name, "score": score, "comment": f"{name}评价"}
                        for name, score in zip(
                            [
                                "开头吸引力",
                                "情节推进",
                                "角色塑造",
                                "对话质量",
                                "悬念设置",
                                "节奏控制",
                                "展示性描写",
                                "语言质量",
                            ],
                            [8, 7, 6, 8, 9, 7, 6, 5],
                            strict=True,
                        )
                    ],
                    "ai_flavor_count": 1,
                    "overall_assessment": "开场和悬念有效，语言仍可压缩。",
                    "bottom3_improvements": [
                        "语言质量：减少解释句",
                        "角色塑造：补充动作选择",
                        "展示性描写：增加触觉细节",
                    ],
                },
                ensure_ascii=False,
            )
            + "\n```",
            "model": "test-model",
            "request_meta": {"provider": "opencode_cli", "model": "test-model"},
        }

        response = self.client.post(
            f"{detail_url}/quality-score-preview",
            json={
                "title": "编辑器中的新标题",
                "content": source,
                "model": "opencode_cli:test-model",
            },
        )

        self.assertEqual(response.status_code, 200)
        report = response.json()["data"]
        self.assertFalse(report["mutated"])
        self.assertTrue(report["recorded"])
        self.assertEqual(report["total_score"], 56)
        self.assertEqual(report["max_score"], 80)
        self.assertEqual(len(report["scores"]), 8)
        call = mock_chat.await_args.kwargs
        self.assertEqual(call["model"], "opencode_cli:test-model")
        self.assertTrue(call["extra_body"]["local_cli_isolated"])
        self.assertIn("编辑器中的新标题", call["messages"][1]["content"])

        after = self.client.get(detail_url).json()["data"]
        self.assertEqual(after["content"], before["content"])
        self.assertEqual(after["current_version"], before["current_version"])
        self.assertEqual(after["snapshot_count"], before["snapshot_count"])
        db = SessionLocal()
        try:
            stored = db.query(Chapter).filter(Chapter.id == chapter["id"]).one()
            self.assertIsNone(stored.quality_score)
            self.assertIsNone(stored.quality_detail)
            self.assertIsNone(stored.quality_evaluated_at)
            metric = db.query(ChapterQualityMetric).filter(
                ChapterQualityMetric.id == report["quality_metric_id"]
            ).one()
            self.assertEqual(metric.chapter_id, chapter["id"])
            self.assertEqual(metric.chapter_version, before["current_version"])
            self.assertEqual(metric.total_score, 56)
            self.assertEqual(metric.max_score, 80)
            self.assertEqual(len(metric.dimension_scores), 8)
            self.assertEqual(metric.source, "manual_quality_button")
        finally:
            db.close()

    def test_list_chapters_keeps_reading_order_independent_from_outline_tree(self):
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
        second = self.create_chapter(project_id, "Second Chapter", second_outline["id"])
        unlinked = self.create_chapter(project_id, "Unlinked Chapter")
        first = self.create_chapter(project_id, "First Chapter", first_outline["id"])

        response = self.client.get(f"{API_PREFIX}/projects/{project_id}/chapters")
        self.assertEqual(response.status_code, 200)
        items = response.json()["data"]["items"]
        self.assertEqual(
            [item["title"] for item in items],
            ["Second Chapter", "Unlinked Chapter", "First Chapter"],
        )
        self.assertEqual([item["sort_order"] for item in items], [1000, 2000, 3000])
        self.assertEqual(items[2]["outline_path"], ["Volume One", "First Outline"])

        reordered = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/chapters/reorder",
            json={"ids": [first["id"], second["id"], unlinked["id"]]},
        )
        self.assertEqual(reordered.status_code, 200, reordered.text)
        reordered_items = reordered.json()["data"]["items"]
        self.assertEqual(
            [item["title"] for item in reordered_items],
            ["First Chapter", "Second Chapter", "Unlinked Chapter"],
        )
        self.assertEqual(
            [item["sort_order"] for item in reordered_items],
            [1000, 2000, 3000],
        )

        # Changing the outline hierarchy after writing must not reorder正文.
        response = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/outline/{first_outline['id']}",
            json={"sort_order": 9},
        )
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.get(f"{API_PREFIX}/projects/{project_id}/chapters")
        self.assertEqual(
            [item["title"] for item in response.json()["data"]["items"]],
            ["First Chapter", "Second Chapter", "Unlinked Chapter"],
        )

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

    def test_save_chapter_invalidates_linked_governance_and_review(self):
        project_id = self.create_project()
        chapter_data = self.create_chapter(project_id, content="窗台有一层新灰。")
        db = SessionLocal()
        try:
            chapter = db.query(Chapter).filter(Chapter.id == chapter_data["id"]).one()
            hook = upsert_foreshadowing(
                db,
                project_id,
                {"title": "窗台上的灰", "source_chapter_id": chapter.id},
            )
            review = record_chapter_governance_review(
                db,
                project_id,
                chapter,
                source="llm",
                findings_count=1,
                evidence="已检查本章伏笔",
            )
            db.commit()
            hook_id = hook.id
            review_id = review.id
        finally:
            db.close()

        response = self.client.put(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter_data['id']}",
            json={"content": "窗台被雨洗净，灰已经消失。"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["governance_invalidated_count"], 2)

        db = SessionLocal()
        try:
            self.assertEqual(db.query(Foreshadowing).filter(Foreshadowing.id == hook_id).one().status, "stale")
            self.assertEqual(
                db.query(ChapterGovernanceReview).filter(
                    ChapterGovernanceReview.id == review_id
                ).one().status,
                "stale",
            )
        finally:
            db.close()

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
