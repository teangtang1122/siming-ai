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
import time
from collections.abc import AsyncGenerator, Callable
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
from .local_cli_output import normalize_cli_output
from .cli_process import hidden_subprocess_kwargs, terminate_cli_process_tree

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
LOCAL_CLI_TIMEOUT_GRACE_SECONDS = 15

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
DASH_STDIN_PROMPT_PROVIDERS = {"codex_cli"}
AGENT_FILE_PROMPT_PROVIDERS = LOCAL_CLI_PROVIDERS - {"custom_cli", "codex_cli"}
OPENCODE_FAMILY_PROVIDERS = {"opencode_cli", "mimocode_cli", "kilocode_cli"}
WINDOWS_SAFE_ARG_CHARS = 12000
OPENCODE_LEGACY_MODEL = "opencode-cli"
OPENCODE_DEFAULT_MODEL = "opencode/deepseek-v4-flash-free"
OPENCODE_MODELS = [
    OPENCODE_DEFAULT_MODEL,
    "opencode/mimo-v2.5-free",
    "opencode/laguna-s-2.1-free", "opencode/north-mini-code-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/big-pickle",
]
MODEL_CONFIG_KEYS = {"model", "default_model", "model_name", "modelName", "defaultModel"}
MODEL_CANDIDATE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{1,199}$")
MODEL_ASSIGNMENT_PATTERN = re.compile(
    r"""(?ix)
    ["']?(?:model|default_model|model_name|modelName|defaultModel)["']?
    \s*[:=]\s*
    ["']?([A-Za-z0-9][A-Za-z0-9_.:/@+-]{1,199})
    """
)
LOCAL_CLI_MODEL_ENV_VARS: dict[str, list[str]] = {
    "claude_cli": ["CLAUDE_MODEL", "ANTHROPIC_MODEL"],
    "codex_cli": ["CODEX_MODEL"],
    "qwen_code_cli": ["QWEN_CODE_MODEL", "QWEN_MODEL", "DASHSCOPE_MODEL", "OPENAI_MODEL"],
    "hermes_cli": ["HERMES_MODEL", "OPENAI_MODEL"],
    "openclaw_cli": ["OPENCLAW_MODEL", "OPENAI_MODEL"],
    "custom_cli": ["SIMING_LOCAL_CLI_MODEL", "LOCAL_CLI_MODEL", "CUSTOM_CLI_MODEL"],
}
LOCAL_CLI_CONFIG_ENV_DIRS: dict[str, list[str]] = {
    "claude_cli": ["CLAUDE_CONFIG_DIR", "CLAUDE_HOME"],
    "codex_cli": ["CODEX_HOME"],
    "qwen_code_cli": ["QWEN_CODE_HOME", "QWEN_HOME"],
    "hermes_cli": ["HERMES_HOME"],
    "openclaw_cli": ["OPENCLAW_HOME"],
}
LOCAL_CLI_CONFIG_RELATIVE_PATHS: dict[str, list[str]] = {
    "claude_cli": [".claude.json", ".claude/settings.json", ".claude/settings.local.json"],
    "codex_cli": [".codex/config.toml"],
    "qwen_code_cli": [".qwen/config.json", ".qwen-code/config.json"],
    "hermes_cli": [".hermes/config.json", ".hermes/config.toml"],
    "openclaw_cli": [".openclaw/config.json", ".openclaw/config.toml"],
}
LOCAL_CLI_CONFIG_RELATIVE_DIRS: dict[str, list[str]] = {
    "codex_cli": [".codex"],
}
LOCAL_CLI_CONFIG_SOURCE_LABELS: dict[str, str] = {
    "claude_cli": "Claude 配置",
    "codex_cli": "Codex 配置",
    "qwen_code_cli": "Qwen 配置",
    "hermes_cli": "Hermes 配置",
    "openclaw_cli": "OpenClaw 配置",
    "custom_cli": "本机 CLI 配置",
}
MODEL_CONFIG_FILE_SUFFIXES = {".json", ".jsonc", ".toml", ".yaml", ".yml", ".ini", ".conf", ".config"}
MODEL_CONFIG_MAX_FILES = 40
MODEL_CONFIG_MAX_BYTES = 128 * 1024


