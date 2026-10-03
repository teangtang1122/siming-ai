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
    ChapterSummary,
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
            db.query(ChapterSummary).delete()
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

    def test_author_can_correct_summary_without_changing_body_version(self):
        project_id = self.create_project()
        chapter = self.create_chapter(project_id, content="林澈推开城门。")
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        before = self.client.get(detail_url).json()["data"]

        response = self.client.put(
            f"{detail_url}/summary",
            json={
                "summary_text": "林澈推开城门，但没有确认门外来者的身份。",
                "key_events": ["林澈推开城门", "来者身份仍待核"],
                "expected_version": before["current_version"],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]
        self.assertEqual(result["source"], "author")
        self.assertEqual(result["chapter_version"], 1)
        after = self.client.get(detail_url).json()["data"]
        self.assertEqual(after["summary_text"], result["summary_text"])
        self.assertEqual(after["key_events"], ["林澈推开城门", "来者身份仍待核"])
        self.assertEqual(after["content"], before["content"])
        self.assertEqual(after["current_version"], before["current_version"])
        self.assertEqual(after["snapshot_count"], before["snapshot_count"])
        self.assertEqual(after["cataloging_required"], before["cataloging_required"])

        db = SessionLocal()
        try:
            summary = db.query(ChapterSummary).filter_by(chapter_id=chapter["id"]).one()
            self.assertEqual(summary.ai_model, "author")
        finally:
            db.close()

    def test_author_summary_correction_rejects_stale_chapter_version(self):
        project_id = self.create_project()
        chapter = self.create_chapter(project_id, content="第一版正文。")
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        saved = self.client.put(
            detail_url,
            json={"content": "第二版正文。", "expected_version": 1},
        )
        self.assertEqual(saved.status_code, 200, saved.text)

        response = self.client.put(
            f"{detail_url}/summary",
            json={
                "summary_text": "基于旧正文的错误摘要。",
                "key_events": [],
                "expected_version": 1,
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("重新核对正文", response.json()["message"])
        self.assertIsNone(self.client.get(detail_url).json()["data"]["summary_text"])

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_de_ai_preview_applies_local_edits_without_mutating_chapter(self, mock_chat):
        project_id = self.create_project()
        source = (
            "周砚站在门口，心中不由得涌起一股难以言说的情绪。"
            "陈禾把三封信交给他，说今晚九点前必须送到城南邮局。"
        )
        chapter = self.create_chapter(project_id, content=source)
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        before = self.client.get(detail_url).json()["data"]
        old = "心中不由得涌起一股难以言说的情绪"
        new = "那股压在胸口的情绪一时难以说清"
        mock_chat.side_effect = [
            {
                "content": json.dumps({"edits": [{"old": old, "new": new}]}, ensure_ascii=False),
                "request_meta": {"provider": "opencode_cli", "model": "test-model"},
            },
            {"content": '{"passed":true,"issues":[]}'},
            {"content": '{"passed":true,"issues":[]}'},
        ]

        response = self.client.post(
            f"{detail_url}/de-ai-preview",
            json={"content": source, "model": "opencode_cli:test-model"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()["data"]
        self.assertEqual(preview["rewritten"], source.replace(old, new))
        self.assertEqual(preview["applied_edit_count"], 1)
        self.assertEqual(preview["rejected_edit_count"], 0)
        self.assertTrue(preview["audit_passed"])
        self.assertFalse(preview["mutated"])
        self.assertFalse(preview["persisted"])
        self.assertEqual(mock_chat.await_count, 3)
        edit_call = mock_chat.await_args_list[0].kwargs
        self.assertTrue(edit_call["extra_body"]["local_cli_isolated"])
        self.assertIn("【原文】", edit_call["messages"][1]["content"])
        self.assertNotIn("事实账本", edit_call["messages"][1]["content"])
        after = self.client.get(detail_url).json()["data"]
        self.assertEqual(after["content"], before["content"])
        self.assertEqual(after["current_version"], before["current_version"])
        self.assertEqual(after["snapshot_count"], before["snapshot_count"])

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_de_ai_preview_rejects_content_loss_before_extra_model_calls(self, mock_chat):
        project_id = self.create_project()
        source = (
            "周砚站在门口，心中不由得涌起一股难以言说的情绪。"
            "陈禾把三封信交给他，说今晚九点前必须送到城南邮局。"
        )
        chapter = self.create_chapter(project_id, content=source)
        detail_url = f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}"
        before = self.client.get(detail_url).json()["data"]
        mock_chat.return_value = {
            "content": json.dumps({"edits": [{"old": source, "new": "周砚送信。"}]}, ensure_ascii=False)
        }

        response = self.client.post(
            f"{detail_url}/de-ai-preview",
            json={"content": source, "model": "deepseek:test-model"},
        )

        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(mock_chat.await_count, 1)
        self.assertEqual(self.client.get(detail_url).json()["data"]["content"], before["content"])

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_de_ai_follow_up_uses_initial_original_for_fidelity(self, mock_chat):
        project_id = self.create_project()
        original = (
            "陈禾说，三天内若没有消息，周砚就把账页交到城南邮局三号信箱。"
            "周砚复述了一遍，把账页收好，留在原地等消息。"
        )
        prior = original.replace("复述了一遍", "照着重复了一遍")
        changed = prior.replace("把账页收好", "将账页仔细收好")
        chapter = self.create_chapter(project_id, content=original)
        mock_chat.side_effect = [
            {"content": json.dumps({"edits": [{"old": "把账页收好", "new": "将账页仔细收好"}]}, ensure_ascii=False)},
            {"content": '{"passed":true,"issues":[]}'},
            {"content": '{"passed":true,"issues":[]}'},
        ]

        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}/de-ai-preview",
            json={
                "content": prior,
                "original_content": original,
                "revision_round": 2,
                "model": "deepseek:test-model",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()["data"]
        self.assertEqual(preview["original"], original)
        self.assertEqual(preview["input"], prior)
        self.assertEqual(preview["rewritten"], changed)
        fidelity_messages = mock_chat.await_args_list[1].kwargs["messages"]
        self.assertIn(original, fidelity_messages[1]["content"])

    @patch(
        "app.services.chapter_revision.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    )
    def test_de_ai_preview_keeps_candidate_when_audit_is_unavailable(self, mock_chat):
        project_id = self.create_project()
        source = (
            "周砚站在门口，心中不由得涌起一股难以言说的情绪。"
            "陈禾把三封信交给他，说今晚九点前必须送到城南邮局。"
        )
        chapter = self.create_chapter(project_id, content=source)

        async def respond(*, messages, **_kwargs):
            if "局部修订" in messages[0]["content"]:
                return {"content": json.dumps({"edits": [{
                    "old": "心中不由得涌起一股难以言说的情绪",
                    "new": "那股压在胸口的情绪一时难以说清",
                }]}, ensure_ascii=False)}
            raise TimeoutError("审计超时")

        mock_chat.side_effect = respond
        response = self.client.post(
            f"{API_PREFIX}/projects/{project_id}/chapters/{chapter['id']}/de-ai-preview",
            json={"content": source, "model": "deepseek:test-model"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()["data"]
        self.assertIn("那股压在胸口", preview["rewritten"])
        self.assertFalse(preview["audit_passed"])
        self.assertTrue(any(item["code"] == "audit_unavailable" for item in preview["warnings"]))

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
