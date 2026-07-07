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
import subprocess
import tempfile
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .base import BaseAdapter
from ..core.exceptions import LLMError


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
    "custom_cli",
}

DEFAULT_LOCAL_CLI_TIMEOUT = 180

DEFAULT_CLI_COMMANDS: dict[str, str] = {
    "claude_cli": "claude",
    "codex_cli": "codex",
    "opencode_cli": "opencode",
    "mimocode_cli": "mimo",
    "cursor_cli": "agent",
    "kilocode_cli": "kilo",
    "qwen_code_cli": "qwen",
    "hermes_cli": "hermes",
    "openclaw_cli": "openclaw",
    "custom_cli": "",
}

DEFAULT_CLI_ARGS: dict[str, list[str]] = {
    # Claude Code is used as a trusted local worker inside Siming. Bypass
    # interactive permission prompts so file reads and Siming MCP tool calls can
    # run unattended while Siming still enforces its own MCP permission boundary.
    "claude_cli": ["--permission-mode", "bypassPermissions", "-p", "{prompt}"],
    "codex_cli": ["exec", "--dangerously-bypass-approvals-and-sandbox", "{prompt}"],
    "opencode_cli": [
        "run",
        "--pure",
        "--dangerously-skip-permissions",
        "--format",
        "json",
        "--model",
        "{model}",
        "{prompt}",
    ],
    "mimocode_cli": ["run", "--dangerously-skip-permissions", "{prompt}"],
    "cursor_cli": ["-p", "--force", "--approve-mcps", "--trust", "--output-format", "text", "{prompt}"],
    "kilocode_cli": ["run", "--auto", "{prompt}"],
    "qwen_code_cli": ["--approval-mode", "yolo", "--output-format", "text", "{prompt}"],
    "hermes_cli": ["--yolo", "--oneshot", "{prompt}"],
    "openclaw_cli": [
        "agent",
        "--local",
        "--json",
        "--session-key",
        "agent:siming:local-cli",
        "--message",
        "{prompt}",
    ],
    "custom_cli": ["{prompt}"],
}

DEFAULT_CLI_MODELS: dict[str, str] = {
    "claude_cli": "claude-code",
    "codex_cli": "codex-cli",
    "opencode_cli": "opencode/deepseek-v4-flash-free",
    "mimocode_cli": "xiaomi/mimo-v2.5-pro",
    "cursor_cli": "cursor-agent",
    "kilocode_cli": "kilocode-cli",
    "qwen_code_cli": "qwen-code-cli",
    "hermes_cli": "hermes-agent",
    "openclaw_cli": "openclaw-agent",
    "custom_cli": "custom-cli",
}

CLI_MODEL_DISCOVERY_ARGS: dict[str, list[str]] = {
    "opencode_cli": ["models"],
    "mimocode_cli": ["models"],
    "cursor_cli": ["--list-models"],
    "kilocode_cli": ["models"],
}

CLI_MODEL_SENTINELS: dict[str, set[str]] = {
    "claude_cli": {"claude-code"},
    "codex_cli": {"codex-cli"},
    "mimocode_cli": {"mimocode-cli"},
    "cursor_cli": {"cursor-agent"},
    "kilocode_cli": {"kilocode-cli"},
    "qwen_code_cli": {"qwen-code-cli"},
    "hermes_cli": {"hermes-agent"},
    "openclaw_cli": {"openclaw-agent"},
    "custom_cli": {"custom-cli"},
}

STDIN_PROMPT_PROVIDERS = {
    "claude_cli",
    "codex_cli",
    "mimocode_cli",
    "cursor_cli",
    "kilocode_cli",
    "qwen_code_cli",
}
AGENT_FILE_PROMPT_PROVIDERS = LOCAL_CLI_PROVIDERS - {"custom_cli"}
OPENCODE_FAMILY_PROVIDERS = {"opencode_cli", "mimocode_cli", "kilocode_cli"}
WINDOWS_SAFE_ARG_CHARS = 12000
OPENCODE_LEGACY_MODEL = "opencode-cli"
OPENCODE_DEFAULT_MODEL = "opencode/deepseek-v4-flash-free"
OPENCODE_MODELS = [
    OPENCODE_DEFAULT_MODEL,
    "opencode/mimo-v2.5-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/north-mini-code-free",
    "opencode/big-pickle",
]


@dataclass(frozen=True)
class CLILaunch:
    args: list[str]
    stdin_text: str | None = None