def _model_option(model: str, display_name: str | None = None) -> dict:
    return {"id": model, "display_name": display_name or model}


def _merge_model_options(*groups: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            model = str(item.get("id") or "").strip()
            if not model or model in seen:
                continue
            seen.add(model)
            merged.append({
                "id": model,
                "display_name": str(item.get("display_name") or model),
            })
    return merged


def _clean_model_candidate(value: object) -> str:
    model = str(value or "").strip().strip("'\"` ,;")
    if not model or model == "{model}" or "{" in model or "}" in model:
        return ""
    if "\\" in model or "://" in model or len(model) > 200:
        return ""
    if model.lower() in {"true", "false", "none", "null", "auto"}:
        return ""
    return model if MODEL_CANDIDATE_PATTERN.fullmatch(model) else ""


def _model_options_from_values(values: list[object], source_label: str) -> list[dict]:
    options: list[dict] = []
    for value in values:
        model = _clean_model_candidate(value)
        if model:
            options.append(_model_option(model, f"{model}（{source_label}）"))
    return _merge_model_options(options)


def _walk_model_values(data: object) -> list[str]:
    values: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key in MODEL_CONFIG_KEYS and isinstance(value, str):
                values.append(value)
            elif isinstance(value, (dict, list)):
                values.extend(_walk_model_values(value))
    elif isinstance(data, list):
        for item in data:
            values.extend(_walk_model_values(item))
    return values


def _model_options_from_config_text(text: str, source_label: str) -> list[dict]:
    values: list[object] = []
    try:
        values.extend(_walk_model_values(json.loads(text)))
    except (TypeError, ValueError):
        pass
    values.extend(match.group(1) for match in MODEL_ASSIGNMENT_PATTERN.finditer(text))
    return _model_options_from_values(values, source_label)


def _model_options_from_cli_args(cli_args: str | None) -> list[dict]:
    if not cli_args:
        return []
    tokens: list[str]
    try:
        parsed = json.loads(cli_args)
        tokens = [str(item) for item in parsed] if isinstance(parsed, list) else shlex.split(str(cli_args))
    except (TypeError, ValueError):
        try:
            tokens = shlex.split(str(cli_args), posix=os.name != "nt")
        except ValueError:
            tokens = str(cli_args).split()

    values: list[str] = []
    for index, token in enumerate(tokens):
        if token in {"--model", "-m"} and index + 1 < len(tokens):
            values.append(tokens[index + 1])
        elif token.startswith("--model=") or token.startswith("-m="):
            values.append(token.split("=", 1)[1])
    return _model_options_from_values(values, "CLI 参数")


def _model_options_from_env(provider: str) -> list[dict]:
    options: list[dict] = []
    for env_name in LOCAL_CLI_MODEL_ENV_VARS.get(provider, []):
        model = _clean_model_candidate(os.environ.get(env_name))
        if model:
            options.append(_model_option(model, f"{model}（环境变量 {env_name}）"))
    return _merge_model_options(options)


def _config_files_from_path(path: Path) -> list[Path]:
    try:
        if path.is_file():
            return [path]
        if not path.is_dir():
            return []
        files: list[Path] = []
        for child in sorted(path.iterdir()):
            if len(files) >= MODEL_CONFIG_MAX_FILES:
                break
            if child.is_file() and child.suffix.lower() in MODEL_CONFIG_FILE_SUFFIXES:
                files.append(child)
            elif child.is_dir() and child.name.lower() in {"config", "configs", "profiles", "settings"}:
                for nested in sorted(child.iterdir()):
                    if len(files) >= MODEL_CONFIG_MAX_FILES:
                        break
                    if nested.is_file() and nested.suffix.lower() in MODEL_CONFIG_FILE_SUFFIXES:
                        files.append(nested)
        return files
    except OSError:
        return []


def _local_cli_config_paths(provider: str) -> list[Path]:
    paths: list[Path] = []
    try:
        home = Path.home()
    except RuntimeError:
        home = None
    for env_name in LOCAL_CLI_CONFIG_ENV_DIRS.get(provider, []):
        env_path = os.environ.get(env_name)
        if env_path:
            paths.extend(_config_files_from_path(Path(env_path).expanduser()))
    if home:
        for relative in LOCAL_CLI_CONFIG_RELATIVE_PATHS.get(provider, []):
            paths.append(home / relative)
        for relative_dir in LOCAL_CLI_CONFIG_RELATIVE_DIRS.get(provider, []):
            paths.extend(_config_files_from_path(home / relative_dir))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique[:MODEL_CONFIG_MAX_FILES]


def _model_options_from_config_files(provider: str) -> list[dict]:
    source_label = LOCAL_CLI_CONFIG_SOURCE_LABELS.get(provider, "本机 CLI 配置")
    options: list[dict] = []
    for path in _local_cli_config_paths(provider):
        try:
            if path.stat().st_size > MODEL_CONFIG_MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        options.extend(_model_options_from_config_text(text, source_label))
    return _merge_model_options(options)


def _configured_local_cli_model_options(provider: str, cli_args: str | None = None) -> list[dict]:
    return _merge_model_options(
        _model_options_from_cli_args(cli_args),
        _model_options_from_env(provider),
        _model_options_from_config_files(provider),
    )


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
        model = _clean_model_candidate(raw_line)
        if not model or model in seen:
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


def _local_cli_fallback_model_options(provider: str) -> list[dict]:
    if provider == "opencode_cli":
        return [_model_option(model) for model in OPENCODE_MODELS]
    model = DEFAULT_CLI_MODELS.get(provider, f"{provider}-default")
    label = f"跟随 {provider.removesuffix('_cli')} 当前默认模型" if is_cli_model_sentinel(provider, model) else model
    return [_model_option(model, label)]


def local_cli_model_options(
    provider: str,
    command: str | None = None,
    cli_args: str | None = None,
) -> list[dict]:
    discovered = discover_local_cli_models(provider, command)
    configured = _configured_local_cli_model_options(provider, cli_args)
    return _merge_model_options(
        configured,
        discovered,
        _local_cli_fallback_model_options(provider),
    )


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
_CLI_AUTH_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bInvalidToken\b",
        r"\binvalid[_\s-]*token\b",
        r"\bexpired[_\s-]*token\b",
        r"\bunauthenticated\b",
        r"\bauthentication\s+(required|failed)\b",
        r"\blog\s*in\s+required\b",
        r"\b(sign|log)\s*in\b",
        r"\bplease\s+(sign|log)\s*in\b",
        r"\bnot\s+authenticated\b",
        r"\b401\s+(Unauthorized|Unauthenticated)\b",
    ]
]
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class CLIQuotaLimitError(RuntimeError):
    """Raised when a running CLI reports provider quota/rate-limit exhaustion."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class CLITimeoutError(RuntimeError):
    """Raised when a CLI is silent or retrying beyond Siming's timeout."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class CLIStalledError(CLITimeoutError):
    """Raised only after the complete CLI process tree has stopped making progress."""


