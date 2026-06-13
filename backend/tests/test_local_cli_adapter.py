"""Tests for local CLI model adapter helpers."""

import unittest
from unittest.mock import patch

from app.ai.local_cli_adapter import (
    LocalCLIAdapter,
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

    def test_normalize_jsonl_output_extracts_text(self):
        adapter = LocalCLIAdapter(api_key="", base_url="codex_cli", cli_command="codex")
        text = adapter._normalize_output('{"type":"message","content":"hello"}\n{"delta":" world"}\n')
        self.assertEqual(text, "hello world")

    def test_normalize_plain_output_is_preserved(self):
        adapter = LocalCLIAdapter(api_key="", base_url="claude_cli", cli_command="claude")
        self.assertEqual(adapter._normalize_output("plain answer\n"), "plain answer")

    @patch("app.ai.local_cli_adapter.os.name", "nt")
    def test_hidden_subprocess_kwargs_hides_windows_console(self):
        kwargs = hidden_subprocess_kwargs()
        self.assertIn("creationflags", kwargs)
        self.assertGreater(kwargs["creationflags"], 0)
