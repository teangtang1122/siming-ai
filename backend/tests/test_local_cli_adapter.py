"""Tests for local CLI model adapter helpers."""

import unittest
from unittest.mock import patch

from app.ai.local_cli_adapter import (
    LocalCLIAdapter,
    OPENCODE_DEFAULT_MODEL,
    hidden_subprocess_kwargs,
    messages_to_prompt,
    parse_cli_args,
    parse_cli_launch,
)


class LocalCLIAdapterHelperTestCase(unittest.TestCase):
    def test_messages_to_prompt_preserves_roles(self):
        prompt = messages_to_prompt([
            {"role": "system", "content": "Follow rules."},
            {"role": "user", "content": "Write chapter 1."},
        ])
        self.assertIn("[SYSTEM]\nFollow rules.", prompt)
        self.assertIn("[USER]\nWrite chapter 1.", prompt)

    def test_parse_cli_args_replaces_placeholders_from_json_array(self):
        args = parse_cli_args('["exec","--model","{model}","{prompt}"]', "codex_cli", "hello", "codex-cli")
        self.assertEqual(args, ["exec", "--model", "codex-cli", "hello"])

    def test_parse_cli_args_appends_prompt_without_placeholder(self):
        args = parse_cli_args('["exec"]', "codex_cli", "hello", "codex-cli")
        self.assertEqual(args, ["exec", "hello"])

    def test_parse_cli_launch_moves_long_prompt_to_stdin(self):
        prompt = "x" * 13000
        launch = parse_cli_launch('["-p","{prompt}"]', "claude_cli", prompt, "claude-code")
        self.assertEqual(launch.args, ["-p"])
        self.assertEqual(launch.stdin_text, prompt)

    def test_parse_cli_launch_keeps_short_prompt_in_args(self):
        launch = parse_cli_launch('["-p","{prompt}"]', "claude_cli", "hello", "claude-code")
        self.assertEqual(launch.args, ["-p", "hello"])
        self.assertIsNone(launch.stdin_text)

    def test_claude_default_args_bypass_permissions(self):
        launch = parse_cli_launch(None, "claude_cli", "hello", "claude-code")
        self.assertEqual(
            launch.args,
            ["--permission-mode", "bypassPermissions", "-p", "hello"],
        )

    def test_opencode_default_args_bypass_permissions(self):
        launch = parse_cli_launch(None, "opencode_cli", "hello", OPENCODE_DEFAULT_MODEL)
        self.assertEqual(
            launch.args,
            [
                "run",
                "--pure",
                "--dangerously-skip-permissions",
                "--format",
                "json",
                "--model",
                OPENCODE_DEFAULT_MODEL,
                "hello",
            ],
        )

    def test_opencode_long_prompt_is_not_moved_to_stdin(self):
        prompt = "x" * 13000
        launch = parse_cli_launch(None, "opencode_cli", prompt, OPENCODE_DEFAULT_MODEL)
        self.assertIsNone(launch.stdin_text)
        self.assertIn(prompt, launch.args)

    def test_mimocode_default_args_bypass_permissions(self):
        launch = parse_cli_launch(None, "mimocode_cli", "hello", "mimocode-cli")
        self.assertEqual(
            launch.args,
            ["run", "--dangerously-skip-permissions", "hello"],
        )

    def test_cursor_default_args_bypass_permissions(self):
        launch = parse_cli_launch(None, "cursor_cli", "hello", "cursor-agent")
        self.assertIn("--force", launch.args)
        self.assertIn("--approve-mcps", launch.args)
        self.assertIn("--trust", launch.args)

    def test_kilocode_default_args_auto_approve(self):
        launch = parse_cli_launch(None, "kilocode_cli", "hello", "kilocode-cli")
        self.assertEqual(launch.args, ["run", "--auto", "hello"])

    def test_qwen_code_default_args_use_yolo(self):
        launch = parse_cli_launch(None, "qwen_code_cli", "hello", "qwen-code-cli")
        self.assertEqual(
            launch.args,
            ["--approval-mode", "yolo", "--output-format", "text", "hello"],
        )

    def test_hermes_default_args_use_yolo(self):
        launch = parse_cli_launch(None, "hermes_cli", "hello", "hermes-agent")
        self.assertEqual(launch.args, ["--yolo", "--oneshot", "hello"])

    def test_openclaw_default_args_use_local_agent(self):
        launch = parse_cli_launch(None, "openclaw_cli", "hello", "openclaw-agent")
        self.assertEqual(
            launch.args,
            ["agent", "--local", "--json", "--message", "hello"],
        )

    def test_normalize_jsonl_output_extracts_text(self):
        adapter = LocalCLIAdapter(api_key="", base_url="codex_cli", cli_command="codex")
        text = adapter._normalize_output('{"type":"message","content":"hello"}\n{"delta":" world"}\n')
        self.assertEqual(text, "hello world")

    def test_normalize_opencode_jsonl_output_extracts_part_text(self):
        adapter = LocalCLIAdapter(api_key="", base_url="opencode_cli", cli_command="opencode")
        text = adapter._normalize_output(
            '{"type":"step_start","part":{"type":"step-start"}}\n'
            '{"type":"text","part":{"type":"text","text":"你好，世界"}}\n'
            '{"type":"step_finish","part":{"type":"step-finish"}}\n'
        )
        self.assertEqual(text, "你好，世界")

    def test_runtime_cwd_does_not_fall_back_to_process_cwd(self):
        with patch.dict(
            "app.ai.local_cli_adapter.os.environ",
            {"MOSHU_CONTENT_ROOT": r"D:\novels"},
            clear=True,
        ), patch("app.ai.local_cli_adapter.Path.mkdir"), patch(
            "app.ai.local_cli_adapter.Path.resolve",
            return_value=__import__("pathlib").Path(r"D:\novels"),
        ):
            cwd = LocalCLIAdapter._runtime_cwd(None)
        self.assertEqual(cwd, r"D:\novels")

    def test_normalize_plain_output_is_preserved(self):
        adapter = LocalCLIAdapter(api_key="", base_url="claude_cli", cli_command="claude")
        self.assertEqual(adapter._normalize_output("plain answer\n"), "plain answer")

    @patch("app.ai.local_cli_adapter.os.name", "nt")
    def test_hidden_subprocess_kwargs_hides_windows_console(self):
        kwargs = hidden_subprocess_kwargs()
        self.assertIn("creationflags", kwargs)
        self.assertGreater(kwargs["creationflags"], 0)
