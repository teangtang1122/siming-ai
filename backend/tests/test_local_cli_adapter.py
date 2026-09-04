"""Tests for local CLI model adapter helpers."""

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.ai.local_cli_adapter import (
    DEFAULT_CLI_MODELS,
    OPENCODE_DEFAULT_MODEL,
    OPENCODE_MODELS,
    OPENCODE_RETIRED_MODELS,
    CLIPermissionRequiredError,
    CLIStalledError,
    CLITurnTerminal,
    LocalCLIAdapter,
    communicate_with_cli_quota_detection,
    detect_cli_auth_error,
    detect_cli_permission_request,
    detect_cli_quota_error,
    discover_local_cli_models,
    effective_local_cli_model,
    ensure_opencode_logging_args,
    extract_cli_error,
    extract_cli_runtime_error,
    hidden_subprocess_kwargs,
    inspect_opencode_turn,
    local_cli_model_options,
    messages_to_prompt,
    parse_cli_args,
    parse_cli_launch,
    preferred_local_cli_model,
    sample_cli_process_tree,
)
from app.ai.local_cli_prompt import prepare_direct_mcp_launch, prepare_opencode_launch
from app.core.exceptions import LLMError


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

    def test_claude_default_args_are_safe(self):
        launch = parse_cli_launch(None, "claude_cli", "hello", "claude-code")
        self.assertEqual(launch.args, ["-p", "hello"])

    def test_codex_default_launch_reads_prompt_from_stdin(self):
        launch = parse_cli_launch(None, "codex_cli", "hello", "codex-cli")
        self.assertEqual(
            launch.args,
            ["exec", "-"],
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

    def test_codex_writing_uses_low_reasoning_without_overriding_explicit_value(self):
        args = ["exec", "-"]
        LocalCLIAdapter._apply_codex_writing_options(args, "writing")
        self.assertEqual(args[-1], "-")
        self.assertIn('model_reasoning_effort="low"', args)

        explicit = ["exec", "-c", 'model_reasoning_effort="high"', "-"]
        LocalCLIAdapter._apply_codex_writing_options(explicit, "writing")
        self.assertNotIn('model_reasoning_effort="low"', explicit)

    def test_opencode_default_args_are_safe(self):
        launch = parse_cli_launch(None, "opencode_cli", "hello", OPENCODE_DEFAULT_MODEL)
        self.assertEqual(
            launch.args,
            [
                "run",
                "--pure",
                "--format",
                "json",
                "--model",
                OPENCODE_DEFAULT_MODEL,
                "hello",
            ],
        )

    def test_opencode_default_uses_a_current_free_model(self):
        self.assertEqual(OPENCODE_DEFAULT_MODEL, "opencode/big-pickle")
        self.assertEqual(DEFAULT_CLI_MODELS["opencode_cli"], OPENCODE_DEFAULT_MODEL)
        self.assertNotIn("opencode/deepseek-v4-flash-free", OPENCODE_MODELS)

    def test_retired_opencode_models_are_mapped_to_the_current_default(self):
        for model in OPENCODE_RETIRED_MODELS:
            self.assertEqual(
                effective_local_cli_model("opencode_cli", model),
                OPENCODE_DEFAULT_MODEL,
            )

    @patch("app.ai.local_cli_models.discover_local_cli_models")
    def test_opencode_preferred_model_uses_a_discovered_current_free_model(self, discover):
        discover.return_value = [
            {"id": "opencode/paid-model", "display_name": "Paid"},
            {"id": "opencode/hy3-free", "display_name": "Free"},
        ]

        self.assertEqual(
            preferred_local_cli_model("opencode_cli", "opencode"),
            "opencode/hy3-free",
        )

    def test_opencode_long_prompt_is_not_moved_to_stdin(self):
        prompt = "x" * 13000
        launch = parse_cli_launch(None, "opencode_cli", prompt, OPENCODE_DEFAULT_MODEL)
        self.assertIsNone(launch.stdin_text)
        self.assertIn(prompt, launch.args)

    @patch("app.ai.local_cli_models.subprocess.run")
    @patch("app.ai.local_cli_models.shutil.which", return_value=r"C:\tools\opencode.exe")
    def test_opencode_verbose_capacity_survives_configured_name_merge(self, _which, run_mock):
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = "opencode/big-pickle\n" + json.dumps({
            "id": "big-pickle", "providerID": "opencode", "name": "Big Pickle",
            "limit": {"context": 200000, "input": 160000, "output": 32000},
            "headers": {"Authorization": "must-not-be-exported"},
        })
        models = local_cli_model_options(
            "opencode_cli", "opencode",
            cli_args='["run","--model","opencode/big-pickle","{prompt}"]',
        )
        selected = [item for item in models if item["id"] == "opencode/big-pickle"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["context_window_tokens"], 200000)
        self.assertEqual(selected[0]["max_output_tokens"], 32000)
        self.assertEqual(selected[0]["capacity_source"], "opencode_cli_metadata")
        self.assertNotIn("must-not-be-exported", json.dumps(models))
        self.assertEqual(run_mock.call_args.args[0][-2:], ["models", "--verbose"])

    @patch("app.ai.local_cli_models.subprocess.run")
    @patch("app.ai.local_cli_models.shutil.which", return_value=r"C:\tools\opencode.exe")
    def test_opencode_discovery_rejects_wrong_identity_and_invalid_capacity(self, _which, run_mock):
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = (
            "opencode/first\n" + json.dumps({
                "id": "different", "providerID": "opencode",
                "limit": {"context": 200000, "output": 32000},
            }) + "\nopencode/second\n" + json.dumps({
                "id": "second", "providerID": "opencode",
                "limit": {"context": 2048, "output": 2048},
            }) + "\nopencode/third\n" + json.dumps({
                "id": "third", "providerID": "opencode",
                "limit": {"context": 100000, "output": 10000},
            })
        )
        models = discover_local_cli_models("opencode_cli", "opencode")
        self.assertEqual([item["id"] for item in models], [
            "opencode/first", "opencode/second", "opencode/third",
        ])
        self.assertNotIn("context_window_tokens", models[0])
        self.assertNotIn("context_window_tokens", models[1])
        self.assertEqual(models[2]["context_window_tokens"], 100000)

    def test_mimocode_default_args_are_safe(self):
        launch = parse_cli_launch(None, "mimocode_cli", "hello", "mimocode-cli")
        self.assertEqual(
            launch.args,
            ["run", "hello"],
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

    @patch("app.ai.local_cli_adapter.subprocess.run")
    @patch("app.ai.local_cli_adapter.shutil.which", return_value=r"C:\tools\codex.cmd")
    def test_codex_model_discovery_parses_models_json(self, _which, run_mock):
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = '[{"slug":"gpt-5.6-sol"},{"slug":"gpt-5.6-terra"},{"id":"codex-cli"}]'
        models = discover_local_cli_models("codex_cli", "codex")
        self.assertEqual(
            [item["id"] for item in models],
            ["gpt-5.6-sol", "gpt-5.6-terra", "codex-cli"],
        )
        command = run_mock.call_args.args[0]
        self.assertTrue(any(str(part).endswith("codex.cmd") for part in command))
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

    def test_non_listing_cli_model_options_include_env_non_claude_config_and_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".claude").mkdir()
            (home / ".claude" / "settings.json").write_text(
                json.dumps({"model": "mimo-v2.5-pro", "profiles": [{"default_model": "opus"}]}),
                encoding="utf-8",
            )
            with patch.dict(
                "app.ai.local_cli_adapter.os.environ",
                {"CLAUDE_MODEL": "haiku"},
                clear=True,
            ), patch("app.ai.local_cli_adapter.Path.home", return_value=home):
                models = local_cli_model_options("claude_cli", command=None)

        ids = [item["id"] for item in models]
        self.assertEqual(ids[:3], ["haiku", "mimo-v2.5-pro", "opus"])
        self.assertIn("claude-code", ids)
        self.assertIn("CLAUDE_MODEL", models[0]["display_name"])

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

    def test_cursor_default_args_are_safe(self):
        launch = parse_cli_launch(None, "cursor_cli", "hello", "cursor-agent")
        self.assertNotIn("--force", launch.args)
        self.assertNotIn("--approve-mcps", launch.args)
        self.assertNotIn("--trust", launch.args)

    def test_kilocode_default_args_are_safe(self):
        launch = parse_cli_launch(None, "kilocode_cli", "hello", "kilocode-cli")
        self.assertEqual(launch.args, ["run", "hello"])

    def test_qwen_code_default_args_are_safe(self):
        launch = parse_cli_launch(None, "qwen_code_cli", "hello", "qwen-code-cli")
        self.assertEqual(
            launch.args,
            ["--output-format", "text", "hello"],
        )

    def test_hermes_default_args_are_safe(self):
        launch = parse_cli_launch(None, "hermes_cli", "hello", "hermes-agent")
        self.assertEqual(launch.args, ["--oneshot", "hello"])

    def test_ungranted_turn_strips_legacy_auto_approval_flags(self):
        adapter = LocalCLIAdapter(api_key="", base_url="claude_cli", cli_command="claude")
        args = ["--permission-mode", "bypassPermissions", "-p", "hello"]

        adapter._apply_provider_runtime_options(
            args,
            model="claude-code",
            cwd=tempfile.gettempdir(),
            permission_granted=False,
        )

        self.assertNotIn("--permission-mode", args)
        self.assertNotIn("bypassPermissions", args)
        self.assertNotIn("--dangerously-skip-permissions", args)

    def test_one_turn_grant_adds_provider_permission_flags(self):
        cases = {
            "claude_cli": "--dangerously-skip-permissions",
            "codex_cli": "--dangerously-bypass-approvals-and-sandbox",
            "cursor_cli": "--approve-mcps",
            "qwen_code_cli": "--approval-mode",
            "hermes_cli": "--yolo",
        }
        for provider, expected in cases.items():
            adapter = LocalCLIAdapter(api_key="", base_url=provider, cli_command="cli")
            args = ["run", "hello"]
            adapter._apply_provider_runtime_options(
                args,
                model="provider-default",
                cwd=tempfile.gettempdir(),
                permission_granted=True,
            )
            self.assertIn(expected, args, provider)

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

    def test_dsh_default_args_use_headless_profile(self):
        launch = parse_cli_launch(None, "dsh_cli", "hello", "dsh-cli")
        self.assertEqual(launch.args, ["--profile", "headless", "hello"])

    def test_dsh_direct_mcp_uses_one_turn_patch(self):
        adapter = LocalCLIAdapter(api_key="", base_url="dsh_cli", cli_command="dsh")
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.ai.local_cli_prompt.resolve_siming_mcp_server",
            return_value={
                "command": r"D:\Siming\python.exe",
                "args": [r"D:\Siming\moshu-mcp-server.py", "--creation-session-id", "session-1"],
                "cwd": r"D:\Siming",
            },
        ):
            launch, env = prepare_direct_mcp_launch(
                adapter,
                adapter._launch("read and update", "dsh-cli"),
                cwd=directory,
                env=adapter._isolated_environment({}, True),
                permission_pack="creation_session",
                creation_session_id="session-1",
            )
            patch_path = Path(launch.args[launch.args.index("--patch") + 1])
            payload = json.loads(patch_path.read_text(encoding="utf-8"))

        config = payload[0]["insert"][0]["config"]
        self.assertEqual(config["serverName"], "siming_turn")
        self.assertEqual(config["args"][-1], "session-1")
        self.assertEqual(launch.args[-1], "read and update")
        self.assertNotIn("NO_MCP", env)

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

    def test_normalize_opencode_lifecycle_only_output_is_empty(self):
        adapter = LocalCLIAdapter(api_key="", base_url="opencode_cli", cli_command="opencode")
        text = adapter._normalize_output(
            '{"type":"step_start","sessionID":"session-1","part":{"type":"step-start"}}\n'
            '{"type":"step_finish","sessionID":"session-1","part":{"type":"step-finish"}}\n'
        )
        self.assertEqual(text, "")

    def test_inspect_opencode_turn_marks_unknown_finish_as_incomplete(self):
        state = inspect_opencode_turn(
            '{"type":"step_start","sessionID":"ses-1","part":{"type":"step-start"}}\n'
            '{"type":"step_finish","sessionID":"ses-1",'
            '"part":{"type":"step-finish","reason":"unknown"}}\n'
        )
        self.assertEqual(state.session_id, "ses-1")
        self.assertEqual(state.finish_reason, "unknown")
        self.assertTrue(state.incomplete)

    def test_inspect_opencode_turn_accepts_stop_finish(self):
        state = inspect_opencode_turn(
            '{"type":"step_start","sessionID":"ses-1","part":{"type":"step-start"}}\n'
            '{"type":"step_finish","sessionID":"ses-1",'
            '"part":{"type":"step-finish","reason":"stop"}}\n'
        )
        self.assertFalse(state.incomplete)

    def test_opencode_zero_exit_runtime_error_is_not_silently_discarded(self):
        error = extract_cli_runtime_error(
            'timestamp=2026-08-22T00:37:02Z level=ERROR message="stream error" '
            'error.error="AI_APICallError: Service Unavailable"'
        )
        self.assertEqual(error, "AI_APICallError: Service Unavailable")

    def test_opencode_unknown_finish_continues_the_same_session_once(self):
        adapter = LocalCLIAdapter(
            api_key="",
            base_url="opencode_cli",
            cli_command="opencode",
        )
        incomplete = (
            b'{"type":"step_start","sessionID":"ses-1",'
            b'"part":{"type":"step-start"}}\n'
            b'{"type":"step_finish","sessionID":"ses-1",'
            b'"part":{"type":"step-finish","reason":"unknown"}}\n'
        )
        completed = (
            '{"type":"step_start","sessionID":"ses-1",'
            '"part":{"type":"step-start"}}\n'
            '{"type":"text","sessionID":"ses-1",'
            '"part":{"type":"text","text":"已完成"}}\n'
            '{"type":"step_finish","sessionID":"ses-1",'
            '"part":{"type":"step-finish","reason":"stop"}}\n'
        ).encode()
        process = SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as directory, patch.object(
            adapter,
            "_command",
            return_value="opencode",
        ), patch(
            "app.ai.local_cli_adapter.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ) as create_process, patch(
            "app.ai.local_cli_adapter.communicate_with_cli_quota_detection",
            new=AsyncMock(side_effect=[(incomplete, b""), (completed, b"")]),
        ) as communicate:
            result = asyncio.run(adapter._run_once(
                "执行任务",
                "opencode/big-pickle",
                {
                    "local_cli_isolated": True,
                    "_local_cli_isolated_cwd": directory,
                    "local_cli_timeout_seconds": 0,
                    "local_cli_resume_incomplete_opencode": True,
                    "local_cli_mcp_creation_session_id": "creation-1",
                    "local_cli_quiet_seconds": 120,
                    "local_cli_suspected_stall_seconds": 300,
                    "local_cli_stalled_seconds": 600,
                },
            ))

        self.assertEqual(result, "已完成")
        self.assertEqual(create_process.await_count, 2)
        resume_args = create_process.await_args_list[1].args
        self.assertIn("--session", resume_args)
        self.assertIn("ses-1", resume_args)
        calls = communicate.await_args_list
        self.assertIsNone(calls[0].kwargs["timeout_seconds"])
        self.assertIsNone(calls[1].kwargs["timeout_seconds"])
        for call in calls:
            self.assertTrue(callable(call.kwargs["external_activity_probe"]))
            self.assertEqual(call.kwargs["quiet_seconds"], 120)
            self.assertEqual(call.kwargs["suspected_stall_seconds"], 300)
            self.assertEqual(call.kwargs["stalled_seconds"], 600)

    def test_normalize_opencode_tool_use_does_not_become_model_text(self):
        adapter = LocalCLIAdapter(api_key="", base_url="opencode_cli", cli_command="opencode")
        text = adapter._normalize_output(
            '{"type":"tool_use","part":{"type":"tool","tool":"read",'
            '"state":{"status":"completed","output":"task file contents"}}}\n'
            '{"type":"text","part":{"type":"text","text":"最终正文"}}\n'
        )
        self.assertEqual(text, "最终正文")

    def test_normalize_opencode_tool_use_only_output_is_empty(self):
        adapter = LocalCLIAdapter(api_key="", base_url="opencode_cli", cli_command="opencode")
        text = adapter._normalize_output(
            '{"type":"tool_use","part":{"type":"tool","tool":"read",'
            '"state":{"status":"completed","output":"task file contents"}}}\n'
        )
        self.assertEqual(text, "")

    def test_normalize_opencode_preserves_direct_structured_json_payload(self):
        adapter = LocalCLIAdapter(api_key="", base_url="opencode_cli", cli_command="opencode")
        payload = '{"data":{"story_overview":"一场记忆追踪","volumes":[]}}'
        text = adapter._normalize_output(
            '{"type":"step_start","part":{"type":"step-start"}}\n'
            + payload
            + '\n{"type":"step_finish","part":{"type":"step-finish"}}\n'
        )
        self.assertEqual(text, payload)

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

    def test_cli_permission_prompts_are_detected(self):
        self.assertIn(
            "聊天窗口确认",
            detect_cli_permission_request("Allow MCP server siming? [y/n]"),
        )
        self.assertIn(
            "聊天窗口确认",
            detect_cli_permission_request("是否允许使用 MCP 工具？"),
        )
        self.assertIn(
            "token quota is not enough",
            detect_cli_quota_error("token quota is not enough; add credit before retrying"),
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

    def test_cli_cpu_activity_prevents_false_stall_without_text_output(self):
        async def run_busy_cli():
            code = (
                "import time; end=time.perf_counter()+0.45; value=0; "
                "exec('while time.perf_counter() < end:\\n value += 1'); print('finished', flush=True)"
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **hidden_subprocess_kwargs(),
            )
            return await communicate_with_cli_quota_detection(
                process,
                poll_seconds=0.02,
                quiet_seconds=0.04,
                suspected_stall_seconds=0.1,
                stalled_seconds=0.2,
            )

        sample_number = 0

        def active_metrics(_pid):
            nonlocal sample_number
            sample_number += 1
            return {
                "alive": True,
                "process_count": 1,
                "cpu_seconds": float(sample_number),
                "read_bytes": 0,
                "write_bytes": 0,
                "rss_bytes": 1,
                "metrics_available": True,
            }

        with patch(
            "app.ai.local_cli_monitor.sample_cli_process_tree",
            side_effect=active_metrics,
        ):
            stdout, stderr = asyncio.run(run_busy_cli())
        self.assertIn(b"finished", stdout)
        self.assertEqual(stderr, b"")

    def test_process_metrics_tolerate_platform_without_io_counters(self):
        class ProcessWithoutIOCounters:
            def __init__(self, _pid):
                pass

            def children(self, recursive=True):
                self.assert_recursive = recursive
                return []

            def is_running(self):
                return True

            def status(self):
                return "running"

            def cpu_times(self):
                return SimpleNamespace(user=0.25, system=0.5)

            def memory_info(self):
                return SimpleNamespace(rss=32)

        portable_psutil = SimpleNamespace(
            Process=ProcessWithoutIOCounters,
            Error=RuntimeError,
            STATUS_ZOMBIE="zombie",
        )
        with patch("app.ai.local_cli_monitor.psutil", portable_psutil):
            metrics = sample_cli_process_tree(123)

        self.assertTrue(metrics["alive"])
        self.assertTrue(metrics["metrics_available"])
        self.assertEqual(metrics["cpu_seconds"], 0.75)
        self.assertEqual(metrics["read_bytes"], 0)
        self.assertEqual(metrics["write_bytes"], 0)
        self.assertEqual(metrics["rss_bytes"], 32)

    def test_closed_output_streams_do_not_bypass_stall_monitor(self):
        async def run_silent_cli():
            code = "import os,time; os.close(1); os.close(2); time.sleep(5)"
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **hidden_subprocess_kwargs(),
            )
            return await communicate_with_cli_quota_detection(
                process,
                poll_seconds=0.02,
                quiet_seconds=0.04,
                suspected_stall_seconds=0.1,
                stalled_seconds=0.2,
            )

        stable_metrics = {
            "alive": True,
            "process_count": 1,
            "cpu_seconds": 0.0,
            "read_bytes": 0,
            "write_bytes": 0,
            "rss_bytes": 1,
            "metrics_available": True,
        }
        with patch(
            "app.ai.local_cli_monitor.sample_cli_process_tree",
            return_value=stable_metrics,
        ), self.assertRaisesRegex(CLIStalledError, "确认卡住"):
            asyncio.run(run_silent_cli())

    def test_persisted_chapter_draft_immediately_stops_cli_process(self):
        async def run_cli_until_draft():
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import time; print('writing', flush=True); time.sleep(5)",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **hidden_subprocess_kwargs(),
            )
            probes = 0

            def terminal_probe():
                nonlocal probes
                probes += 1
                return "draft-1" if probes >= 2 else None

            return await communicate_with_cli_quota_detection(
                process,
                timeout_seconds=10,
                terminal_probe=terminal_probe,
                terminal_poll_seconds=0.1,
                poll_seconds=0.1,
            )

        started = time.monotonic()
        with self.assertRaisesRegex(CLITurnTerminal, "draft-1"):
            asyncio.run(run_cli_until_draft())
        self.assertLess(time.monotonic() - started, 3)

    def test_terminal_draft_probe_uses_exact_run_iteration_evidence(self):
        session = MagicMock()
        detected = (
            {
                "tool": "save_external_outline_draft",
                "status": "ok",
                "detail": "大纲草稿已保存",
                "data": {"draft_id": "outline-draft-1"},
                "turn_directive": "end_after_outline_draft",
                "turn_terminal": True,
            },
            "大纲草稿已生成",
        )
        runtime_body = {
            "local_cli_terminal_draft_project_id": "project-1",
            "local_cli_terminal_draft_run_id": "run-1",
            "local_cli_terminal_draft_iteration": 4,
        }

        with patch(
            "app.database.session.SessionLocal",
            return_value=session,
        ), patch(
            "app.services.workspace.terminal_draft_detection.local_cli_terminal_draft",
            return_value=detected,
        ) as probe_drafts:
            probe = LocalCLIAdapter._terminal_turn_probe(runtime_body)
            self.assertIsNotNone(probe)
            self.assertEqual(
                probe(),
                "save_external_outline_draft:outline-draft-1",
            )

        probe_drafts.assert_called_once_with(
            session,
            "project-1",
            "run-1",
            4,
        )
        session.close.assert_called_once_with()

    def test_category_selection_ends_only_the_pending_model_step(self):
        from app.services.tool_category_state import (
            activate_tool_categories,
            create_tool_category_state,
            remove_tool_category_state,
            replace_tool_categories,
        )

        state_file = create_tool_category_state()
        try:
            probe = LocalCLIAdapter._terminal_turn_probe({
                "local_cli_mcp_authorized": True,
                "local_cli_mcp_tool_category_state_file": state_file,
            })
            self.assertIsNotNone(probe)
            self.assertIsNone(probe())
            replace_tool_categories(state_file, ["story_knowledge"])
            self.assertEqual(probe(), "set_tool_categories:1")
            activate_tool_categories(state_file)
            self.assertIsNone(probe())
            replace_tool_categories(state_file, ["writing_context"])
            self.assertEqual(probe(), "set_tool_categories:2")
        finally:
            remove_tool_category_state(state_file)

    def test_category_probe_requires_managed_mcp_authorization(self):
        self.assertIsNone(LocalCLIAdapter._terminal_turn_probe({
            "local_cli_mcp_tool_category_state_file": "not-authorized",
        }))

    def test_terminal_output_distinguishes_categories_and_draft_types(self):
        adapter = LocalCLIAdapter(api_key="", base_url="opencode_cli", cli_command="opencode")
        context = SimpleNamespace(cwd="unused", isolated=False)
        process = SimpleNamespace(returncode=-1)
        for reason, expected in (
            ("set_tool_categories:1", "工具类别已切换，继续下一模型步骤。"),
            ("save_external_outline_draft:1", "大纲草稿已生成，等待作者确认。"),
            ("save_external_chapter_draft:1", "章节草稿已生成，等待作者保存。"),
        ):
            with self.subTest(reason=reason), patch.object(adapter, "_cleanup_isolated_workspace"):
                result = asyncio.run(adapter._finalize_run_output(
                    context, process, b"", b"", reason, "model", {},
                ))
                self.assertEqual(result, expected)

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

    def test_isolated_cli_retries_transient_network_failure_and_cleans_each_workspace(self):
        adapter = LocalCLIAdapter(api_key="", base_url="opencode_cli", cli_command="opencode")
        run_once = AsyncMock(side_effect=[LLMError("unknown certificate verification error"), "CLI_OK"])
        sleep = AsyncMock()

        with patch.object(adapter, "_run_once", run_once), patch(
            "app.ai.local_cli_adapter.asyncio.sleep",
            sleep,
        ):
            result = asyncio.run(adapter._run(
                "Reply exactly CLI_OK",
                OPENCODE_DEFAULT_MODEL,
                {"local_cli_isolated": True},
            ))

        self.assertEqual(result, "CLI_OK")
        self.assertEqual(run_once.await_count, 2)
        sleep.assert_awaited_once_with(1)
        workspaces = [Path(call.args[2]["_local_cli_isolated_cwd"]) for call in run_once.await_args_list]
        self.assertEqual(len(set(workspaces)), 2)
        self.assertTrue(all(not workspace.exists() for workspace in workspaces))

    def test_explicitly_granted_nonisolated_cli_does_not_retry_transient_failure(self):
        adapter = LocalCLIAdapter(api_key="", base_url="opencode_cli", cli_command="opencode")
        run_once = AsyncMock(side_effect=[LLMError("stream error"), "unexpected success"])

        with patch.object(adapter, "_run_once", run_once), self.assertRaisesRegex(
            LLMError, "stream error"
        ):
            asyncio.run(adapter._run(
                "prompt",
                "model",
                {
                    "local_cli_isolated": False,
                    "local_cli_mcp_authorized": True,
                },
            ))

        self.assertEqual(run_once.await_count, 1)

    def test_direct_mcp_cli_does_not_restart_after_transient_post_write_failure(self):
        adapter = LocalCLIAdapter(api_key="", base_url="opencode_cli", cli_command="opencode")
        run_once = AsyncMock(
            side_effect=[
                LLMError("connection reset after durable MCP write"),
                "unexpected second process",
            ]
        )

        with patch.object(adapter, "_run_once", run_once), self.assertRaisesRegex(
            LLMError, "connection reset"
        ):
            asyncio.run(
                adapter._run(
                    "prompt",
                    "model",
                    {
                        "local_cli_isolated": True,
                        "local_cli_mcp_authorized": True,
                        "local_cli_retry_attempts": 3,
                    },
                )
            )

        self.assertEqual(run_once.await_count, 1)

    def test_ungranted_cli_permission_prompt_stops_without_waiting_for_timeout(self):
        async def run_prompting_cli():
            code = (
                "import time; "
                "print('Allow MCP server siming? [y/n]', flush=True); "
                "time.sleep(5)"
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **hidden_subprocess_kwargs(),
            )
            return await communicate_with_cli_quota_detection(
                process,
                timeout_seconds=10,
                poll_seconds=0.02,
                stop_on_permission_request=True,
            )

        started = time.monotonic()
        with self.assertRaises(CLIPermissionRequiredError):
            asyncio.run(run_prompting_cli())
        self.assertLess(time.monotonic() - started, 3)

    def test_isolated_cli_does_not_retry_authentication_failure(self):
        adapter = LocalCLIAdapter(api_key="", base_url="qwen_code_cli", cli_command="qwen")
        run_once = AsyncMock(side_effect=LLMError("No auth type is selected"))

        with patch.object(adapter, "_run_once", run_once), self.assertRaisesRegex(
            LLMError, "No auth type"
        ):
            asyncio.run(adapter._run("prompt", "model", {"local_cli_isolated": True}))

        self.assertEqual(run_once.await_count, 1)

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

    def test_file_prompt_instruction_allows_verified_mcp_writes_when_requested(self):
        instruction = LocalCLIAdapter._file_prompt_instruction(
            r"D:\novels\siming-task.md",
            [],
            allow_mcp=True,
        )
        self.assertIn("允许使用已配置的 Siming MCP", instruction)
        self.assertIn("以文件中的 SYSTEM、当前作用域 ID 和 USER 指令为准", instruction)
        self.assertIn("不要根据通用 MCP 工具目录自行改成作品列表", instruction)
        self.assertNotIn("你是司命项目助手", instruction)
        self.assertIn("写入后再次读取验证", instruction)
        self.assertNotIn("不要调用 Siming MCP", instruction)

    def test_opencode_launch_flattens_task_pointer_for_windows_cmd(self):
        adapter = LocalCLIAdapter(api_key="", base_url="opencode_cli", cli_command="opencode")
        with tempfile.TemporaryDirectory() as directory:
            launch, prompt_file = adapter._opencode_family_launch(
                prompt="[SYSTEM]\nroute the request\n[USER]\nwrite chapter one",
                model=OPENCODE_DEFAULT_MODEL,
                cwd=directory,
                attachments=[],
                allow_mcp=False,
            )
            rendered = " ".join(launch.args)
            self.assertIn(prompt_file, rendered)
            self.assertNotIn("\n", rendered)
            attached = [
                launch.args[index + 1]
                for index, item in enumerate(launch.args[:-1])
                if item == "--file"
            ]
            self.assertNotIn(prompt_file, attached)

    def test_opencode_native_launch_passes_short_prompt_without_read_tool(self):
        adapter = LocalCLIAdapter(api_key="", base_url="opencode_cli", cli_command="opencode")
        with tempfile.TemporaryDirectory() as directory:
            launch, prompt_file = adapter._opencode_family_launch(
                prompt="[SYSTEM]\nroute the request\n[USER]\nwrite chapter one",
                model=OPENCODE_DEFAULT_MODEL,
                cwd=directory,
                attachments=[],
                allow_mcp=False,
                direct_prompt_safe=True,
            )

            rendered = " ".join(launch.args)
            self.assertEqual(prompt_file, "")
            self.assertIn("[SYSTEM] route the request [USER] write chapter one", rendered)
            self.assertNotIn("请读取 UTF-8 任务文件", rendered)
            self.assertNotIn("\n", rendered)

    def test_opencode_pointer_keeps_explicit_source_attachments(self):
        adapter = LocalCLIAdapter(api_key="", base_url="opencode_cli", cli_command="opencode")
        with tempfile.TemporaryDirectory() as directory:
            attachment = Path(directory) / "source.txt"
            attachment.write_text("reference", encoding="utf-8")
            launch, prompt_file = adapter._opencode_family_launch(
                prompt="[SYSTEM]\nroute the request\n[USER]\nwrite chapter one",
                model=OPENCODE_DEFAULT_MODEL,
                cwd=directory,
                attachments=[str(attachment)],
                allow_mcp=False,
            )
            attached = [
                launch.args[index + 1]
                for index, item in enumerate(launch.args[:-1])
                if item == "--file"
            ]

            self.assertEqual(attached, [str(attachment)])
            self.assertNotIn(prompt_file, attached)

    def test_opencode_isolated_read_hides_global_config_and_external_paths(self):
        adapter = LocalCLIAdapter(api_key="", base_url="opencode_cli", cli_command="opencode")
        with tempfile.TemporaryDirectory() as directory:
            _launch, _prompt_file, env = prepare_opencode_launch(
                adapter,
                prompt="读取本轮快照",
                model=OPENCODE_DEFAULT_MODEL,
                cwd=directory,
                attachments=[],
                allow_mcp=False,
                isolated=True,
                permission_granted=False,
            )

            config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
            self.assertFalse(config["mcp"]["siming"]["enabled"])
            self.assertEqual(config["permission"]["*"], "deny")
            self.assertEqual(config["permission"]["read"], "allow")
            self.assertEqual(config["permission"]["external_directory"], "deny")
            self.assertTrue(env["XDG_CONFIG_HOME"].startswith(str(Path(directory).resolve())))
            self.assertEqual(env["OPENCODE_CONFIG_DIR"], env["XDG_CONFIG_HOME"])
            self.assertEqual(env["NO_MCP"], "1")

    def test_opencode_grant_injects_only_process_scoped_siming_mcp(self):
        adapter = LocalCLIAdapter(api_key="", base_url="opencode_cli", cli_command="opencode")
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.ai.local_cli_prompt.resolve_siming_mcp_server",
            return_value={
                "mode": "source",
                "command": r"D:\Siming\python.exe",
                "args": [
                    r"D:\Siming\moshu-mcp-server.py",
                    "--permission-pack",
                    "creation_session",
                    "--creation-session-id",
                    "session-1",
                ],
                "cwd": r"D:\Siming",
            },
        ):
            launch, _prompt_file, env = prepare_opencode_launch(
                adapter,
                prompt="更新目标字数",
                model=OPENCODE_DEFAULT_MODEL,
                cwd=directory,
                attachments=[],
                allow_mcp=True,
                isolated=True,
                permission_granted=True,
                mcp_permission_pack="creation_session",
                mcp_creation_session_id="session-1",
            )

        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(set(config["mcp"]), {"siming_turn"})
        self.assertEqual(
            config["mcp"]["siming_turn"]["command"][-2:],
            ["--creation-session-id", "session-1"],
        )
        self.assertEqual(config["permission"]["*"], "deny")
        self.assertEqual(config["permission"]["siming_turn_*"], "allow")
        self.assertEqual(config["permission"]["external_directory"], "deny")
        self.assertTrue(env["XDG_CONFIG_HOME"].startswith(str(Path(directory).resolve())))
        self.assertEqual(env["OPENCODE_CONFIG_DIR"], env["XDG_CONFIG_HOME"])
        self.assertNotIn("NO_MCP", env)
        self.assertNotIn("--auto", launch.args)
        self.assertNotIn("--dangerously-skip-permissions", launch.args)

    def test_normalize_plain_output_is_preserved(self):
        adapter = LocalCLIAdapter(api_key="", base_url="claude_cli", cli_command="claude")
        self.assertEqual(adapter._normalize_output("plain answer\n"), "plain answer")

    @patch("app.ai.local_cli_adapter.subprocess.CREATE_NO_WINDOW", 0x08000000, create=True)
    @patch("app.ai.local_cli_adapter.os.name", "nt")
    def test_hidden_subprocess_kwargs_hides_windows_console(self):
        kwargs = hidden_subprocess_kwargs()
        self.assertIn("creationflags", kwargs)
        self.assertGreater(kwargs["creationflags"], 0)
