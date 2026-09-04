"""Local CLI adapter for supported local coding-agent CLIs.

This adapter treats local coding-agent CLIs as model executors. It is designed
for short, bounded generation tasks controlled by Siming, not for exposing
Siming secrets or letting the child process own Siming's workflow state.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # packaged builds install psutil; source fallbacks stay usable
    psutil = None

from ..core.exceptions import LLMError
from ..core.legacy_env import get_compatible_env
from .base import BaseAdapter
from .cli_process import hidden_subprocess_kwargs, terminate_cli_process_tree
from .local_cli_output import normalize_cli_output
from .local_cli_prompt import (
    file_prompt_instruction,
    prepare_direct_mcp_launch,
    prepare_long_prompt_launch,
    prepare_opencode_launch,
)
from .local_cli_read_grants import LocalCLIReadGrantError, stage_explicit_read_paths
from .local_cli_models import (
    DEFAULT_CLI_COMMANDS,
    DEFAULT_CLI_MODELS,
    OPENCODE_DEFAULT_MODEL,
    OPENCODE_MODELS,
    OPENCODE_RETIRED_MODELS,
    discover_local_cli_models,
    effective_local_cli_model,
    is_cli_model_sentinel,
    local_cli_model_options,
    preferred_local_cli_model,
)
from .local_cli_monitor import (
    CLIInterruptedError,
    CLIPermissionRequiredError,
    CLIQuotaLimitError,
    CLIStalledError,
    CLITimeoutError,
    CLITurnTerminal,
    communicate_with_cli_quota_detection,
    detect_cli_auth_error,
    detect_cli_permission_request,
    detect_cli_quota_error,
    sample_cli_process_tree,
)

LOCAL_CLI_PROVIDERS = {
    "claude_cli",
    "codex_cli",
    "opencode_cli",
    "mimocode_cli",
    "cursor_cli",
    "kilocode_cli",
    "qwen_code_cli",
    "hermes_cli",
    "openclaw_cli",
    "dsh_cli",
    "custom_cli",
}

DEFAULT_LOCAL_CLI_TIMEOUT = 180
LOCAL_CLI_TIMEOUT_GRACE_SECONDS = 15
DEFAULT_ISOLATED_CLI_ATTEMPTS = 3
MAX_ISOLATED_CLI_ATTEMPTS = 4
TRANSIENT_CLI_ERROR_MARKERS = (
    "certificate verification error",
    "certificate verify failed",
    "connection reset",
    "connection aborted",
    "econnreset",
    "etimedout",
    "temporarily unavailable",
    "service unavailable",
    "temporary failure",
    "stream error",
    "http 408",
    "http 425",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
)

OPENCODE_INCOMPLETE_FINISH_REASONS = frozenset({
    "unknown",
    "tool-calls",
    "tool_calls",
    "length",
})
OPENCODE_RESUME_PROMPT = (
    "上一轮模型连接在完成任务前中断。继续同一个司命任务，不要重新解释任务。"
    "先通过 siming_turn 的 get_creation_snapshot 读取最新 revision："
    "如果目标内容已经写入，禁止重复写入，立即给出最终答复并结束；"
    "如果尚未写入，继续生成并立即通过 MCP 写入，工具返回成功后直接结束。"
)

DEFAULT_CLI_ARGS: dict[str, list[str]] = {
    # Provider-specific task/MCP permissions are injected by the adapter for
    # the isolated child process; the UI does not pause for an extra grant.
    "claude_cli": ["-p", "{prompt}"],
    "codex_cli": ["exec", "{prompt}"],
    "opencode_cli": [
        "run",
        "--pure",
        "--format",
        "json",
        "--model",
        "{model}",
        "{prompt}",
    ],
    "mimocode_cli": ["run", "{prompt}"],
    "cursor_cli": ["-p", "--output-format", "text", "{prompt}"],
    "kilocode_cli": ["run", "{prompt}"],
    "qwen_code_cli": ["--output-format", "text", "{prompt}"],
    "hermes_cli": ["--oneshot", "{prompt}"],
    "openclaw_cli": [
        "agent",
        "--local",
        "--json",
        "--session-key",
        "agent:siming:local-cli",
        "--message",
        "{prompt}",
    ],
    "dsh_cli": ["--profile", "headless", "{prompt}"],
    "custom_cli": ["{prompt}"],
}

UNSAFE_PERMISSION_FLAGS: dict[str, set[str]] = {
    "claude_cli": {"--dangerously-skip-permissions"},
    "codex_cli": {
        "--dangerously-bypass-approvals-and-sandbox",
        "--full-auto",
    },
    "opencode_cli": {"--dangerously-skip-permissions", "--auto"},
    "mimocode_cli": {"--dangerously-skip-permissions"},
    "cursor_cli": {"--force", "--approve-mcps", "--trust"},
    "kilocode_cli": {"--auto"},
    "qwen_code_cli": {"--yolo"},
    "hermes_cli": {"--yolo"},
}
UNSAFE_PERMISSION_OPTIONS: dict[str, dict[str, set[str]]] = {
    "claude_cli": {"--permission-mode": {"bypasspermissions"}},
    "codex_cli": {
        "--ask-for-approval": {"never"},
        "-a": {"never"},
        "--sandbox": {"danger-full-access"},
    },
    "qwen_code_cli": {"--approval-mode": {"yolo"}},
}

STDIN_PROMPT_PROVIDERS = {
    "claude_cli",
    "codex_cli",
    "mimocode_cli",
    "cursor_cli",
    "kilocode_cli",
    "qwen_code_cli",
}
DASH_STDIN_PROMPT_PROVIDERS = {"codex_cli"}
AGENT_FILE_PROMPT_PROVIDERS = LOCAL_CLI_PROVIDERS - {"custom_cli", "codex_cli"}
OPENCODE_FAMILY_PROVIDERS = {"opencode_cli", "mimocode_cli", "kilocode_cli"}
OPENCODE_DIRECT_PROMPT_LIMIT = 8_000
WINDOWS_SAFE_ARG_CHARS = 12000
@dataclass(frozen=True)
class CLILaunch:
    args: list[str]
    stdin_text: str | None = None


@dataclass(frozen=True)
class CLIRunContext:
    command: str
    launch: CLILaunch
    cwd: str
    isolated: bool
    allow_mcp: bool
    mcp_authorized: bool
    env: dict[str, str]
    prompt_file: str | None
    codex_output_file: str | None
    cleanup_codex_output_file: bool
    request_started_at: float
    request_timeout: float | None
    operation_id: str | None


def _unlink_if_exists(path: str | None) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


@dataclass(frozen=True)
class OpenCodeTurnState:
    """Terminal state reported by one ``opencode run --format json`` process."""

    session_id: str = ""
    finish_reason: str = ""
    saw_step_start: bool = False
    saw_step_finish: bool = False

    @property
    def incomplete(self) -> bool:
        if self.finish_reason in OPENCODE_INCOMPLETE_FINISH_REASONS:
            return True
        return self.saw_step_start and not self.saw_step_finish


def is_local_cli_provider(provider: str | None) -> bool:
    return (provider or "").strip().lower() in LOCAL_CLI_PROVIDERS


def ensure_opencode_logging_args(provider: str, args: list[str]) -> None:
    """Make opencode surface provider retry/quota errors on stderr."""
    if provider != "opencode_cli":
        return
    if "--print-logs" not in args:
        args.insert(0, "--print-logs")
    if "--log-level" not in args:
        insert_at = args.index("--print-logs") + 1 if "--print-logs" in args else 0
        args[insert_at:insert_at] = ["--log-level", "WARN"]


def _message_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
            elif item:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def messages_to_prompt(messages: list[dict]) -> str:
    """Convert OpenAI-style messages into a plain prompt for CLI tools."""
    sections: list[str] = []
    for msg in messages:
        role = str(msg.get("role") or "user").upper()
        content = _message_text(msg.get("content"))
        if not content:
            continue
        sections.append(f"[{role}]\n{content}")
    return "\n\n".join(sections).strip()


def parse_cli_args(raw: str | None, provider: str, prompt: str, model: str) -> list[str]:
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                parts = [str(item) for item in parsed]
            else:
                parts = shlex.split(str(raw), posix=False)
        except Exception:
            parts = shlex.split(str(raw), posix=False)
    else:
        parts = DEFAULT_CLI_ARGS.get(provider, ["{prompt}"])

    result: list[str] = []
    for part in parts:
        result.append(part.replace("{prompt}", prompt).replace("{model}", model))
    if not any("{prompt}" in part for part in parts) and prompt not in result:
        result.append(prompt)
    return result


def parse_cli_launch(raw: str | None, provider: str, prompt: str, model: str) -> CLILaunch:
    """Build CLI launch arguments, moving long prompts to stdin when possible."""
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                parts = [str(item) for item in parsed]
            else:
                parts = shlex.split(str(raw), posix=False)
        except Exception:
            parts = shlex.split(str(raw), posix=False)
    else:
        parts = DEFAULT_CLI_ARGS.get(provider, ["{prompt}"])

    can_use_stdin = provider in STDIN_PROMPT_PROVIDERS and "{prompt}" in parts
    use_stdin = can_use_stdin and (
        provider in DASH_STDIN_PROMPT_PROVIDERS or len(prompt) > WINDOWS_SAFE_ARG_CHARS
    )
    result: list[str] = []
    for part in parts:
        if use_stdin and part == "{prompt}":
            if provider in DASH_STDIN_PROMPT_PROVIDERS:
                result.append("-")
            continue
        result.append(part.replace("{prompt}", prompt).replace("{model}", model))
    if use_stdin:
        return CLILaunch(args=result, stdin_text=prompt)
    if not any("{prompt}" in part for part in parts) and prompt not in result:
        result.append(prompt)
    return CLILaunch(args=result)


def _extract_text_from_json_event(data: dict) -> str:
    """Best-effort extraction across Claude/Codex/opencode JSONL variants."""
    if data.get("type") == "error" or "error" in data:
        return ""
    candidates = [
        data.get("delta"),
        data.get("content"),
        data.get("text"),
        data.get("message"),
        data.get("output"),
    ]
    for value in candidates:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            nested = _extract_text_from_json_event(value)
            if nested:
                return nested

    item = data.get("item")
    if isinstance(item, dict):
        nested = _extract_text_from_json_event(item)
        if nested:
            return nested
    part = data.get("part")
    if isinstance(part, dict):
        nested = _extract_text_from_json_event(part)
        if nested:
            return nested

    content = data.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _extract_error_from_json_event(data: dict) -> str:
    if data.get("type") != "error" and "error" not in data:
        return ""
    error = data.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        nested = error.get("data")
        if isinstance(nested, dict):
            return str(nested.get("message") or nested.get("error") or nested)
        return str(error.get("message") or error)
    return str(data.get("message") or data)


def extract_cli_error(text: str) -> str:
    first_error = ""
    has_text = False
    for line in text.splitlines():
        try:
            data = json.loads(line.strip())
        except Exception:
            continue
        if isinstance(data, dict):
            error = _extract_error_from_json_event(data)
            if error:
                first_error = first_error or error
                continue
            if _extract_text_from_json_event(data):
                has_text = True
    return "" if has_text else first_error


def inspect_opencode_turn(text: str) -> OpenCodeTurnState:
    """Read OpenCode's real terminal reason instead of treating exit code 0 as success."""

    session_id = ""
    finish_reason = ""
    saw_step_start = False
    saw_step_finish = False
    for line in text.splitlines():
        try:
            data = json.loads(line.strip())
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        part = data.get("part") if isinstance(data.get("part"), dict) else {}
        session_id = str(
            data.get("sessionID")
            or data.get("session_id")
            or part.get("sessionID")
            or part.get("session_id")
            or session_id
            or ""
        ).strip()
        event_type = str(data.get("type") or "").strip().lower().replace("-", "_")
        part_type = str(part.get("type") or "").strip().lower().replace("-", "_")
        if event_type == "step_start" or part_type == "step_start":
            saw_step_start = True
        if event_type == "step_finish" or part_type == "step_finish":
            saw_step_finish = True
            finish_reason = str(
                part.get("reason")
                or data.get("reason")
                or finish_reason
                or ""
            ).strip().lower().replace("_", "-")
    return OpenCodeTurnState(
        session_id=session_id,
        finish_reason=finish_reason,
        saw_step_start=saw_step_start,
        saw_step_finish=saw_step_finish,
    )


