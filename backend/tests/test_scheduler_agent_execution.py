"""Scheduler Agent execution tests using the real streamed tool protocol."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import threading
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _tool_call(call_id: str, name: str, arguments: Any) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _category_call(*categories: str) -> dict[str, Any]:
    return _tool_call(
        "category-call",
        "set_tool_categories",
        {"enabled_categories": list(categories)},
    )


class _StreamGateway:
    """Return genuine async generators, matching every production provider."""

    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self.turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    async def stream_chat_completion_with_tools(self, **kwargs: Any):
        self.calls.append(copy.deepcopy(kwargs))
        if not self.turns:
            raise AssertionError("模型收到的步骤数超出测试预期")
        turn = self.turns.pop(0)
        content = str(turn.get("content") or "")
        if content:
            yield {"type": "content_delta", "delta": content}

        tool_calls = turn.get("tool_calls") or []
        for index, tool_call in enumerate(tool_calls):
            function = tool_call["function"]
            raw_arguments = function.get("arguments", "{}")
            if isinstance(raw_arguments, dict):
                raw_arguments = json.dumps(raw_arguments, ensure_ascii=False)
            raw_arguments = str(raw_arguments)
            midpoint = max(1, len(raw_arguments) // 2)
            yield {
                "type": "tool_call_delta",
                "index": index,
                "id": tool_call.get("id"),
                "name": function.get("name"),
                "arguments_delta": raw_arguments[:midpoint],
            }
            if raw_arguments[midpoint:]:
                yield {
                    "type": "tool_call_delta",
                    "index": index,
                    "arguments_delta": raw_arguments[midpoint:],
                }

        yield {
            "type": "done",
            "finish_reason": "tool_calls" if tool_calls else "stop",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
            },
        }


class RunTaskPromptTest(unittest.TestCase):
    """Verify scheduled prompts use streamed responses and category gating."""

    @classmethod
    def setUpClass(cls) -> None:
        from app.bootstrap.composition import configure_application_services

        configure_application_services()

    def _mock_task(self) -> MagicMock:
        task = MagicMock()
        task.id = "task1"
        task.project_id = "p1"
        task.prompt = "Search for characters"
        task.tool_policy = ["list_characters", "search_characters"]
        return task

    @staticmethod
    def _run_with_gateway(task: MagicMock, gateway: _StreamGateway) -> str:
        from app.services.scheduler.engine import _run_task_prompt

        with patch(
            "app.services.workspace.scheduled_task_runner.LLMGateway",
            gateway,
        ):
            return _run_task_prompt(MagicMock(), task)

    def test_function_exists(self) -> None:
        from app.services.scheduler.engine import _run_task_prompt

        self.assertTrue(callable(_run_task_prompt))

    def test_collects_real_async_generator_and_opens_category_next_step(self) -> None:
        gateway = _StreamGateway([
            {"tool_calls": [_category_call("story_knowledge")]},
            {"content": "Here are the characters"},
        ])

        result = self._run_with_gateway(self._mock_task(), gateway)

        self.assertEqual(result, "Here are the characters")
        self.assertEqual(len(gateway.calls), 2)
        first_names = {
            tool["function"]["name"] for tool in gateway.calls[0]["tools"]
        }
        second_names = {
            tool["function"]["name"] for tool in gateway.calls[1]["tools"]
        }
        self.assertEqual(first_names, {"set_tool_categories"})
        self.assertEqual(
            second_names,
            {"set_tool_categories", "list_characters", "search_characters"},
        )
        self.assertEqual(gateway.calls[0]["tool_choice"], "required")
        self.assertEqual(gateway.calls[1]["tool_choice"], "auto")

    @patch(
        "app.services.workspace.scheduled_task_runner.workspace_executor."
        "execute_workspace_action",
        new_callable=AsyncMock,
    )
    def test_executes_business_tool_after_category_selection(
        self,
        mock_execute: AsyncMock,
    ) -> None:
        gateway = _StreamGateway([
            {"tool_calls": [_category_call("story_knowledge")]},
            {
                "tool_calls": [
                    _tool_call("character-call", "list_characters", {})
                ]
            },
            {"content": "Found 3 characters"},
        ])
        mock_execute.return_value = {
            "tool": "list_characters",
            "status": "ok",
            "detail": "Found 3",
            "data": {"items": []},
        }

        result = self._run_with_gateway(self._mock_task(), gateway)

        self.assertEqual(result, "Found 3 characters")
        mock_execute.assert_awaited_once()
        self.assertEqual(
            mock_execute.await_args.args[2],
            {"tool": "list_characters", "arguments": {}},
        )
        self.assertEqual(
            [message["role"] for message in gateway.calls[2]["messages"]],
            ["system", "user", "assistant", "tool", "assistant", "tool"],
        )

    @patch(
        "app.services.workspace.scheduled_task_runner.workspace_executor."
        "execute_workspace_action",
        new_callable=AsyncMock,
    )
    def test_agent_continues_past_the_old_ten_step_limit(
        self,
        mock_execute: AsyncMock,
    ) -> None:
        gateway = _StreamGateway([
            {"tool_calls": [_category_call("story_knowledge")]},
            *[
                {
                    "tool_calls": [
                        _tool_call(f"character-call-{index}", "list_characters", {})
                    ]
                }
                for index in range(10)
            ],
            {"content": "Finished after the old limit"},
        ])
        mock_execute.return_value = {
            "tool": "list_characters",
            "status": "ok",
            "detail": "Checked",
            "data": {"items": []},
        }

        result = self._run_with_gateway(self._mock_task(), gateway)

        self.assertEqual(result, "Finished after the old limit")
        self.assertEqual(len(gateway.calls), 12)
        self.assertEqual(mock_execute.await_count, 10)

    def test_tool_policy_is_intersected_after_category_selection(self) -> None:
        task = self._mock_task()
        task.tool_policy = ["list_characters"]
        gateway = _StreamGateway([
            {"tool_calls": [_category_call("story_knowledge")]},
            {"content": "Done"},
        ])

        self._run_with_gateway(task, gateway)

        second_names = {
            tool["function"]["name"] for tool in gateway.calls[1]["tools"]
        }
        self.assertEqual(
            second_names,
            {"set_tool_categories", "list_characters"},
        )

    def test_empty_policy_allows_selected_scheduler_category(self) -> None:
        task = self._mock_task()
        task.tool_policy = None
        gateway = _StreamGateway([
            {"tool_calls": [_category_call("story_knowledge")]},
            {"content": "Done"},
        ])

        self._run_with_gateway(task, gateway)

        first_names = {
            tool["function"]["name"] for tool in gateway.calls[0]["tools"]
        }
        second_names = {
            tool["function"]["name"] for tool in gateway.calls[1]["tools"]
        }
        self.assertEqual(first_names, {"set_tool_categories"})
        self.assertIn("list_characters", second_names)
        self.assertIn("create_character", second_names)
        self.assertNotIn("create_project", second_names)
        self.assertGreater(len(second_names), 5)

    def test_nonempty_invalid_policy_does_not_expand_to_all_tools(self) -> None:
        task = self._mock_task()
        task.tool_policy = [""]
        gateway = _StreamGateway([
            {"tool_calls": [_category_call("story_knowledge")]},
            {"content": "No authorized business tools"},
        ])

        self._run_with_gateway(task, gateway)

        second_names = {
            tool["function"]["name"] for tool in gateway.calls[1]["tools"]
        }
        self.assertEqual(second_names, {"set_tool_categories"})

    @patch(
        "app.services.workspace.scheduled_task_runner.workspace_executor."
        "execute_workspace_action",
        new_callable=AsyncMock,
    )
    def test_invalid_json_rejects_the_complete_batch_before_any_handler(
        self,
        mock_execute: AsyncMock,
    ) -> None:
        gateway = _StreamGateway([
            {"tool_calls": [_category_call("story_knowledge")]},
            {
                "tool_calls": [
                    _tool_call("bad-call", "list_characters", "{not-json")
                ]
            },
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            "conversation_protocol_invalid:invalid_native_tool_arguments_json",
        ):
            self._run_with_gateway(self._mock_task(), gateway)

        mock_execute.assert_not_awaited()
        self.assertEqual(len(gateway.calls), 2)

    @patch(
        "app.services.workspace.scheduled_task_runner.workspace_executor."
        "execute_workspace_action",
        new_callable=AsyncMock,
    )
    def test_hidden_tool_call_is_rejected_by_runtime(
        self,
        mock_execute: AsyncMock,
    ) -> None:
        task = self._mock_task()
        task.tool_policy = ["list_characters"]
        gateway = _StreamGateway([
            {"tool_calls": [_category_call("story_knowledge")]},
            {
                "tool_calls": [
                    _tool_call("hidden-call", "search_characters", {"query": "A"})
                ]
            },
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            "conversation_protocol_invalid:native_tool_not_open",
        ):
            self._run_with_gateway(task, gateway)

        mock_execute.assert_not_awaited()
        self.assertEqual(len(gateway.calls), 2)

    @patch(
        "app.services.workspace.scheduled_task_runner.workspace_executor."
        "execute_workspace_action",
        new_callable=AsyncMock,
    )
    def test_category_controller_mixed_batch_is_rejected_atomically(
        self,
        mock_execute: AsyncMock,
    ) -> None:
        gateway = _StreamGateway([
            {
                "tool_calls": [
                    _category_call("story_knowledge"),
                    _tool_call("early-call", "list_characters", {}),
                ]
            },
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            "conversation_protocol_invalid:category_controller_must_be_only_call",
        ):
            self._run_with_gateway(self._mock_task(), gateway)

        mock_execute.assert_not_awaited()
        self.assertEqual(len(gateway.calls), 1)

    @patch(
        "app.services.workspace.scheduled_task_runner.workspace_executor."
        "execute_workspace_action",
        new_callable=AsyncMock,
    )
    def test_terminal_draft_mixed_batch_is_rejected_before_any_handler(
        self,
        mock_execute: AsyncMock,
    ) -> None:
        task = self._mock_task()
        task.tool_policy = ["save_external_outline_draft", "search_context"]
        gateway = _StreamGateway([
            {"tool_calls": [_category_call("writing_context")]},
            {
                "tool_calls": [
                    _tool_call(
                        "draft-call",
                        "save_external_outline_draft",
                        {
                            "nodes": [{"title": "不应保存"}],
                            "context_manifest_id": "manifest-1",
                            "context_selection_token": "token-1",
                        },
                    ),
                    _tool_call("search-call", "search_context", {}),
                ]
            },
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            "conversation_protocol_invalid:draft_tool_must_be_only_call",
        ):
            self._run_with_gateway(task, gateway)

        mock_execute.assert_not_awaited()
        self.assertEqual(len(gateway.calls), 2)

    @patch(
        "app.services.workspace.scheduled_task_runner.workspace_executor."
        "execute_workspace_action",
        new_callable=AsyncMock,
    )
    def test_terminal_draft_result_ends_without_another_model_step(
        self,
        mock_execute: AsyncMock,
    ) -> None:
        task = self._mock_task()
        task.tool_policy = ["save_external_outline_draft"]
        gateway = _StreamGateway([
            {"tool_calls": [_category_call("writing_context")]},
            {
                "tool_calls": [
                    _tool_call(
                        "draft-call",
                        "save_external_outline_draft",
                        {
                            "nodes": [{"title": "第一章"}],
                            "context_manifest_id": "manifest-1",
                            "context_selection_token": "token-1",
                        },
                    )
                ]
            },
        ])
        mock_execute.return_value = {
            "tool": "save_external_outline_draft",
            "status": "ok",
            "detail": "大纲草稿已保存，等待作者确认",
            "data": {"draft_id": "outline-draft-1"},
            "turn_directive": "end_after_outline_draft",
            "turn_terminal": True,
        }

        result = self._run_with_gateway(task, gateway)

        self.assertEqual(
            result,
            "大纲草稿已生成并显示在大纲页，尚未写入正式大纲。"
            "请先编辑或确认；只有作者确认后才能据此发起新的写章请求。",
        )
        mock_execute.assert_awaited_once()
        self.assertEqual(len(gateway.calls), 2)

    @patch(
        "app.services.workspace.scheduled_task_runner.workspace_executor."
        "execute_workspace_action",
        new_callable=AsyncMock,
    )
    def test_malformed_and_oversized_batches_never_execute_a_prefix(
        self,
        mock_execute: AsyncMock,
    ) -> None:
        cases = {
            "empty": (
                [_tool_call("empty", "list_characters", "")],
                "native_tool_arguments_empty",
            ),
            "non_object": (
                [_tool_call("array", "list_characters", "[]")],
                "native_tool_arguments_not_object",
            ),
            "missing_id": (
                [_tool_call("", "list_characters", {})],
                "native_tool_call_id_missing",
            ),
            "missing_name": (
                [_tool_call("missing-name", "", {})],
                "native_tool_name_missing",
            ),
            "result_batch_over_capacity": (
                [
                    _tool_call(f"call-{index}", "list_characters", {})
                    for index in range(13)
                ],
                "tool_result_batch_over_capacity",
            ),
        }
        for calls, reason in cases.values():
            with self.subTest(reason=reason):
                mock_execute.reset_mock()
                gateway = _StreamGateway([
                    {"tool_calls": [_category_call("story_knowledge")]},
                    {"tool_calls": calls},
                ])
                with self.assertRaisesRegex(
                    RuntimeError,
                    f"conversation_protocol_invalid:{reason}",
                ):
                    self._run_with_gateway(self._mock_task(), gateway)
                mock_execute.assert_not_awaited()
                self.assertEqual(len(gateway.calls), 2)


class RunScheduledTaskNowTest(unittest.TestCase):
    def test_protocol_error_is_persisted_with_stable_reason(self) -> None:
        from app.services.scheduler.engine import _execute_task

        task = MagicMock()
        task.id = "task-protocol-error"
        task.cron_expr = None
        task.interval_minutes = None
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = task
        stable_error = (
            "Agent execution failed: conversation_protocol_invalid:"
            "native_tool_arguments_empty: 整批未执行"
        )
        with (
            patch("app.services.scheduler.engine.SessionLocal", return_value=db),
            patch(
                "app.services.scheduler.engine._run_task_prompt",
                side_effect=RuntimeError(stable_error),
            ),
            patch("app.services.scheduler.engine.commit_session") as commit,
        ):
            _execute_task(task.id)

        self.assertEqual(task.last_run_status, "error")
        self.assertEqual(task.last_run_output, stable_error)
        self.assertEqual(commit.call_count, 2)
        db.close.assert_called_once()

    def test_sync_runner_is_offloaded_from_existing_event_loop(self) -> None:
        from app.services.workspace.tools.scheduler import run_scheduled_task_now

        task = MagicMock()
        task.id = "task1"
        task.name = "Task"
        worker_threads: list[int] = []

        def execute_task(_task_id: str) -> None:
            worker_threads.append(threading.get_ident())

        db = MagicMock()
        caller_thread = threading.get_ident()
        with (
            patch(
                "app.services.workspace.tools.scheduler._find_task",
                return_value=task,
            ),
            patch(
                "app.services.workspace.tools.scheduler.get_active_tasks",
                return_value=[],
            ),
            patch(
                "app.services.workspace.tools.scheduler._execute_task",
                side_effect=execute_task,
            ) as mock_execute,
        ):
            result = asyncio.run(
                run_scheduled_task_now(db, "p1", {"id": "task1"})
            )

        self.assertEqual(result["status"], "ok")
        mock_execute.assert_called_once_with("task1")
        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads[0], caller_thread)
        db.refresh.assert_called_once_with(task)


if __name__ == "__main__":
    unittest.main()
