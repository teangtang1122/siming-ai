"""Tests for the model-driven new-novel interview."""
import asyncio
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.novel_creation_interview import (
    INTERVIEW_API_TIMEOUT_SECONDS,
    INTERVIEW_CLI_TIMEOUT_GRACE_SECONDS,
    INTERVIEW_CLI_TIMEOUT_SECONDS,
    INTERVIEW_MAX_TURNS,
    NovelInterviewError,
    decide_next_interview_step,
)


class NovelCreationInterviewDecisionTest(unittest.TestCase):
    def test_model_receives_full_history_and_asks_one_contextual_question(self):
        response = {
            "action": "ask_more",
            "reason": "实验体身份需要一个会改变开局的主动选择",
            "question": {
                "question": "既然她是实验体，她第一次主动违抗组织会付出什么代价？",
                "purpose": "决定开篇冲突和主角底色",
                "options": [],
                "type": "text",
            },
        }
        history = [
            {"question": "你更想从哪里开始？", "answer": "从她逃出实验室的那一夜开始"},
            {"question": "追捕她的人是谁？", "answer": "她曾经最信任的训练官"},
        ]
        with patch(
            "app.services.novel_creation_interview.LLMGateway.model_identity",
            return_value=("openai", "test-model"),
        ), patch(
            "app.services.novel_creation_interview.LLMGateway.chat_completion",
            new=AsyncMock(return_value={"content": json.dumps(response, ensure_ascii=False)}),
        ) as chat_mock:
            result = asyncio.run(decide_next_interview_step(
                user_brief="我想写一个被秘密组织培养的实验体",
                qa_history=history,
                genre_label="玄幻/修仙",
                target_audience="男频",
                platform="起点",
                model="openai:test-model",
            ))

        self.assertEqual(result["action"], "ask_more")
        self.assertEqual(len(result["questions"]), 1)
        self.assertIn("主动违抗组织", result["questions"][0]["question"])
        kwargs = chat_mock.await_args.kwargs
        self.assertEqual(kwargs["timeout"], INTERVIEW_API_TIMEOUT_SECONDS)
        self.assertEqual(kwargs["retry"], 0)
        prompt = "\n".join(item["content"] for item in kwargs["messages"])
        self.assertIn("禁止调用固定问题清单", prompt)
        self.assertIn("她曾经最信任的训练官", prompt)
        self.assertIn("从她逃出实验室的那一夜开始", prompt)

    def test_local_cli_interview_has_short_independent_timeout(self):
        with patch(
            "app.services.novel_creation_interview.LLMGateway.model_identity",
            return_value=("codex_cli", "codex-cli"),
        ), patch(
            "app.services.novel_creation_interview.is_local_cli_provider",
            return_value=True,
        ), patch(
            "app.services.novel_creation_interview.LLMGateway.chat_completion",
            new=AsyncMock(return_value={"content": '{"action":"generate","reason":"信息足够"}'}),
        ) as chat_mock:
            result = asyncio.run(decide_next_interview_step(
                user_brief="写一个实验体逃亡的故事",
                model="codex_cli:codex-cli",
            ))

        self.assertEqual(result["action"], "generate")
        self.assertEqual(chat_mock.await_args.kwargs["timeout"], INTERVIEW_CLI_TIMEOUT_SECONDS)
        self.assertEqual(
            chat_mock.await_args.kwargs["extra_body"],
            {"local_cli_timeout_grace_seconds": INTERVIEW_CLI_TIMEOUT_GRACE_SECONDS},
        )

    def test_dynamic_interview_drops_model_supplied_choices(self):
        content = json.dumps({
            "action": "ask_more",
            "reason": "需要确认代价",
            "question": {
                "question": "她逃离组织时最不愿失去什么？",
                "purpose": "确认开局的情感代价",
                "options": ["自由", "同伴", "记忆"],
                "type": "single_select",
            },
        }, ensure_ascii=False)
        with patch(
            "app.services.novel_creation_interview.LLMGateway.model_identity",
            return_value=("openai", "test-model"),
        ), patch(
            "app.services.novel_creation_interview.LLMGateway.chat_completion",
            new=AsyncMock(return_value={"content": content}),
        ):
            result = asyncio.run(decide_next_interview_step(
                user_brief="一个实验体逃离组织的故事",
                model="openai:test-model",
            ))

        self.assertEqual(result["questions"][0]["options"], [])
        self.assertEqual(result["questions"][0]["type"], "text")

    def test_quota_failure_is_not_replaced_by_a_preset_question(self):
        with patch(
            "app.services.novel_creation_interview.LLMGateway.model_identity",
            return_value=("opencode_cli", "free-model"),
        ), patch(
            "app.services.novel_creation_interview.LLMGateway.chat_completion",
            new=AsyncMock(side_effect=RuntimeError("Free usage exceeded, retrying in 9h")),
        ):
            with self.assertRaises(NovelInterviewError) as raised:
                asyncio.run(decide_next_interview_step(
                    user_brief="创建新小说",
                    model="opencode_cli:free-model",
                ))

        self.assertEqual(raised.exception.failure_class, "quota_or_rate_limit")
        self.assertIn("Free usage exceeded", str(raised.exception))
        self.assertIn("切换有额度的模型", raised.exception.next_action)

    def test_empty_response_is_reported_without_fallback(self):
        with patch(
            "app.services.novel_creation_interview.LLMGateway.model_identity",
            return_value=("openai", "test-model"),
        ), patch(
            "app.services.novel_creation_interview.LLMGateway.chat_completion",
            new=AsyncMock(return_value={"content": ""}),
        ):
            with self.assertRaises(NovelInterviewError) as raised:
                asyncio.run(decide_next_interview_step(user_brief="创建新小说"))

        self.assertEqual(raised.exception.failure_class, "empty_response")

    def test_repeated_model_question_is_rejected(self):
        content = json.dumps({
            "action": "ask_more",
            "question": {"question": "主角是谁？", "options": [], "type": "text"},
        }, ensure_ascii=False)
        with patch(
            "app.services.novel_creation_interview.LLMGateway.model_identity",
            return_value=("openai", "test-model"),
        ), patch(
            "app.services.novel_creation_interview.LLMGateway.chat_completion",
            new=AsyncMock(return_value={"content": content}),
        ):
            with self.assertRaises(NovelInterviewError) as raised:
                asyncio.run(decide_next_interview_step(
                    user_brief="创建新小说",
                    qa_history=[{"question": "主角是谁？", "answer": "一个实验体"}],
                ))

        self.assertEqual(raised.exception.failure_class, "invalid_response")
        self.assertIn("重复", str(raised.exception))

    def test_interview_turn_cap_enters_generation_without_another_call(self):
        history = [
            {"question": f"动态问题 {index}", "answer": f"回答 {index}"}
            for index in range(INTERVIEW_MAX_TURNS)
        ]
        chat_mock = AsyncMock()
        with patch(
            "app.services.novel_creation_interview.LLMGateway.chat_completion",
            new=chat_mock,
        ):
            result = asyncio.run(decide_next_interview_step(
                user_brief="创建新小说",
                qa_history=history,
            ))

        self.assertEqual(result["action"], "generate")
        chat_mock.assert_not_awaited()


