"""Local CLI adapter for supported local coding-agent CLIs.

This adapter treats local coding-agent CLIs as model executors. It is designed
for short, bounded generation tasks controlled by Moshu, not for exposing
Moshu secrets or letting the child process own Moshu's workflow state.
"""
from __future__ import annotations

import asyncio
import json
import os
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
    # Claude Code is used as a trusted local worker inside Moshu. Bypass
    # interactive permission prompts so file reads and Moshu MCP tool calls can
    # run unattended while Moshu still enforces its own MCP permission boundary.
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
    "openclaw_cli": ["agent", "--local", "--json", "--message", "{prompt}"],
    "custom_cli": ["{prompt}"],
}

DEFAULT_CLI_MODELS: dict[str, str] = {
    "claude_cli": "claude-code",
    "codex_cli": "codex-cli",
    "opencode_cli": "opencode/deepseek-v4-flash-free",
    "mimocode_cli": "mimocode-cli",
    "cursor_cli": "cursor-agent",
    "kilocode_cli": "kilocode-cli",
    "qwen_code_cli": "qwen-code-cli",
    "hermes_cli": "hermes-agent",
    "openclaw_cli": "openclaw-agent",
    "custom_cli": "custom-cli",
}

STDIN_PROMPT_PROVIDERS = {
    "claude_cli",
    "codex_cli",
    "mimocode_cli",
    "cursor_cli",
    "kilocode_cli",
    "qwen_code_cli",
}
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


def local_cli_model_options(provider: str) -> list[dict]:
    if provider == "opencode_cli":
        return [{"id": model, "display_name": model} for model in OPENCODE_MODELS]
    model = DEFAULT_CLI_MODELS.get(provider, f"{provider}-default")
    return [{"id": model, "display_name": model}]


def hidden_subprocess_kwargs() -> dict:
    """Hide transient CLI windows when Moshu launches model CLIs on Windows."""
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
            os.environ.get("MOSHU_CONTENT_ROOT") or "",
            str(Path(os.environ.get("MOSHU_HOME") or tempfile.gettempdir()) / "projects"),
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
        fallback = Path(tempfile.gettempdir()) / "moshu-cli-workspace"
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
    def _opencode_env() -> dict[str, str]:
        env = os.environ.copy()
        env["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
        env["OPENCODE_PURE"] = "1"
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps({
            "mcp": {
                "moshu": {
                    "type": "local",
                    "command": ["cmd", "/c", "exit", "0"],
                    "enabled": False,
                }
            },
            # Internal model execution must be side-effect free. The complete
            # prompt and source documents are attached before the run, so no
            # filesystem, shell, web, or MCP tool is required.
            "permission": {"*": "deny"},
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

    def _opencode_launch(
        self,
        *,
        prompt: str,
        model: str,
        cwd: str,
        attachments: list[str],
    ) -> tuple[CLILaunch, str]:
        execution_model = effective_local_cli_model(self._provider, model)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".md",
            prefix="moshu-opencode-task-",
            delete=False,
        ) as handle:
            handle.write(prompt)
            prompt_file = handle.name

        launch = self._launch(
            "请完整读取附件中的墨枢任务，严格遵守其中的系统规则。"
            "不要调用任何工具，不要写文件，只在最终回复中输出任务要求的结果。",
            execution_model,
        )
        args = list(launch.args)
        self._ensure_opencode_option(args, "--pure")
        self._ensure_opencode_option(args, "--format", "json")
        self._ensure_opencode_option(args, "--model", execution_model)
        self._ensure_opencode_option(args, "--dir", cwd)
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
        if self._provider == "opencode_cli":
            launch, prompt_file = self._opencode_launch(
                prompt=prompt,
                model=model,
                cwd=cwd,
                attachments=attachments,
            )
            env = self._opencode_env()
        elif len(prompt) > WINDOWS_SAFE_ARG_CHARS and self._provider not in STDIN_PROMPT_PROVIDERS:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".md",
                prefix="moshu-cli-prompt-",
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
        stdout, stderr = await proc.communicate(input=stdin_bytes)
        if prompt_file:
            try:
                os.unlink(prompt_file)
            except OSError:
                pass
        out_text = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            detail = err_text or out_text or f"exit code {proc.returncode}"
            raise LLMError(f"本机 CLI 调用失败: {detail}")
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