class CLIInterruptedError(RuntimeError):
    """Raised when the monitored CLI process tree disappears unexpectedly."""


def sample_cli_process_tree(pid: int) -> dict[str, Any]:
    """Return non-sensitive liveness, CPU and IO counters for a CLI process tree."""
    if psutil is None:
        return {"alive": True, "process_count": 1, "metrics_available": False}
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
    except (psutil.Error, OSError):
        return {"alive": False, "process_count": 0, "metrics_available": True}
    cpu_seconds = 0.0
    read_bytes = 0
    write_bytes = 0
    rss_bytes = 0
    alive = 0
    for process in processes:
        try:
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                continue
            alive += 1
            cpu = process.cpu_times()
            cpu_seconds += float(cpu.user) + float(cpu.system)
            io = process.io_counters()
            read_bytes += int(getattr(io, "read_bytes", 0) or 0)
            write_bytes += int(getattr(io, "write_bytes", 0) or 0)
            rss_bytes += int(process.memory_info().rss or 0)
        except (psutil.Error, OSError):
            continue
    return {
        "alive": alive > 0,
        "process_count": alive,
        "cpu_seconds": round(cpu_seconds, 3),
        "read_bytes": read_bytes,
        "write_bytes": write_bytes,
        "rss_bytes": rss_bytes,
        "metrics_available": True,
    }