class NovelCreationInterviewPersistenceTest(unittest.TestCase):
    @staticmethod
    def _session():
        return SimpleNamespace(
            user_brief="一个实验体逃亡的故事",
            genre="fantasy",
            target_audience="all",
            platform="qidian",
            draft_json={},
            last_error_json=None,
        )

    def test_pending_question_and_history_are_saved_in_session(self):
        from app.services.workspace.tools.novel_creation import _run_dynamic_interview

        session = self._session()
        db = MagicMock()
        history = [{"question": "她为什么逃？", "answer": "她发现记忆被篡改"}]
        next_question = {
            "question": "哪段被篡改的记忆最可能让她反过来追查组织？",
            "purpose": "决定故事发动机",
            "options": [],
            "type": "text",
        }
        with patch(
            "app.services.workspace.tools.novel_creation._evaluate_answers",
            new=AsyncMock(return_value={
                "action": "ask_more",
                "reason": "承接最新回答",
                "questions": [next_question],
            }),
        ):
            result, feedback, skipped = asyncio.run(_run_dynamic_interview(
                db,
                session,
                session_id="session-1",
                user_brief="",
                feedback="",
                qa_history=history,
                skip_questions=False,
                model="openai:test-model",
            ))

        self.assertEqual(result["status"], "need_clarification")
        self.assertEqual(result["data"]["questions"], [next_question])
        self.assertEqual(feedback, "")
        self.assertFalse(skipped)
        self.assertEqual(session.draft_json["interview"]["history"], history)
        self.assertEqual(session.draft_json["interview"]["pending_question"], next_question)
        db.commit.assert_called_once()

    def test_failure_keeps_history_and_specific_recovery(self):
        from app.services.workspace.tools.novel_creation import _run_dynamic_interview

        session = self._session()
        db = MagicMock()
        history = [{"question": "她为什么逃？", "answer": "她发现记忆被篡改"}]
        error = NovelInterviewError(
            "动态采访失败：请求超时。",
            failure_class="timeout",
            next_action="切换更快的模型后发送“继续”。",
        )
        with patch(
            "app.services.workspace.tools.novel_creation._evaluate_answers",
            new=AsyncMock(side_effect=error),
        ):
            result, _, _ = asyncio.run(_run_dynamic_interview(
                db,
                session,
                session_id="session-1",
                user_brief="",
                feedback="",
                qa_history=history,
                skip_questions=False,
                model="openai:test-model",
            ))

        self.assertEqual(result["status"], "interview_failed")
        self.assertEqual(result["data"]["failure_class"], "timeout")
        self.assertEqual(session.draft_json["interview"]["history"], history)
        self.assertEqual(session.last_error_json["failure_class"], "timeout")
        db.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