def is_local_cli_provider(provider: str | None) -> bool:
    return (provider or "").strip().lower() in LOCAL_CLI_PROVIDERS


def effective_local_cli_model(provider: str, model: str) -> str:
    if provider == "opencode_cli" and model == OPENCODE_LEGACY_MODEL:
        return OPENCODE_DEFAULT_MODEL
    return model


def is_cli_model_sentinel(provider: str, model: str | None) -> bool:
    return not model or model in CLI_MODEL_SENTINELS.get(provider, set())


def _subprocess_command(command: str, args: list[str]) -> list[str]:
    if os.name == "nt" and Path(command).suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/d", "/s", "/c", command, *args]
    return [command, *args]


def discover_local_cli_models(
    provider: str,
    command: str | None = None,
    *,
    timeout: int = 15,
) -> list[dict]:
    discovery_args = CLI_MODEL_DISCOVERY_ARGS.get(provider)
    resolved = (
        shutil.which(command or "")
        or (command if command and os.path.exists(command) else None)
        or shutil.which(DEFAULT_CLI_COMMANDS.get(provider, ""))
    )
    if not discovery_args or not resolved:
        return []
    try:
        completed = subprocess.run(
            _subprocess_command(resolved, discovery_args),
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    models: list[dict] = []
    seen: set[str] = set()
    for raw_line in completed.stdout.splitlines():
        model = raw_line.strip()
        if not model or "/" not in model or model in seen:
            continue
        seen.add(model)
        models.append({"id": model, "display_name": model})
    return models


def preferred_local_cli_model(provider: str, command: str | None = None) -> str:
    models = discover_local_cli_models(provider, command)
    ids = {item["id"] for item in models}
    preferred = DEFAULT_CLI_MODELS.get(provider, f"{provider}-default")
    if preferred in ids or not models:
        return preferred
    return models[0]["id"]


def local_cli_model_options(provider: str, command: str | None = None) -> list[dict]:
    discovered = discover_local_cli_models(provider, command)
    if discovered:
        return discovered
    if provider == "opencode_cli":
        return [{"id": model, "display_name": model} for model in OPENCODE_MODELS]
    model = DEFAULT_CLI_MODELS.get(provider, f"{provider}-default")
    label = f"跟随 {provider.removesuffix('_cli')} 当前默认模型" if is_cli_model_sentinel(provider, model) else model
    return [{"id": model, "display_name": label}]


def hidden_subprocess_kwargs() -> dict:
    """Hide transient CLI windows when Siming launches model CLIs on Windows."""
    if os.name != "nt":
        return {}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": creationflags} if creationflags else {}


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
    use_stdin = can_use_stdin and len(prompt) > WINDOWS_SAFE_ARG_CHARS
    result: list[str] = []
    for part in parts:
        if use_stdin and part == "{prompt}":
            continue
        result.append(part.replace("{prompt}", prompt).replace("{model}", model))
    if use_stdin:
        return CLILaunch(args=result, stdin_text=prompt)
    if not any("{prompt}" in part for part in parts) and prompt not in result:
        result.append(prompt)
    return CLILaunch(args=result)


def _extract_text_from_json_event(data: dict) -> str:
    """Best-effort extraction across Claude/Codex/opencode JSONL variants."""
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
    for line in text.splitlines():
        try:
            data = json.loads(line.strip())
        except Exception:
            continue
        if isinstance(data, dict):
            error = _extract_error_from_json_event(data)
            if error:
                return error
    return ""


_CLI_QUOTA_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bfree\s+usage\s+exceeded\b",
        r"\bfree\s+(plan|tier|usage).{0,60}(exceeded|exhausted|limit|quota)\b",
        r"\busage\s+(exceeded|exhausted)\b",
        r"\binsufficient[_\s-]*quota\b",
        r"\bquota[_\s-]*(exceeded|reached|exhausted)\b",
        r"\b(rate|request|usage|daily|monthly|credit|billing)[_\s-]*(limit|quota)\b",
        r"\b(limit|quota)[_\s-]*(exceeded|reached|exhausted)\b",
        r"\btoo\s+many\s+requests\b",
        r"\bresource\s+exhausted\b",
        r"\binsufficient\s+(credits|balance)\b",
        r"\bcredits?\s+(exhausted|depleted|used\s+up)\b",
        r"\bpayment\s+required\b",
        r"\bfree\s+(tier|usage)\s+limit\b",
        r"\bHTTP\s*(402|429)\b",
        r"\bstatus\s*(code)?\s*[:=]?\s*(402|429)\b",
        r"\b(402|429)\s+(Payment Required|Too Many Requests)\b",
        r"额度[已已经]*\s*(用尽|耗尽|不足|达到|超过)",
        r"配额[已已经]*\s*(用尽|耗尽|不足|达到|超过)",
        r"限额[已已经]*\s*(用尽|耗尽|不足|达到|超过)",
        r"(达到|超过).{0,12}(额度|配额|限额|用量上限|请求上限)",
        r"(余额|点数|积分|额度|配额).{0,8}不足",
        r"(今日|每日|本月|免费).{0,8}(额度|配额|次数|用量).{0,8}(用完|耗尽|达到上限)",
        r"(请求过多|速率限制|频率限制)",
    ]
]
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class CLIQuotaLimitError(RuntimeError):
    """Raised when a running CLI reports provider quota/rate-limit exhaustion."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


def _first_relevant_line(text: str) -> str:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(pattern.search(stripped) for pattern in _CLI_QUOTA_PATTERNS):
            return stripped[:500]
    return str(text or "").strip()[:500]


def detect_cli_quota_error(*texts: str) -> str:
    combined = "\n".join(str(text or "") for text in texts if text)
    if not combined:
        return ""
    combined = _ANSI_ESCAPE_RE.sub("", combined)
    if not any(pattern.search(combined) for pattern in _CLI_QUOTA_PATTERNS):
        return ""
    detail = _first_relevant_line(combined)
    suffix = f"：{detail}" if detail else ""
    return f"本机 CLI 提供方额度/限额已耗尽或触发速率限制{suffix}"


async def terminate_cli_process_tree(process: asyncio.subprocess.Process) -> None:
    """Best-effort termination for CLIs that spawn child retry processes."""
    if process.returncode is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/F", "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                **hidden_subprocess_kwargs(),
            )
        except Exception:
            try:
                process.kill()
            except ProcessLookupError:
                pass
    else:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except Exception:
        pass


async def communicate_with_cli_quota_detection(
    process: asyncio.subprocess.Process,
    *,
    input_bytes: bytes | None = None,
    extra_texts: tuple[str, ...] = (),
) -> tuple[bytes, bytes]:
    """Communicate with a CLI while scanning live output for quota failures."""
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    queue: asyncio.Queue[tuple[str, bytes | None]] = asyncio.Queue()

    async def _read_stream(name: str, stream: asyncio.StreamReader | None, chunks: list[bytes]) -> None:
        if stream is None:
            await queue.put((name, None))
            return
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            chunks.append(chunk)
            await queue.put((name, chunk))
        await queue.put((name, None))

    async def _write_stdin() -> None:
        if input_bytes is None or process.stdin is None:
            return
        try:
            process.stdin.write(input_bytes)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                process.stdin.close()
            except Exception:
                pass

    readers = [
        asyncio.create_task(_read_stream("stdout", process.stdout, stdout_chunks)),
        asyncio.create_task(_read_stream("stderr", process.stderr, stderr_chunks)),
    ]
    stdin_task = asyncio.create_task(_write_stdin())
    active_readers = len(readers)
    try:
        while active_readers:
            _name, chunk = await queue.get()
            if chunk is None:
                active_readers -= 1
                continue
            out_text = b"".join(stdout_chunks).decode("utf-8", errors="replace")
            err_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
            quota_error = detect_cli_quota_error(*extra_texts, err_text, out_text)
            if quota_error:
                await terminate_cli_process_tree(process)
                raise CLIQuotaLimitError(quota_error, stdout=out_text, stderr=err_text)
        await process.wait()
        await stdin_task
        return b"".join(stdout_chunks), b"".join(stderr_chunks)
    except asyncio.CancelledError:
        await terminate_cli_process_tree(process)
        raise
    finally:
        for task in [*readers, stdin_task]:
            if not task.done():
                task.cancel()
        await asyncio.gather(*readers, stdin_task, return_exceptions=True)


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
        return resolved

    def _args(self, prompt: str, model: str) -> list[str]:
        return parse_cli_args(self.cli_args, self._provider, prompt, model)

    def _launch(self, prompt: str, model: str) -> CLILaunch:
        return parse_cli_launch(self.cli_args, self._provider, prompt, model)

    @staticmethod
    def _runtime_cwd(extra_body: Optional[dict]) -> str:
        requested = str((extra_body or {}).get("local_cli_cwd") or "").strip()
        candidates = [
            requested,
            os.environ.get("SIMING_CONTENT_ROOT") or os.environ.get("MOSHU_CONTENT_ROOT") or "",
            str(Path(os.environ.get("SIMING_HOME") or os.environ.get("MOSHU_HOME") or tempfile.gettempdir()) / "projects"),
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

    @staticmethod
    def _runtime_attachments(extra_body: Optional[dict]) -> list[str]:
        raw = (extra_body or {}).get("local_cli_attachments") or []
        if isinstance(raw, str):
            raw = [raw]
        attachments: list[str] = []
        for value in raw:
            path = Path(str(value)).expanduser()
            if path.exists() and path.is_file():
                attachments.append(str(path.resolve()))
        return attachments

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
    def _file_prompt_instruction(prompt_file: str, attachments: list[str]) -> str:
        attachment_note = ""
        if attachments:
            attachment_note = (
                "\n任务可能引用以下只读资料文件：\n"
                + "\n".join(f"- {path}" for path in attachments)
            )
        return (
            "你是司命内部的文本生成执行器，不是代码助手。"
            f"请读取 UTF-8 任务文件：{prompt_file}\n"
            "严格按文件中的 SYSTEM/USER 指令完成任务。"
            "除读取该任务文件和其中明确引用的资料外，不要扫描代码仓库，"
            "不要修改文件，不要调用 Siming MCP 或其他外部工具。"
            "最终只输出任务要求的正文或结构化结果，不要回复 Ready。"
            f"{attachment_note}"
        )

    @staticmethod
    def _opencode_env() -> dict[str, str]:
        env = os.environ.copy()
        env["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
        env["OPENCODE_PURE"] = "1"
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
            "permission": {"*": "deny", "read": "allow"},
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

    def _apply_provider_runtime_options(
        self,
        args: list[str],
        *,
        model: str,
        cwd: str,
    ) -> None:
        provider = self._provider
        if not is_cli_model_sentinel(provider, model) and "--model" not in args and "-m" not in args:
            if provider == "openclaw_cli" and "--message" in args:
                args[args.index("--message"):args.index("--message")] = ["--model", model]
            else:
                self._insert_before_prompt(args, ["--model", model])
        if provider == "claude_cli":
            if "--dangerously-skip-permissions" not in args and "--permission-mode" not in args:
                self._insert_before_prompt(args, ["--dangerously-skip-permissions"])
        elif provider == "codex_cli":
            if "--dangerously-bypass-approvals-and-sandbox" not in args:
                self._insert_before_prompt(args, ["--dangerously-bypass-approvals-and-sandbox"])
            if "--cd" not in args and "-C" not in args:
                self._insert_before_prompt(args, ["--cd", cwd])
            if "--skip-git-repo-check" not in args:
                self._insert_before_prompt(args, ["--skip-git-repo-check"])
            if "--ephemeral" not in args:
                self._insert_before_prompt(args, ["--ephemeral"])
        elif provider == "cursor_cli":
            for flag in ("--force", "--approve-mcps", "--trust"):
                if flag not in args:
                    self._insert_before_prompt(args, [flag])
            if "--workspace" not in args:
                self._insert_before_prompt(args, ["--workspace", cwd])
        elif provider == "qwen_code_cli":
            if "--approval-mode" not in args and "--yolo" not in args:
                self._insert_before_prompt(args, ["--approval-mode", "yolo"])
            if "--include-directories" not in args and "--add-dir" not in args:
                self._insert_before_prompt(args, ["--include-directories", cwd])
        elif provider == "hermes_cli" and "--yolo" not in args:
            self._insert_before_prompt(args, ["--yolo"])
        elif provider == "openclaw_cli" and "--session-key" not in args:
            insert_at = args.index("--message") if "--message" in args else max(0, len(args) - 1)
            args[insert_at:insert_at] = ["--session-key", "agent:siming:local-cli"]

    def _opencode_family_launch(
        self,
        *,
        prompt: str,
        model: str,
        cwd: str,
        attachments: list[str],
    ) -> tuple[CLILaunch, str]:
        execution_model = effective_local_cli_model(self._provider, model)
        prompt_file = self._write_prompt_file(prompt, cwd, self._provider)

        launch = self._launch(
            self._file_prompt_instruction(prompt_file, attachments),
            execution_model,
        )
        args = list(launch.args)
        self._ensure_opencode_option(args, "--pure")
        self._ensure_opencode_option(args, "--format", "json")
        self._ensure_opencode_option(args, "--dir", cwd)
        if not is_cli_model_sentinel(self._provider, execution_model):
            self._ensure_opencode_option(args, "--model", execution_model)
        if self._provider == "mimocode_cli":
            self._ensure_opencode_option(args, "--dangerously-skip-permissions")
        elif self._provider == "kilocode_cli":
            self._ensure_opencode_option(args, "--auto")
        for path in [prompt_file, *attachments]:
            args.extend(["--file", path])
        return CLILaunch(args=args), prompt_file

    async def _run(
        self,
        prompt: str,
        model: str,
        extra_body: Optional[dict] = None,
    ) -> str:
        command = self._command()
        prompt_file: str | None = None
        launch_prompt = prompt
        cwd = self._runtime_cwd(extra_body)
        attachments = self._runtime_attachments(extra_body)
        env = os.environ.copy()
        if self._provider in OPENCODE_FAMILY_PROVIDERS:
            launch, prompt_file = self._opencode_family_launch(
                prompt=prompt,
                model=model,
                cwd=cwd,
                attachments=attachments,
            )
            if self._provider == "opencode_cli":
                env = self._opencode_env()
        elif self._provider in AGENT_FILE_PROMPT_PROVIDERS:
            prompt_file = self._write_prompt_file(prompt, cwd, self._provider)
            launch_prompt = self._file_prompt_instruction(prompt_file, attachments)
            launch = self._launch(launch_prompt, model)
            args = list(launch.args)
            self._apply_provider_runtime_options(args, model=model, cwd=cwd)
            launch = CLILaunch(args=args, stdin_text=launch.stdin_text)
        elif len(prompt) > WINDOWS_SAFE_ARG_CHARS and self._provider not in STDIN_PROMPT_PROVIDERS:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".md",
                prefix="siming-cli-prompt-",
                delete=False,
            ) as handle:
                handle.write(prompt)
                prompt_file = handle.name
            launch_prompt = (
                "Read the complete UTF-8 task prompt from this local file and follow it exactly: "
                f"{prompt_file}"
            )
            launch = self._launch(launch_prompt, model)
        else:
            launch = self._launch(launch_prompt, model)
        try:
            proc = await asyncio.create_subprocess_exec(
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
            if prompt_file:
                try:
                    os.unlink(prompt_file)
                except OSError:
                    pass
            raise LLMError(f"启动本机 CLI 失败: {exc}")

        stdin_bytes = launch.stdin_text.encode("utf-8") if launch.stdin_text is not None else None
        try:
            stdout, stderr = await communicate_with_cli_quota_detection(
                proc,
                input_bytes=stdin_bytes,
            )
        except CLIQuotaLimitError as exc:
            raise LLMError(str(exc)) from exc
        finally:
            if prompt_file:
                try:
                    os.unlink(prompt_file)
                except OSError:
                    pass
        out_text = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()
        event_error = extract_cli_error(out_text)
        quota_error = detect_cli_quota_error(err_text, event_error, out_text)
        if quota_error:
            raise LLMError(quota_error)
        if proc.returncode != 0:
            detail = err_text or out_text or f"exit code {proc.returncode}"
            raise LLMError(f"本机 CLI 调用失败: {detail}")
        if event_error:
            raise LLMError(f"本机 CLI 调用失败: {event_error}")
        return self._normalize_output(out_text)

    def _normalize_output(self, text: str) -> str:
        if not text:
            return ""
        lines = text.splitlines()
        parsed_parts: list[str] = []
        json_lines = 0
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except Exception:
                continue
            json_lines += 1
            extracted = _extract_text_from_json_event(data)
            if extracted:
                parsed_parts.append(extracted)
        if json_lines and parsed_parts:
            return "".join(parsed_parts).strip()
        return text.strip()

    async def chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str | dict] = None,
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
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
    ) -> AsyncGenerator[str, None]:
        # Most CLIs buffer output until the model turn ends. Yield the final
        # text as one chunk so callers still use the streaming endpoint safely.
        result = await self.chat_completion(messages, model, temperature, max_tokens, extra_body)
        if result["content"]:
            yield result["content"]

    async def stream_chat_completion_with_tools(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str | dict] = None,
    ) -> AsyncGenerator[dict, None]:
        result = await self.chat_completion(messages, model, temperature, max_tokens, extra_body)
        if result["content"]:
            yield {"type": "content_delta", "delta": result["content"]}
        yield {"type": "done", "finish_reason": "stop", "usage": result["usage"]}