_OPENCODE_LOG_ERROR_RE = re.compile(r'error\.error="((?:\\.|[^"\\])*)"')


def extract_cli_runtime_error(*texts: str) -> str:
    """Extract provider failures that OpenCode logs while still exiting with code 0."""

    for text in texts:
        for line in str(text or "").splitlines():
            lowered = line.lower()
            if "level=error" not in lowered:
                continue
            if not any(marker in lowered for marker in (
                "stream error",
                "ai_apicallerror",
                "ai_retryerror",
                "service unavailable",
                "upstream request failed",
            )):
                continue
            match = _OPENCODE_LOG_ERROR_RE.search(line)
            detail = match.group(1) if match else line.strip()
            try:
                detail = json.loads(f'"{detail}"')
            except Exception:
                detail = detail.replace(r'\"', '"').replace(r"\\", "\\")
            return str(detail)[:1000]
    return ""


class LocalCLIAdapter(BaseAdapter):
    """Adapter for local agent CLIs used as text generation backends."""

    @property
    def provider_name(self) -> str:
        return "local_cli"

    @property
    def _provider(self) -> str:
        return (self.base_url or "").strip() or "custom_cli"

    def _command(self) -> str:
        command = (self.cli_command or DEFAULT_CLI_COMMANDS.get(self._provider) or "").strip()
        if not command:
            raise LLMError("本机 CLI 提供商未配置命令路径")
        resolved = shutil.which(command) or (command if os.path.exists(command) else None)
        if not resolved:
            raise LLMError(f"未找到本机 CLI 命令: {command}")
        if self._provider == "opencode_cli":
            # npm exposes OpenCode through .cmd/.ps1 launchers on Windows. A
            # native binary sits beside the package and is safer for direct
            # argv prompts: model/user text can never be reinterpreted by cmd.
            resolved_path = Path(resolved)
            native = (
                resolved_path.parent
                / "node_modules"
                / "opencode-ai"
                / "bin"
                / "opencode.exe"
            )
            if native.is_file():
                resolved = str(native)
        return resolved

    def _args(self, prompt: str, model: str) -> list[str]:
        return parse_cli_args(self.cli_args, self._provider, prompt, model)

    def _launch(self, prompt: str, model: str) -> CLILaunch:
        return parse_cli_launch(self.cli_args, self._provider, prompt, model)

    @staticmethod
    def _runtime_cwd(extra_body: dict | None) -> str:
        if bool((extra_body or {}).get("local_cli_isolated")):
            # CLI-as-model execution never needs a project checkout. An empty
            # per-call directory prevents accidental repository/project scans.
            managed_cwd = str((extra_body or {}).get("_local_cli_isolated_cwd") or "").strip()
            if managed_cwd:
                return managed_cwd
            return tempfile.mkdtemp(prefix="siming-cli-isolated-")
        requested = str((extra_body or {}).get("local_cli_cwd") or "").strip()
        candidates = [
            requested,
            get_compatible_env("SIMING_CONTENT_ROOT"),
            str(Path(get_compatible_env("SIMING_HOME", default=tempfile.gettempdir())) / "projects"),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            path = Path(candidate).expanduser()
            try:
                path.mkdir(parents=True, exist_ok=True)
                return str(path.resolve())
            except OSError:
                continue
        fallback = Path(tempfile.gettempdir()) / "siming-cli-workspace"
        fallback.mkdir(parents=True, exist_ok=True)
        return str(fallback.resolve())

    def _runtime_attachments(self, extra_body: dict | None, cwd: str) -> list[str]:
        body = extra_body or {}
        attachments: list[str] = []
        raw = [] if bool(body.get("local_cli_isolated")) else body.get("local_cli_attachments") or []
        if isinstance(raw, str):
            raw = [raw]
        for value in raw:
            path = Path(str(value)).expanduser()
            if path.exists() and path.is_file():
                attachments.append(str(path.resolve()))

        if bool(body.get("local_cli_read_permission_granted")):
            if self._provider != "opencode_cli":
                raise LLMError("当前安全的本地路径只读授权仅支持 OpenCode")
            read_paths = body.get("local_cli_read_paths") or []
            if isinstance(read_paths, str):
                read_paths = [read_paths]
            try:
                attachments.extend(stage_explicit_read_paths(read_paths, cwd))
            except LocalCLIReadGrantError as exc:
                raise LLMError(f"本地路径未授权：{exc}") from exc
            except OSError as exc:
                raise LLMError(f"创建本轮只读快照失败：{exc}") from exc
        return attachments

    @staticmethod
    def _isolated_environment(base: dict[str, str], isolated: bool) -> dict[str, str]:
        """Disable ambient Agent integrations for CLI-as-model execution."""
        if not isolated:
            return base
        env = dict(base)
        env["SIMING_LOCAL_CLI_ISOLATED"] = "1"
        # Providers that recognize one of these flags disable their MCP loader;
        # unrecognized variables are harmless. The empty cwd and prompt rules
        # remain the provider-independent safety boundary.
        env["SIMING_DISABLE_MCP"] = "1"
        env["MCP_DISABLE"] = "1"
        env["NO_MCP"] = "1"
        env["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
        env["CLAUDE_CODE_DISABLE_MCP"] = "1"
        env["CODEX_DISABLE_MCP"] = "1"
        return env

    @staticmethod
    def _cleanup_isolated_workspace(cwd: str, isolated: bool) -> None:
        if not isolated:
            return
        target = Path(cwd).resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        if target.parent != temp_root or not target.name.startswith("siming-cli-isolated-"):
            return

        def _remove_readonly(function: Callable[..., Any], path: str, _error: Any) -> None:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            function(path)

        with suppress(OSError):
            shutil.rmtree(target, onerror=_remove_readonly)

    @staticmethod
    def _write_prompt_file(prompt: str, cwd: str, provider: str) -> str:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".md",
            prefix=f"siming-{provider}-task-",
            dir=cwd,
            delete=False,
        ) as handle:
            handle.write(prompt)
            return handle.name

    @staticmethod
    def _file_prompt_instruction(
        prompt_file: str,
        attachments: list[str],
        *,
        allow_mcp: bool = False,
    ) -> str:
        return file_prompt_instruction(prompt_file, attachments, allow_mcp=allow_mcp)

    @staticmethod
    def _opencode_env(cwd: str | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
        env["OPENCODE_PURE"] = "1"
        if cwd:
            config_root = str((Path(cwd) / ".siming-opencode-config").resolve())
            Path(config_root).mkdir(parents=True, exist_ok=True)
            env["XDG_CONFIG_HOME"] = config_root
            env["OPENCODE_CONFIG_DIR"] = config_root
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps({
            "mcp": {
                "siming": {
                    "type": "local",
                    "command": ["cmd", "/c", "exit", "0"],
                    "enabled": False,
                }
            },
            # Internal model execution must be side-effect free. The complete
            # prompt and source documents are attached before the run. Some
            # OpenCode models still choose the read tool for attachments, so
            # allow read-only access while keeping writes, shell, web, and MCP
            # disabled.
            "permission": {
                "*": "deny",
                "read": "allow",
                "external_directory": "deny",
            },
        }, ensure_ascii=False)
        return env

    @staticmethod
    def _ensure_opencode_option(args: list[str], flag: str, value: str | None = None) -> None:
        if flag in args:
            return
        insert_at = 1 if args and args[0] == "run" else 0
        args.insert(insert_at, flag)
        if value is not None:
            args.insert(insert_at + 1, value)

    @staticmethod
    def _insert_before_prompt(args: list[str], values: list[str]) -> None:
        insert_at = max(0, len(args) - 1)
        args[insert_at:insert_at] = values

    @staticmethod
    def _strip_ungranted_permission_args(provider: str, args: list[str]) -> list[str]:
        """Remove auto-approval flags, including values saved by older releases."""

        flags = UNSAFE_PERMISSION_FLAGS.get(provider, set())
        options = UNSAFE_PERMISSION_OPTIONS.get(provider, {})
        cleaned: list[str] = []
        index = 0
        while index < len(args):
            token = args[index]
            lowered = token.lower()
            if lowered in {flag.lower() for flag in flags}:
                index += 1
                continue
            option_removed = False
            for option, unsafe_values in options.items():
                option_lower = option.lower()
                if lowered == option_lower and index + 1 < len(args):
                    value = args[index + 1].lower()
                    if value in unsafe_values:
                        index += 2
                        option_removed = True
                        break
                prefix = option_lower + "="
                if lowered.startswith(prefix) and lowered[len(prefix):] in unsafe_values:
                    index += 1
                    option_removed = True
                    break
            if option_removed:
                continue
            cleaned.append(token)
            index += 1
        return cleaned

    def _apply_permission_mode(self, args: list[str], permission_granted: bool) -> None:
        provider = self._provider
        args[:] = self._strip_ungranted_permission_args(provider, args)
        if not permission_granted:
            return
        if provider == "claude_cli":
            self._insert_before_prompt(args, ["--dangerously-skip-permissions"])
        elif provider == "codex_cli":
            self._insert_before_prompt(args, ["--dangerously-bypass-approvals-and-sandbox"])
        elif provider == "opencode_cli":
            # A granted OpenCode turn receives an explicit process-scoped
            # permission map. Do not widen it with --auto (and current
            # OpenCode no longer accepts --dangerously-skip-permissions).
            return
        elif provider == "mimocode_cli":
            self._ensure_opencode_option(args, "--dangerously-skip-permissions")
        elif provider == "cursor_cli":
            for flag in ("--force", "--approve-mcps", "--trust"):
                self._insert_before_prompt(args, [flag])
        elif provider == "kilocode_cli":
            self._ensure_opencode_option(args, "--auto")
        elif provider == "qwen_code_cli":
            self._insert_before_prompt(args, ["--approval-mode", "yolo"])
        elif provider == "hermes_cli":
            self._insert_before_prompt(args, ["--yolo"])

    @staticmethod
    def _codex_output_last_message_path(args: list[str], cwd: str) -> str | None:
        for index, value in enumerate(args):
            if value in {"--output-last-message", "-o"} and index + 1 < len(args):
                path = Path(args[index + 1])
                if not path.is_absolute():
                    path = Path(cwd) / path
                return str(path)
        return None

    def _ensure_codex_output_file(self, args: list[str], cwd: str) -> tuple[str, bool]:
        existing = self._codex_output_last_message_path(args, cwd)
        if existing:
            return existing, False
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".txt",
            prefix="siming-codex-output-",
            dir=cwd,
            delete=False,
        )
        try:
            output_file = handle.name
        finally:
            handle.close()
        self._insert_before_prompt(args, ["--output-last-message", output_file])
        return output_file, True

    def _apply_provider_runtime_options(
        self,
        args: list[str],
        *,
        model: str,
        cwd: str,
        permission_granted: bool = False,
    ) -> None:
        provider = self._provider
        self._apply_permission_mode(args, permission_granted)
        if not is_cli_model_sentinel(provider, model) and "--model" not in args and "-m" not in args:
            if provider == "openclaw_cli" and "--message" in args:
                args[args.index("--message"):args.index("--message")] = ["--model", model]
            else:
                self._insert_before_prompt(args, ["--model", model])
        if provider == "codex_cli":
            if "--cd" not in args and "-C" not in args:
                self._insert_before_prompt(args, ["--cd", cwd])
            if "--skip-git-repo-check" not in args:
                self._insert_before_prompt(args, ["--skip-git-repo-check"])
            if "--ephemeral" not in args:
                self._insert_before_prompt(args, ["--ephemeral"])
        elif provider == "cursor_cli":
            if "--workspace" not in args:
                self._insert_before_prompt(args, ["--workspace", cwd])
        elif provider == "qwen_code_cli":
            if "--include-directories" not in args and "--add-dir" not in args:
                self._insert_before_prompt(args, ["--include-directories", cwd])
        elif provider == "openclaw_cli" and "--session-key" not in args:
            insert_at = args.index("--message") if "--message" in args else max(0, len(args) - 1)
            args[insert_at:insert_at] = ["--session-key", "agent:siming:local-cli"]

    @classmethod
    def _apply_codex_writing_options(cls, args: list[str], task_type: str) -> None:
        """Keep prose transforms fast and less over-deliberated in Codex CLI."""

        if str(task_type or "").strip().lower() != "writing":
            return
        if any("model_reasoning_effort" in token for token in args):
            return
        cls._insert_before_prompt(args, ["-c", 'model_reasoning_effort="low"'])

    def _opencode_family_launch(
        self,
        *,
        prompt: str,
        model: str,
        cwd: str,
        attachments: list[str],
        allow_mcp: bool = False,
        permission_granted: bool = False,
        direct_prompt_safe: bool = False,
    ) -> tuple[CLILaunch, str]:
        execution_model = effective_local_cli_model(self._provider, model)
        flattened_prompt = " ".join(
            part.strip() for part in prompt.splitlines() if part.strip()
        )
        direct_prompt = (
            self._provider == "opencode_cli"
            and direct_prompt_safe
            and len(flattened_prompt) <= OPENCODE_DIRECT_PROMPT_LIMIT
        )
        if direct_prompt:
            # Short isolated model calls do not need an Agent read-tool turn.
            # Supplying the flattened prompt to the native .exe avoids empty
            # read-only turns and prevents tool diagnostics from leaking into
            # generated prose.
            prompt_file = ""
            instruction = flattened_prompt
        else:
            prompt_file = self._write_prompt_file(prompt, cwd, self._provider)
            instruction = self._file_prompt_instruction(
                prompt_file,
                attachments,
                allow_mcp=allow_mcp,
            )
            # Script launchers cannot safely carry embedded newlines. Keep the
            # complete task in UTF-8 and flatten only the pointer instruction.
            instruction = " ".join(
                part.strip() for part in instruction.splitlines() if part.strip()
            )
        launch = self._launch(instruction, execution_model)
        args = list(launch.args)
        self._apply_permission_mode(args, permission_granted)
        self._ensure_opencode_option(args, "--pure")
        self._ensure_opencode_option(args, "--format", "json")
        self._ensure_opencode_option(args, "--dir", cwd)
        if not is_cli_model_sentinel(self._provider, execution_model):
            self._ensure_opencode_option(args, "--model", execution_model)
        ensure_opencode_logging_args(self._provider, args)
        # OpenCode already receives a flattened pointer to the task file.  On
        # long Chinese prompts, attaching that same file with ``--file`` as
        # well can make current OpenCode builds end the turn with zero tokens
        # and no error.  Keep the task inside the isolated cwd and let the
        # explicitly allowed read tool load it once.  User-authorized source
        # attachments remain real ``--file`` parts.  Other OpenCode-family
        # CLIs retain their existing attachment behavior.
        attached_paths = attachments if self._provider == "opencode_cli" else [prompt_file, *attachments]
        for path in attached_paths:
            args.extend(["--file", path])
        return CLILaunch(args=args), prompt_file

    def _opencode_resume_launch(
        self,
        *,
        model: str,
        cwd: str,
        session_id: str,
        permission_granted: bool,
    ) -> CLILaunch:
        """Continue the exact OpenCode session after an incomplete provider stream."""

        launch = self._launch(OPENCODE_RESUME_PROMPT, model)
        args = list(launch.args)
        self._apply_permission_mode(args, permission_granted)
        self._ensure_opencode_option(args, "--pure")
        self._ensure_opencode_option(args, "--format", "json")
        self._ensure_opencode_option(args, "--dir", cwd)
        self._ensure_opencode_option(args, "--session", session_id)
        if not is_cli_model_sentinel(self._provider, model):
            self._ensure_opencode_option(args, "--model", model)
        ensure_opencode_logging_args(self._provider, args)
        return CLILaunch(args=args, stdin_text=launch.stdin_text)

    @staticmethod
    def _timeout_seconds(extra_body: dict | None) -> float | None:
        body = extra_body or {}
        raw = body.get("local_cli_timeout_seconds", DEFAULT_LOCAL_CLI_TIMEOUT)
        if "local_cli_timeout_seconds" in body and raw in (None, 0, "0", "none", "unbounded"):
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return float(DEFAULT_LOCAL_CLI_TIMEOUT)
        return value if value > 0 else float(DEFAULT_LOCAL_CLI_TIMEOUT)

    @staticmethod
    def _terminal_turn_probe(runtime_body: dict[str, Any]) -> Callable[[], Any] | None:
        from ..services.workspace.terminal_draft_detection import (
            local_cli_terminal_draft_probe,
        )

        draft_probe = local_cli_terminal_draft_probe(runtime_body)
        category_file = str(
            runtime_body.get("local_cli_mcp_tool_category_state_file") or ""
        ).strip() if runtime_body.get("local_cli_mcp_authorized") else ""
        if not category_file:
            return draft_probe

        def probe() -> str | None:
            from ..services.tool_category_state import read_tool_category_state

            state = read_tool_category_state(category_file)
            if state["version"] > state["active_version"]:
                # The model has committed a replacement category set. Its
                # current process cannot see the next step's tools, so stop
                # now instead of waiting for misleading trailing model text.
                return f"set_tool_categories:{state['version']}"
            return draft_probe() if draft_probe is not None else None

        return probe

    @staticmethod
    def _creation_revision_activity_probe(
        runtime_body: dict[str, Any],
    ) -> Callable[[], Any] | None:
        session_id = str(
            runtime_body.get("local_cli_mcp_creation_session_id") or ""
        ).strip()
        if not session_id:
            return None

        def _probe() -> str | None:
            from ..database.models import NovelCreationSession
            from ..database.session import SessionLocal

            session = SessionLocal()
            try:
                revision = session.query(NovelCreationSession.revision).filter(
                    NovelCreationSession.id == session_id,
                ).scalar()
                return f"creation_revision:{revision}" if revision is not None else None
            finally:
                session.close()

        return _probe

    @staticmethod
    def _activity_window(runtime_body: dict[str, Any], key: str) -> float | None:
        raw = runtime_body.get(key)
        if raw in (None, ""):
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @staticmethod
    def _resume_incomplete_opencode(extra_body: dict[str, Any]) -> bool:
        raw = extra_body.get("local_cli_resume_incomplete_opencode", False)
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return bool(raw)

    async def _continue_incomplete_opencode_turn(
        self,
        *,
        command: str,
        model: str,
        cwd: str,
        env: dict[str, str],
        session_id: str,
        timeout_seconds: float | None,
        operation_id: str | None,
        mcp_authorized: bool,
        allow_mcp: bool,
        runtime_body: dict[str, Any],
    ) -> str:
        launch = self._opencode_resume_launch(
            model=model,
            cwd=cwd,
            session_id=session_id,
            permission_granted=mcp_authorized and not allow_mcp,
        )
        try:
            process = await asyncio.create_subprocess_exec(
                command,
                *launch.args,
                stdin=asyncio.subprocess.PIPE if launch.stdin_text is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                **hidden_subprocess_kwargs(),
            )
        except OSError as exc:
            raise LLMError(f"继续 OpenCode 会话失败: {exc}") from exc

        try:
            stdout, stderr = await communicate_with_cli_quota_detection(
                process,
                input_bytes=(
                    launch.stdin_text.encode("utf-8")
                    if launch.stdin_text is not None else None
                ),
                timeout_seconds=timeout_seconds,
                operation_id=operation_id,
                external_activity_probe=self._creation_revision_activity_probe(runtime_body),
                quiet_seconds=self._activity_window(runtime_body, "local_cli_quiet_seconds"),
                suspected_stall_seconds=self._activity_window(
                    runtime_body,
                    "local_cli_suspected_stall_seconds",
                ),
                stalled_seconds=self._activity_window(runtime_body, "local_cli_stalled_seconds"),
                stop_on_permission_request=not mcp_authorized,
            )
        except CLIQuotaLimitError as exc:
            raise LLMError(str(exc)) from exc
        except (CLITimeoutError, CLIStalledError) as exc:
            raise LLMError(str(exc)) from exc

        out_text = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()
        result = self._normalize_output(out_text)
        auth_error = detect_cli_auth_error(err_text, extract_cli_error(out_text), out_text)
        if auth_error:
            raise LLMError(auth_error)
        quota_error = detect_cli_quota_error(err_text, out_text)
        if quota_error:
            raise LLMError(quota_error)
        if process.returncode != 0:
            detail = err_text or out_text or f"exit code {process.returncode}"
            raise LLMError(f"本机 CLI 调用失败: {detail}")
        event_error = extract_cli_error(out_text)
        if event_error:
            raise LLMError(f"本机 CLI 调用失败: {event_error}")
        runtime_error = extract_cli_runtime_error(err_text, out_text) if not result else ""
        if runtime_error:
            raise LLMError(f"本机 CLI 调用失败: {runtime_error}")
        state = inspect_opencode_turn(out_text)
        if state.incomplete:
            raise LLMError(
                "OpenCode 模型连接在生成过程中再次中断，未收到完整结束信号；"
                "司命没有把未完成内容当成成功结果"
            )
        return result

    def _prepare_run_context(
        self,
        prompt: str,
        model: str,
        runtime_body: dict[str, Any],
    ) -> CLIRunContext:
        mcp_authorized = bool(runtime_body.get("local_cli_mcp_authorized"))
        if not mcp_authorized:
            runtime_body["local_cli_isolated"] = True
            runtime_body["local_cli_allow_mcp"] = False
        command = self._command()
        cwd = self._runtime_cwd(runtime_body)
        isolated = bool(runtime_body.get("local_cli_isolated"))
        attachments = self._runtime_attachments(runtime_body, cwd)
        allow_mcp = mcp_authorized and bool(runtime_body.get("local_cli_allow_mcp"))
        env = self._isolated_environment(os.environ.copy(), isolated)
        prompt_file: str | None = None
        codex_output_file: str | None = None
        cleanup_codex_output_file = False
        try:
            if self._provider in OPENCODE_FAMILY_PROVIDERS:
                launch, prompt_file, env = prepare_opencode_launch(
                    self,
                    prompt=prompt,
                    model=model,
                    cwd=cwd,
                    attachments=attachments,
                    allow_mcp=allow_mcp,
                    isolated=isolated,
                    permission_granted=mcp_authorized and not allow_mcp,
                    direct_prompt_safe=(
                        self._provider == "opencode_cli"
                        and Path(command).suffix.lower() == ".exe"
                    ),
                    mcp_permission_pack=str(
                        runtime_body.get("local_cli_mcp_permission_pack")
                        or "readonly_collaboration"
                    ),
                    mcp_project_id=str(runtime_body.get("local_cli_mcp_project_id") or ""),
                    mcp_creation_session_id=str(
                        runtime_body.get("local_cli_mcp_creation_session_id") or ""
                    ),
                    mcp_tool_category_state_file=str(
                        runtime_body.get("local_cli_mcp_tool_category_state_file") or ""
                    ),
                    mcp_direct_mcp_lease_token=str(
                        runtime_body.get("local_cli_mcp_lease_token") or ""
                    ),
                )
            elif self._provider == "codex_cli":
                launch = self._launch(prompt, model)
                args = list(launch.args)
                self._apply_provider_runtime_options(
                    args,
                    model=model,
                    cwd=cwd,
                    permission_granted=mcp_authorized and not allow_mcp,
                )
                self._apply_codex_writing_options(
                    args,
                    str(runtime_body.get("moshu_task_type") or ""),
                )
                codex_output_file, cleanup_codex_output_file = (
                    self._ensure_codex_output_file(args, cwd)
                )
                launch = CLILaunch(args=args, stdin_text=launch.stdin_text)
            elif self._provider in AGENT_FILE_PROMPT_PROVIDERS:
                prompt_file = self._write_prompt_file(prompt, cwd, self._provider)
                launch_prompt = self._file_prompt_instruction(
                    prompt_file,
                    attachments,
                    allow_mcp=allow_mcp,
                )
                launch = self._launch(launch_prompt, model)
                args = list(launch.args)
                self._apply_provider_runtime_options(
                    args,
                    model=model,
                    cwd=cwd,
                    permission_granted=mcp_authorized and not allow_mcp,
                )
                launch = CLILaunch(args=args, stdin_text=launch.stdin_text)
            elif (
                len(prompt) > WINDOWS_SAFE_ARG_CHARS
                and self._provider not in STDIN_PROMPT_PROVIDERS
            ):
                launch, prompt_file = prepare_long_prompt_launch(self, prompt, model)
            else:
                launch = self._launch(prompt, model)
            if allow_mcp and self._provider not in OPENCODE_FAMILY_PROVIDERS:
                launch, env = prepare_direct_mcp_launch(
                    self,
                    launch,
                    cwd=cwd,
                    env=env,
                    permission_pack=str(
                        runtime_body.get("local_cli_mcp_permission_pack")
                        or "readonly_collaboration"
                    ),
                    project_id=str(runtime_body.get("local_cli_mcp_project_id") or ""),
                    creation_session_id=str(
                        runtime_body.get("local_cli_mcp_creation_session_id") or ""
                    ),
                    tool_category_state_file=str(
                        runtime_body.get("local_cli_mcp_tool_category_state_file") or ""
                    ),
                    direct_mcp_lease_token=str(
                        runtime_body.get("local_cli_mcp_lease_token") or ""
                    ),
                )
        except ValueError as exc:
            _unlink_if_exists(prompt_file)
            _unlink_if_exists(codex_output_file if cleanup_codex_output_file else None)
            raise LLMError(str(exc)) from exc
        return CLIRunContext(
            command=command,
            launch=launch,
            cwd=cwd,
            isolated=isolated,
            allow_mcp=allow_mcp,
            mcp_authorized=mcp_authorized,
            env=env,
            prompt_file=prompt_file,
            codex_output_file=codex_output_file,
            cleanup_codex_output_file=cleanup_codex_output_file,
            request_started_at=time.monotonic(),
            request_timeout=self._timeout_seconds(runtime_body),
            operation_id=str(runtime_body.get("operation_id") or "") or None,
        )

    @staticmethod
    async def _spawn_run_process(context: CLIRunContext) -> asyncio.subprocess.Process:
        try:
            return await asyncio.create_subprocess_exec(
                context.command,
                *context.launch.args,
                stdin=(
                    asyncio.subprocess.PIPE
                    if context.launch.stdin_text is not None else None
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=context.cwd,
                env=context.env,
                **hidden_subprocess_kwargs(),
            )
        except OSError as exc:
            raise LLMError(f"启动本机 CLI 失败: {exc}") from exc

    async def _collect_run_output(
        self,
        context: CLIRunContext,
        process: asyncio.subprocess.Process,
        runtime_body: dict[str, Any],
    ) -> tuple[bytes, bytes, str | None]:
        stdin_bytes = (
            context.launch.stdin_text.encode("utf-8")
            if context.launch.stdin_text is not None else None
        )
        try:
            stdout, stderr = await communicate_with_cli_quota_detection(
                process,
                input_bytes=stdin_bytes,
                timeout_seconds=context.request_timeout,
                operation_id=context.operation_id,
                external_activity_probe=self._creation_revision_activity_probe(runtime_body),
                terminal_probe=self._terminal_turn_probe(runtime_body),
                quiet_seconds=self._activity_window(
                    runtime_body,
                    "local_cli_quiet_seconds",
                ),
                suspected_stall_seconds=self._activity_window(
                    runtime_body,
                    "local_cli_suspected_stall_seconds",
                ),
                stalled_seconds=self._activity_window(
                    runtime_body,
                    "local_cli_stalled_seconds",
                ),
                stop_on_permission_request=not context.mcp_authorized,
            )
            return stdout, stderr, None
        except CLITurnTerminal as exc:
            return (
                exc.stdout.encode("utf-8"),
                exc.stderr.encode("utf-8"),
                str(exc),
            )
        except CLIPermissionRequiredError:
            raise
        except CLIQuotaLimitError as exc:
            raise LLMError(str(exc)) from exc
        except (CLITimeoutError, CLIStalledError) as exc:
            raise LLMError(str(exc)) from exc

    @staticmethod
    def _codex_file_result(context: CLIRunContext, returncode: int | None) -> str:
        if not context.codex_output_file or returncode != 0:
            return ""
        try:
            return Path(context.codex_output_file).read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            return ""

    async def _finalize_run_output(
        self,
        context: CLIRunContext,
        process: asyncio.subprocess.Process,
        stdout: bytes,
        stderr: bytes,
        terminal_reason: str | None,
        model: str,
        runtime_body: dict[str, Any],
    ) -> str:
        out_text = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()
        event_error = extract_cli_error(out_text)
        auth_error = (
            None
            if terminal_reason
            else detect_cli_auth_error(err_text, event_error, out_text)
        )
        if auth_error:
            raise LLMError(auth_error)
        if terminal_reason:
            self._cleanup_isolated_workspace(context.cwd, context.isolated)
            kind = terminal_reason.partition(":")[0]
            messages = {
                "set_tool_categories": "工具类别已切换，继续下一模型步骤。",
                "save_external_chapter_draft": "章节草稿已生成，等待作者保存。",
                "save_external_outline_draft": "大纲草稿已生成，等待作者确认。",
            }
            if kind not in messages:
                raise LLMError("本机 CLI 返回了未知的回合终止边界")
            return messages[kind]
        if file_text := self._codex_file_result(context, process.returncode):
            self._cleanup_isolated_workspace(context.cwd, context.isolated)
            return file_text
        quota_error = detect_cli_quota_error(err_text, event_error, out_text)
        if quota_error:
            raise LLMError(quota_error)
        if process.returncode != 0:
            detail = err_text or out_text or f"exit code {process.returncode}"
            raise LLMError(f"本机 CLI 调用失败: {detail}")
        if event_error:
            raise LLMError(f"本机 CLI 调用失败: {event_error}")
        result = self._normalize_output(out_text)
        runtime_error = extract_cli_runtime_error(
            err_text,
            out_text,
        ) if not result else ""
        if runtime_error:
            raise LLMError(f"本机 CLI 调用失败: {runtime_error}")
        if self._provider == "opencode_cli":
            state = inspect_opencode_turn(out_text)
            if state.incomplete:
                remaining = (
                    context.request_timeout
                    - (time.monotonic() - context.request_started_at)
                    if context.request_timeout is not None else None
                )
                can_resume = (
                    self._resume_incomplete_opencode(runtime_body)
                    and bool(state.session_id)
                    and (remaining is None or remaining > 5)
                )
                if not can_resume:
                    raise LLMError(
                        "OpenCode 模型连接在生成过程中中断，未收到完整结束信号；"
                        "司命没有把未完成内容当成成功结果"
                    )
                result = await self._continue_incomplete_opencode_turn(
                    command=context.command,
                    model=effective_local_cli_model(self._provider, model),
                    cwd=context.cwd,
                    env=context.env,
                    session_id=state.session_id,
                    timeout_seconds=(
                        float(remaining) if remaining is not None else None
                    ),
                    operation_id=context.operation_id,
                    mcp_authorized=context.mcp_authorized,
                    allow_mcp=context.allow_mcp,
                    runtime_body=runtime_body,
                )
        self._cleanup_isolated_workspace(context.cwd, context.isolated)
        return result

    async def _run_once(
        self,
        prompt: str,
        model: str,
        extra_body: dict | None = None,
    ) -> str:
        runtime_body = dict(extra_body or {})
        context = self._prepare_run_context(prompt, model, runtime_body)
        try:
            process = await self._spawn_run_process(context)
            stdout, stderr, terminal_reason = await self._collect_run_output(
                context,
                process,
                runtime_body,
            )
            return await self._finalize_run_output(
                context,
                process,
                stdout,
                stderr,
                terminal_reason,
                model,
                runtime_body,
            )
        finally:
            _unlink_if_exists(context.prompt_file)
            _unlink_if_exists(
                context.codex_output_file
                if context.cleanup_codex_output_file else None
            )


    @staticmethod
    def _isolated_retry_attempts(extra_body: dict | None) -> int:
        body = extra_body or {}
        if bool(body.get("local_cli_mcp_authorized")):
            return 1
        if not bool(body.get("local_cli_isolated")):
            return 1
        raw = body.get("local_cli_retry_attempts", DEFAULT_ISOLATED_CLI_ATTEMPTS)
        try:
            attempts = int(raw)
        except (TypeError, ValueError):
            attempts = DEFAULT_ISOLATED_CLI_ATTEMPTS
        return max(1, min(attempts, MAX_ISOLATED_CLI_ATTEMPTS))

    @staticmethod
    def _is_transient_cli_failure(error: BaseException) -> bool:
        detail = str(error).lower()
        return any(marker in detail for marker in TRANSIENT_CLI_ERROR_MARKERS)

    async def _run(
        self,
        prompt: str,
        model: str,
        extra_body: dict | None = None,
    ) -> str:
        base_body = dict(extra_body or {})
        if not bool(base_body.get("local_cli_mcp_authorized")):
            base_body["local_cli_isolated"] = True
            base_body["local_cli_allow_mcp"] = False
        attempts = self._isolated_retry_attempts(base_body)
        for attempt in range(attempts):
            attempt_body = dict(base_body)
            isolated_cwd: str | None = None
            if bool(attempt_body.get("local_cli_isolated")):
                isolated_cwd = tempfile.mkdtemp(prefix="siming-cli-isolated-")
                attempt_body["_local_cli_isolated_cwd"] = isolated_cwd
            try:
                return await self._run_once(prompt, model, attempt_body)
            except LLMError as exc:
                if attempt + 1 >= attempts or not self._is_transient_cli_failure(exc):
                    raise
                await asyncio.sleep(min(2 ** attempt, 4))
            finally:
                if isolated_cwd:
                    self._cleanup_isolated_workspace(isolated_cwd, True)
        raise LLMError("本机 CLI 调用失败")

    def _normalize_output(self, text: str) -> str:
        return normalize_cli_output(text, _extract_text_from_json_event)

    async def chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        extra_body: dict | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
    ) -> dict:
        prompt = messages_to_prompt(messages)
        content = await self._run(prompt, model, extra_body)
        return {
            "content": content,
            "model": model,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "tool_calls": None,
        }

    async def stream_chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        extra_body: dict | None = None,
    ) -> AsyncGenerator[str, None]:
        # Most CLIs buffer output until the model turn ends. Yield the final
        # text as one chunk so callers still use the streaming endpoint safely.
        self.last_stream_finish_reason = None
        result = await self.chat_completion(messages, model, temperature, max_tokens, extra_body)
        if result["content"]:
            yield result["content"]
        self.last_stream_finish_reason = "stop"

    async def stream_chat_completion_with_tools(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        extra_body: dict | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
    ) -> AsyncGenerator[dict, None]:
        result = await self.chat_completion(messages, model, temperature, max_tokens, extra_body)
        if result["content"]:
            yield {"type": "content_delta", "delta": result["content"]}
        yield {"type": "done", "finish_reason": "stop", "usage": result["usage"]}