def _process_metrics_advanced(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if previous is None:
        return True
    if not current.get("metrics_available"):
        return False
    return any(
        float(current.get(key) or 0) > float(previous.get(key) or 0)
        for key in ("cpu_seconds", "read_bytes", "write_bytes")
    ) or int(current.get("process_count") or 0) != int(previous.get("process_count") or 0)


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


def detect_cli_auth_error(*texts: str) -> str:
    combined = "\n".join(str(text or "") for text in texts if text)
    if not combined:
        return ""
    combined = _ANSI_ESCAPE_RE.sub("", combined)
    if not any(pattern.search(combined) for pattern in _CLI_AUTH_PATTERNS):
        return ""
    detail = ""
    for line in combined.splitlines():
        stripped = line.strip()
        if stripped and any(pattern.search(stripped) for pattern in _CLI_AUTH_PATTERNS):
            detail = stripped[:500]
            break
    suffix = f"：{detail}" if detail else ""
    return f"本机 CLI 登录凭据无效或已过期{suffix}"


async def communicate_with_cli_quota_detection(
    process: asyncio.subprocess.Process,
    *,
    input_bytes: bytes | None = None,
    extra_texts: tuple[str, ...] = (),
    timeout_seconds: float | None = None,
    operation_id: str | None = None,
    external_activity_probe: Callable[[], Any] | None = None,
    poll_seconds: float = 5.0,
    quiet_seconds: float | None = None,
    suspected_stall_seconds: float | None = None,
    stalled_seconds: float | None = None,
) -> tuple[bytes, bytes]:
    """Communicate with a CLI while scanning live output for quota failures."""
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    queue: asyncio.Queue[tuple[str, bytes | None]] = asyncio.Queue()
    deadline = time.monotonic() + timeout_seconds if timeout_seconds else None
    quiet_after = quiet_seconds or float(os.environ.get("SIMING_CLI_QUIET_SECONDS", 600))
    suspect_after = suspected_stall_seconds or float(os.environ.get("SIMING_CLI_SUSPECTED_STALL_SECONDS", 1800))
    stalled_after = stalled_seconds or float(os.environ.get("SIMING_CLI_STALLED_SECONDS", 3600))
    last_meaningful_activity = time.monotonic()
    last_output_activity = last_meaningful_activity
    last_metrics: dict[str, Any] | None = None
    last_external_activity: Any = None
    reported_health = "active"

    if operation_id is None:
        try:
            from ..modules.operations.interfaces.runtime import current_operation_id

            operation_id = current_operation_id()
        except Exception:
            operation_id = None

    def _report(signal: str, payload: dict[str, Any] | None = None, message: str | None = None) -> None:
        if not operation_id:
            return
        try:
            from ..services.operation_runtime import record_operation_signal

            record_operation_signal(operation_id, signal, payload, message)
        except Exception:
            # Progress reporting must never break the provider call.
            return

    _report("phase", {"pid": process.pid}, "本机 CLI 已启动，正在等待模型处理")

    def _decoded_output() -> tuple[str, str]:
        return (
            b"".join(stdout_chunks).decode("utf-8", errors="replace"),
            b"".join(stderr_chunks).decode("utf-8", errors="replace"),
        )

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
        while active_readers or process.returncode is None:
            if active_readers == 0 and process.returncode is not None:
                break
            try:
                now_monotonic = time.monotonic()
                remaining = deadline - now_monotonic if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    raise TimeoutError
                wait_seconds = max(0.1, min(poll_seconds, remaining)) if remaining is not None else max(0.1, poll_seconds)
                _name, chunk = await asyncio.wait_for(queue.get(), timeout=wait_seconds)
            except TimeoutError as exc:
                now_monotonic = time.monotonic()
                if deadline is None or now_monotonic < deadline:
                    metrics = sample_cli_process_tree(process.pid)
                    try:
                        external_activity = external_activity_probe() if external_activity_probe else None
                    except Exception:
                        external_activity = None
                    advanced = _process_metrics_advanced(last_metrics, metrics)
                    if external_activity is not None and external_activity != last_external_activity:
                        last_external_activity = external_activity
                        advanced = True
                        _report("tool", {"activity": str(external_activity)[:200]}, "CLI 已执行司命工具")
                    if advanced:
                        last_meaningful_activity = now_monotonic
                        reported_health = "active"
                        _report("process", metrics, "模型进程仍在计算")
                    else:
                        _report("heartbeat", metrics)
                    last_metrics = metrics
                    idle = now_monotonic - last_meaningful_activity
                    output_idle = now_monotonic - last_output_activity
                    if not metrics.get("alive") and process.returncode is None:
                        try:
                            await asyncio.wait_for(process.wait(), timeout=max(0.2, poll_seconds))
                        except TimeoutError:
                            _report(
                                "disconnected",
                                {**metrics, "lifecycle_status": "interrupted"},
                                "CLI 进程已经意外中断",
                            )
                            await terminate_cli_process_tree(process)
                            raise CLIInterruptedError(
                                "本机 CLI 进程已经意外中断，最近检查点已保留",
                            )
                        continue
                    elif idle >= stalled_after and metrics.get("metrics_available"):
                        _report("stalled", metrics, "CLI 进程已确认长时间没有任何活动")
                        out_text, err_text = _decoded_output()
                        await terminate_cli_process_tree(process)
                        raise CLIStalledError(
                            "本机 CLI 已确认卡住：进程、输出、工具调用和磁盘读写均长时间没有变化",
                            stdout=out_text,
                            stderr=err_text,
                        )
                    elif idle >= suspect_after and reported_health != "suspected_stall":
                        reported_health = "suspected_stall"
                        _report("suspected_stall", metrics, "暂时没有检测到活动，可继续等待或重试当前任务")
                    elif output_idle >= quiet_after and reported_health == "active":
                        reported_health = "quiet"
                        _report("quiet", metrics, "暂时没有新文字输出，模型进程仍在运行")
                    continue
                out_text, err_text = _decoded_output()
                quota_error = detect_cli_quota_error(*extra_texts, err_text, out_text)
                await terminate_cli_process_tree(process)
                if quota_error:
                    raise CLIQuotaLimitError(quota_error, stdout=out_text, stderr=err_text) from exc
                seconds = int(timeout_seconds or 0)
                raise CLITimeoutError(
                    f"本机 CLI 请求超时（{seconds}秒）",
                    stdout=out_text,
                    stderr=err_text,
                ) from exc
            if chunk is None:
                active_readers -= 1
                continue
            now_monotonic = time.monotonic()
            last_meaningful_activity = now_monotonic
            last_output_activity = now_monotonic
            reported_health = "active"
            _report("output", {"stream": _name, "bytes": len(chunk)}, "模型正在返回内容")
            out_text, err_text = _decoded_output()
            quota_error = detect_cli_quota_error(*extra_texts, err_text, out_text)
            if quota_error:
                await terminate_cli_process_tree(process)
                raise CLIQuotaLimitError(quota_error, stdout=out_text, stderr=err_text)
        await process.wait()
        await stdin_task
        return b"".join(stdout_chunks), b"".join(stderr_chunks)
    except asyncio.CancelledError:
        out_text, err_text = _decoded_output()
        quota_error = detect_cli_quota_error(*extra_texts, err_text, out_text)
        await terminate_cli_process_tree(process)
        if quota_error:
            raise CLIQuotaLimitError(quota_error, stdout=out_text, stderr=err_text)
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
    def _runtime_cwd(extra_body: dict | None) -> str:
        if bool((extra_body or {}).get("local_cli_isolated")):
            # CLI-as-model execution never needs a project checkout. An empty
            # per-call directory prevents accidental repository/project scans.
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

    @staticmethod
    def _runtime_attachments(extra_body: dict | None) -> list[str]:
        if bool((extra_body or {}).get("local_cli_isolated")):
            return []
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
        try:
            shutil.rmtree(cwd, ignore_errors=True)
        except OSError:
            pass

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
        ensure_opencode_logging_args(self._provider, args)
        for path in [prompt_file, *attachments]:
            args.extend(["--file", path])
        return CLILaunch(args=args), prompt_file

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

    async def _run(
        self,
        prompt: str,
        model: str,
        extra_body: dict | None = None,
    ) -> str:
        command = self._command()
        prompt_file: str | None = None
        codex_output_file: str | None = None
        cleanup_codex_output_file = False
        launch_prompt = prompt
        cwd = self._runtime_cwd(extra_body)
        isolated = bool((extra_body or {}).get("local_cli_isolated"))
        attachments = self._runtime_attachments(extra_body)
        env = self._isolated_environment(os.environ.copy(), isolated)
        if self._provider in OPENCODE_FAMILY_PROVIDERS:
            launch, prompt_file = self._opencode_family_launch(
                prompt=prompt,
                model=model,
                cwd=cwd,
                attachments=attachments,
            )
            if self._provider == "opencode_cli":
                env = self._isolated_environment(self._opencode_env(), isolated)
        elif self._provider == "codex_cli":
            launch = self._launch(launch_prompt, model)
            args = list(launch.args)
            self._apply_provider_runtime_options(args, model=model, cwd=cwd)
            codex_output_file, cleanup_codex_output_file = self._ensure_codex_output_file(args, cwd)
            launch = CLILaunch(args=args, stdin_text=launch.stdin_text)
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
            if cleanup_codex_output_file and codex_output_file:
                try:
                    os.unlink(codex_output_file)
                except OSError:
                    pass
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
                timeout_seconds=self._timeout_seconds(extra_body),
                operation_id=str((extra_body or {}).get("operation_id") or "") or None,
            )
        except CLIQuotaLimitError as exc:
            if cleanup_codex_output_file and codex_output_file:
                try:
                    os.unlink(codex_output_file)
                except OSError:
                    pass
            raise LLMError(str(exc)) from exc
        except (CLITimeoutError, CLIStalledError) as exc:
            if cleanup_codex_output_file and codex_output_file:
                try:
                    os.unlink(codex_output_file)
                except OSError:
                    pass
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
        auth_error = detect_cli_auth_error(err_text, event_error, out_text)
        if auth_error:
            if cleanup_codex_output_file and codex_output_file:
                try:
                    os.unlink(codex_output_file)
                except OSError:
                    pass
            raise LLMError(auth_error)
        if codex_output_file and proc.returncode == 0:
            try:
                file_text = Path(codex_output_file).read_text(encoding="utf-8").strip()
            except OSError:
                file_text = ""
            finally:
                if cleanup_codex_output_file:
                    try:
                        os.unlink(codex_output_file)
                    except OSError:
                        pass
            if file_text:
                self._cleanup_isolated_workspace(cwd, isolated)
                return file_text
        out_text = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()
        event_error = extract_cli_error(out_text)
        quota_error = detect_cli_quota_error(err_text, event_error, out_text)
        if quota_error:
            if cleanup_codex_output_file and codex_output_file:
                try:
                    os.unlink(codex_output_file)
                except OSError:
                    pass
            raise LLMError(quota_error)
        if proc.returncode != 0:
            if cleanup_codex_output_file and codex_output_file:
                try:
                    os.unlink(codex_output_file)
                except OSError:
                    pass
            detail = err_text or out_text or f"exit code {proc.returncode}"
            raise LLMError(f"本机 CLI 调用失败: {detail}")
        if event_error:
            raise LLMError(f"本机 CLI 调用失败: {event_error}")
        result = self._normalize_output(out_text)
        self._cleanup_isolated_workspace(cwd, isolated)
        return result

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
        result = await self.chat_completion(messages, model, temperature, max_tokens, extra_body)
        if result["content"]:
            yield result["content"]

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
