"""Tests for local CLI model adapter helpers."""

import unittest
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from app.core.exceptions import LLMError
from app.ai.local_cli_adapter import (
    LocalCLIAdapter,
    OPENCODE_DEFAULT_MODEL,
    detect_cli_auth_error,
    detect_cli_quota_error,
    discover_local_cli_models,
    ensure_opencode_logging_args,
    extract_cli_error,
    hidden_subprocess_kwargs,
    local_cli_model_options,
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

    def test_codex_default_launch_reads_prompt_from_stdin(self):
        launch = parse_cli_launch(None, "codex_cli", "hello", "codex-cli")
        self.assertEqual(
            launch.args,
            ["exec", "--dangerously-bypass-approvals-and-sandbox", "-"],
        )
        self.assertEqual(launch.stdin_text, "hello")

    def test_codex_runtime_options_keep_stdin_dash_as_prompt_marker(self):
        adapter = LocalCLIAdapter(api_key="", base_url="codex_cli", cli_command="codex")
        with tempfile.TemporaryDirectory() as directory:
            launch = adapter._launch("hello", "codex-cli")
            args = list(launch.args)
            adapter._apply_provider_runtime_options(args, model="codex-cli", cwd=directory)
            output_file, cleanup = adapter._ensure_codex_output_file(args, directory)
            try:
                self.assertTrue(cleanup)
                self.assertEqual(args[-1], "-")
                self.assertIn("--cd", args)
                self.assertIn("--skip-git-repo-check", args)
                self.assertIn("--ephemeral", args)
                self.assertIn("--output-last-message", args)
                self.assertEqual(args[args.index("--output-last-message") + 1], output_file)
            finally:
                Path(output_file).unlink(missing_ok=True)

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

    @patch("app.ai.local_cli_adapter.subprocess.run")
    @patch("app.ai.local_cli_adapter.shutil.which", return_value=r"C:\tools\mimo.cmd")
    def test_mimocode_model_discovery_uses_native_models_command(self, _which, run_mock):
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = "mimo/mimo-auto\nxiaomi/mimo-v2.5-pro\n"
        models = discover_local_cli_models("mimocode_cli", "mimo")
        self.assertEqual(
            [item["id"] for item in models],
            ["mimo/mimo-auto", "xiaomi/mimo-v2.5-pro"],
        )
        command = run_mock.call_args.args[0]
        self.assertTrue(any(str(part).endswith("mimo.cmd") for part in command))
        self.assertEqual(command[-1], "models")

    def test_codex_model_options_include_configured_model_and_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            (codex_home / "config.toml").write_text('model = "gpt-5.5"\n', encoding="utf-8")
            with patch.dict("app.ai.local_cli_adapter.os.environ", {"CODEX_HOME": str(codex_home)}, clear=True):
                models = local_cli_model_options("codex_cli", command=None)

        ids = [item["id"] for item in models]
        self.assertEqual(ids[0], "gpt-5.5")
        self.assertIn("codex-cli", ids)
        self.assertIn("Codex 配置", models[0]["display_name"])

    def test_non_listing_cli_model_options_include_env_config_and_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".claude").mkdir()
            (home / ".claude" / "settings.json").write_text(
                json.dumps({"model": "sonnet", "profiles": [{"default_model": "opus"}]}),
                encoding="utf-8",
            )
            with patch.dict(
                "app.ai.local_cli_adapter.os.environ",
                {"CLAUDE_MODEL": "haiku"},
                clear=True,
            ), patch("app.ai.local_cli_adapter.Path.home", return_value=home):
                models = local_cli_model_options("claude_cli", command=None)

        ids = [item["id"] for item in models]
        self.assertEqual(ids[:3], ["haiku", "sonnet", "opus"])
        self.assertIn("claude-code", ids)
        self.assertIn("环境变量 CLAUDE_MODEL", models[0]["display_name"])

    def test_custom_cli_model_options_include_fixed_cli_arg_model(self):
        models = local_cli_model_options(
            "custom_cli",
            command=None,
            cli_args='["run","--model","local/qwen3-coder","{prompt}"]',
        )

        ids = [item["id"] for item in models]
        self.assertEqual(ids[0], "local/qwen3-coder")
        self.assertIn("custom-cli", ids)

    def test_mimocode_file_launch_attaches_prompt_and_selected_model(self):
        adapter = LocalCLIAdapter(
            api_key="",
            base_url="mimocode_cli",
            cli_command="mimo",
            cli_args='["run","--dangerously-skip-permissions","{prompt}"]',
        )
        with tempfile.TemporaryDirectory() as directory:
            launch, prompt_file = adapter._opencode_family_launch(
                prompt="中文任务",
                model="xiaomi/mimo-v2.5-pro",
                cwd=directory,
                attachments=[],
            )
            self.assertIn("--model", launch.args)
            self.assertIn("xiaomi/mimo-v2.5-pro", launch.args)
            self.assertIn("--format", launch.args)
            self.assertIn("json", launch.args)
            self.assertIn("--dir", launch.args)
            self.assertIn("--file", launch.args)
            self.assertEqual(launch.args[-1], prompt_file)
            self.assertEqual(Path(prompt_file).read_text(encoding="utf-8"), "中文任务")

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
            [
                "agent",
                "--local",
                "--json",
                "--session-key",
                "agent:siming:local-cli",
                "--message",
                "hello",
            ],
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

    def test_json_error_event_is_detected_even_with_zero_exit_code(self):
        error = extract_cli_error(
            '{"type":"error","error":{"data":{"message":"Please sign in"}}}\n'
        )
        self.assertEqual(error, "Please sign in")

    def test_codex_transient_json_errors_do_not_hide_final_message(self):
        adapter = LocalCLIAdapter(api_key="", base_url="codex_cli", cli_command="codex")
        text = (
            '{"type":"error","message":"Reconnecting... 2/5 (request timed out)"}\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"OK"}}\n'
        )
        self.assertEqual(extract_cli_error(text), "")
        self.assertEqual(adapter._normalize_output(text), "OK")

    def test_cli_auth_errors_are_detected_from_plain_and_json_output(self):
        self.assertIn("登录凭据", detect_cli_auth_error("(InvalidToken)"))
        self.assertIn(
            "Please sign in",
            detect_cli_auth_error('{"type":"error","error":{"data":{"message":"Please sign in"}}}'),
        )

    def test_quota_errors_are_detected_from_plain_and_json_output(self):
        self.assertIn(
            "额度/限额",
            detect_cli_quota_error("Error: quota exceeded for provider"),
        )
        self.assertIn(
            "额度/限额",
            detect_cli_quota_error('{"type":"error","error":{"message":"HTTP 429 Too Many Requests"}}'),
        )
        self.assertIn(
            "额度/限额",
            detect_cli_quota_error("今日免费额度已用完，请明天再试"),
        )
        self.assertIn(
            "Free usage exceeded",
            detect_cli_quota_error("Free usage exceeded, subscribe to Go [retrying in 9h 28m attempt #1]"),
        )
        self.assertIn(
            "Rate limit exceeded",
            detect_cli_quota_error('error.error="AI_APICallError: Rate limit exceeded. Please try again later."'),
        )

    def test_opencode_logging_args_are_inserted_before_run(self):
        args = ["run", "--pure", "hello"]
        ensure_opencode_logging_args("opencode_cli", args)

        self.assertEqual(args[:4], ["--print-logs", "--log-level", "WARN", "run"])

        ensure_opencode_logging_args("opencode_cli", args)
        self.assertEqual(args.count("--print-logs"), 1)
        self.assertEqual(args.count("--log-level"), 1)

    def test_opencode_file_launch_enables_warn_logs(self):
        adapter = LocalCLIAdapter(api_key="", base_url="opencode_cli", cli_command="opencode")
        with tempfile.TemporaryDirectory() as directory:
            launch, _prompt_file = adapter._opencode_family_launch(
                prompt="task",
                model=OPENCODE_DEFAULT_MODEL,
                cwd=directory,
                attachments=[],
            )

        self.assertEqual(launch.args[:4], ["--print-logs", "--log-level", "WARN", "run"])

    def test_local_cli_adapter_raises_clear_quota_error_even_with_zero_exit_code(self):
        adapter = LocalCLIAdapter(
            api_key="",
            base_url="custom_cli",
            cli_command=sys.executable,
            cli_args=json.dumps(["-c", "print('Error: quota exceeded for provider')"]),
        )

        with self.assertRaisesRegex(LLMError, "额度/限额"):
            asyncio.run(adapter.chat_completion(
                messages=[{"role": "user", "content": "hello"}],
                model="custom-cli",
            ))

    def test_local_cli_adapter_aborts_retrying_quota_process_before_timeout(self):
        code = (
            "import sys, time; "
            "print('Free usage exceeded, subscribe to Go [retrying in 9h 28m attempt #1]', flush=True); "
            "time.sleep(5)"
        )
        adapter = LocalCLIAdapter(
            api_key="",
            base_url="custom_cli",
            cli_command=sys.executable,
            cli_args=json.dumps(["-c", code]),
        )

        started = time.monotonic()
        with self.assertRaisesRegex(LLMError, "Free usage exceeded"):
            asyncio.run(adapter.chat_completion(
                messages=[{"role": "user", "content": "hello"}],
                model="custom-cli",
            ))

        self.assertLess(time.monotonic() - started, 3)

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

    def test_agent_cli_prompt_is_written_as_utf8_task_file(self):
        adapter = LocalCLIAdapter(api_key="", base_url="claude_cli", cli_command="claude")
        with tempfile.TemporaryDirectory() as directory:
            prompt_file = adapter._write_prompt_file("中文任务：写第一章", directory, "claude_cli")
            self.assertEqual(Path(prompt_file).read_text(encoding="utf-8"), "中文任务：写第一章")
            self.assertEqual(Path(prompt_file).parent, Path(directory))

    def test_file_prompt_instruction_blocks_repository_scanning_and_mcp_writes(self):
        instruction = LocalCLIAdapter._file_prompt_instruction(
            r"D:\novels\moshu-task.md",
            [r"D:\novels\reference.txt"],
        )
        self.assertIn("不是代码助手", instruction)
        self.assertIn("不要扫描代码仓库", instruction)
        self.assertIn("不要调用 Siming MCP", instruction)
        self.assertIn(r"D:\novels\reference.txt", instruction)

    def test_normalize_plain_output_is_preserved(self):
        adapter = LocalCLIAdapter(api_key="", base_url="claude_cli", cli_command="claude")
        self.assertEqual(adapter._normalize_output("plain answer\n"), "plain answer")

    @patch("app.ai.local_cli_adapter.os.name", "nt")
    def test_hidden_subprocess_kwargs_hides_windows_console(self):
        kwargs = hidden_subprocess_kwargs()
        self.assertIn("creationflags", kwargs)
        self.assertGreater(kwargs["creationflags"], 0)
