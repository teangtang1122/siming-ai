"""Tests for scheduler agent execution — tool chain integration."""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class RunTaskPromptTest(unittest.TestCase):
    """Verify _run_task_prompt uses the agent tool chain."""

    def _mock_task(self):
        task = MagicMock()
        task.id = "task1"
        task.project_id = "p1"
        task.prompt = "Search for characters"
        task.tool_policy = ["list_characters", "search_characters"]
        return task

    def test_function_exists(self):
        """Verify _run_task_prompt is importable."""
        from app.services.scheduler.engine import _run_task_prompt
        self.assertTrue(callable(_run_task_prompt))

    @patch("app.ai.gateway.LLMGateway.stream_chat_completion_with_tools", new_callable=AsyncMock)
    def test_calls_llm_with_tools(self, mock_llm):
        """Verify the function calls LLM with tool schemas."""
        from app.services.scheduler.engine import _run_task_prompt

        mock_llm.return_value = {
            "content": "Here are the characters",
            "tool_calls": [],
        }

        task = self._mock_task()
        db = MagicMock()

        result = _run_task_prompt(db, task)
        mock_llm.assert_called_once()

    @patch("app.services.workspace.executor.execute_workspace_action", new_callable=AsyncMock)
    @patch("app.ai.gateway.LLMGateway.stream_chat_completion_with_tools", new_callable=AsyncMock)
    def test_executes_tool_calls(self, mock_llm, mock_exec):
        """Verify tool calls are executed through workspace executor."""
        from app.services.scheduler.engine import _run_task_prompt

        mock_llm.side_effect = [
            {
                "content": "",
                "tool_calls": [{
                    "id": "tc1",
                    "function": {"name": "list_characters", "arguments": "{}"},
                }],
            },
            {
                "content": "Found 3 characters",
                "tool_calls": [],
            },
        ]

        mock_exec.return_value = {
            "tool": "list_characters",
            "status": "ok",
            "detail": "Found 3",
            "data": {"items": []},
        }

        task = self._mock_task()
        db = MagicMock()

        result = _run_task_prompt(db, task)
        self.assertEqual(mock_exec.call_count, 1)

    @patch("app.ai.gateway.LLMGateway.stream_chat_completion_with_tools", new_callable=AsyncMock)
    def test_tool_policy_filters_schemas(self, mock_llm):
        """Verify tool_policy restricts which tools are available."""
        from app.services.scheduler.engine import _run_task_prompt

        mock_llm.return_value = {
            "content": "Done",
            "tool_calls": [],
        }

        task = self._mock_task()
        task.tool_policy = ["list_characters"]
        db = MagicMock()

        _run_task_prompt(db, task)

        call_args = mock_llm.call_args
        tools = call_args.kwargs.get("tools", [])
        tool_names = {t["function"]["name"] for t in tools}
        self.assertIn("list_characters", tool_names)
        # Should not include tools not in policy
        self.assertNotIn("create_project", tool_names)

    @patch("app.ai.gateway.LLMGateway.stream_chat_completion_with_tools", new_callable=AsyncMock)
    def test_empty_policy_allows_all(self, mock_llm):
        """Verify empty tool_policy allows all tools."""
        from app.services.scheduler.engine import _run_task_prompt

        mock_llm.return_value = {
            "content": "Done",
            "tool_calls": [],
        }

        task = self._mock_task()
        task.tool_policy = None
        db = MagicMock()

        _run_task_prompt(db, task)

        call_args = mock_llm.call_args
        tools = call_args.kwargs.get("tools", [])
        self.assertGreater(len(tools), 5)


if __name__ == "__main__":
    unittest.main()
