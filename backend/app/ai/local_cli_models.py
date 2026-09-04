"""Local Agent CLI model discovery and configured-model inspection."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from .cli_process import hidden_subprocess_kwargs

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
    "dsh_cli": "dsh",
    "custom_cli": "",
}
OPENCODE_LEGACY_MODEL = "opencode-cli"
OPENCODE_DEFAULT_MODEL = "opencode/big-pickle"
OPENCODE_RETIRED_MODELS = frozenset({
    OPENCODE_LEGACY_MODEL,
    "opencode/deepseek-v4-flash-free",
})
OPENCODE_MODELS = [
    OPENCODE_DEFAULT_MODEL,
    "opencode/mimo-v2.5-free",
    "opencode/hy3-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/nemotron-3.5-lightning-free",
    "opencode/x-preview-f-free",
    "opencode/muse-spark-1.2-contributor-free",
]
DEFAULT_CLI_MODELS: dict[str, str] = {
    "claude_cli": "claude-code",
    "codex_cli": "codex-cli",
    "opencode_cli": OPENCODE_DEFAULT_MODEL,
    "mimocode_cli": "xiaomi/mimo-v2.5-pro",
    "cursor_cli": "cursor-agent",
    "kilocode_cli": "kilocode-cli",
    "qwen_code_cli": "qwen-code-cli",
    "hermes_cli": "hermes-agent",
    "openclaw_cli": "openclaw-agent",
    "dsh_cli": "dsh-cli",
    "custom_cli": "custom-cli",
}
CLI_MODEL_DISCOVERY_ARGS: dict[str, list[str]] = {
    "opencode_cli": ["models", "--verbose"],
    "mimocode_cli": ["models"],
    "codex_cli": ["models"],
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
    "dsh_cli": {"dsh-cli"},
    "custom_cli": {"custom-cli"},
}
MODEL_CONFIG_KEYS = {"model", "default_model", "model_name", "modelName", "defaultModel"}
MODEL_CANDIDATE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{1,199}$")
MODEL_ASSIGNMENT_PATTERN = re.compile(
    r'''(?ix)
    ["']?(?:model|default_model|model_name|modelName|defaultModel)["']?
    \s*[:=]\s*
    ["']?([A-Za-z0-9][A-Za-z0-9_.:/@+-]{1,199})
    '''
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
LOCAL_CLI_CONFIG_RELATIVE_DIRS: dict[str, list[str]] = {"codex_cli": [".codex"]}
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
    by_id: dict[str, dict] = {}
    for group in groups:
        for item in group:
            model = str(item.get("id") or "").strip()
            if not model:
                continue
            if model not in by_id:
                option = {
                    "id": model,
                    "display_name": str(item.get("display_name") or model),
                }
                by_id[model] = option
                merged.append(option)
            # A configured name may precede discovery. Keep its label, while
            # retaining capacity evidence from the CLI's exact model record.
            for key in (
                "context_window_tokens", "max_output_tokens",
                "safety_margin_tokens", "capacity_source",
            ):
                if key in item:
                    by_id[model][key] = item[key]
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
    return _merge_model_options(*[
        [_model_option(model, f"{model}（{source_label}）")]
        for value in values
        if (model := _clean_model_candidate(value))
    ])


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
                files.extend(
                    nested for nested in sorted(child.iterdir())
                    if nested.is_file() and nested.suffix.lower() in MODEL_CONFIG_FILE_SUFFIXES
                )
        return files[:MODEL_CONFIG_MAX_FILES]
    except OSError:
        return []


def _local_cli_config_paths(provider: str) -> list[Path]:
    paths: list[Path] = []
    try:
        home = Path.home()
    except RuntimeError:
        home = None
    for env_name in LOCAL_CLI_CONFIG_ENV_DIRS.get(provider, []):
        if env_path := os.environ.get(env_name):
            paths.extend(_config_files_from_path(Path(env_path).expanduser()))
    if home:
        paths.extend(home / relative for relative in LOCAL_CLI_CONFIG_RELATIVE_PATHS.get(provider, []))
        for relative_dir in LOCAL_CLI_CONFIG_RELATIVE_DIRS.get(provider, []):
            paths.extend(_config_files_from_path(home / relative_dir))
    unique: dict[str, Path] = {str(path): path for path in paths}
    return list(unique.values())[:MODEL_CONFIG_MAX_FILES]


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
    environment = [
        _model_option(model, f"{model}（环境变量 {env_name}）")
        for env_name in LOCAL_CLI_MODEL_ENV_VARS.get(provider, [])
        if (model := _clean_model_candidate(os.environ.get(env_name)))
    ]
    return _merge_model_options(
        _model_options_from_cli_args(cli_args),
        environment,
        _model_options_from_config_files(provider),
    )


def effective_local_cli_model(provider: str, model: str) -> str:
    if provider == "opencode_cli" and model in OPENCODE_RETIRED_MODELS:
        return OPENCODE_DEFAULT_MODEL
    return model


def is_cli_model_sentinel(provider: str, model: str | None) -> bool:
    return not model or model in CLI_MODEL_SENTINELS.get(provider, set())


def _subprocess_command(command: str, args: list[str]) -> list[str]:
    if os.name == "nt" and Path(command).suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/d", "/s", "/c", command, *args]
    return [command, *args]


def _extract_discovered_models(payload: str) -> list[str]:
    text = payload.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        data = None
    if data is None:
        return [
            value
            for value in (_clean_model_candidate(line) for line in payload.splitlines())
            if value
        ]
    values: list[object] = []

    def collect(model_data: object) -> None:
        if isinstance(model_data, str):
            values.append(model_data)
        elif isinstance(model_data, list):
            for item in model_data:
                collect(item)
        elif isinstance(model_data, dict):
            values.extend(
                value for key in ("id", "slug", "model", "name")
                if isinstance((value := model_data.get(key)), str)
            )
            for key in ("models", "data", "items"):
                if key in model_data:
                    collect(model_data[key])

    collect(data)
    return [value for value in map(str, values) if _clean_model_candidate(value)]


def _opencode_model_options(payload: str) -> list[dict]:
    """Read OpenCode's model-ID line followed by its verbose JSON record."""
    decoder = json.JSONDecoder()
    options: list[dict] = []
    offset = 0
    while offset < len(payload):
        end = payload.find("\n", offset)
        end = len(payload) if end < 0 else end
        model = _clean_model_candidate(payload[offset:end])
        offset = end + 1
        if not model or "/" not in model:
            continue
        option = _model_option(model)
        while offset < len(payload) and payload[offset].isspace():
            offset += 1
        if offset < len(payload) and payload[offset] == "{":
            try:
                record, offset = decoder.raw_decode(payload, offset)
            except json.JSONDecodeError:
                options.append(option)
                break
            if isinstance(record, dict) and (
                f"{record.get('providerID')}/{record.get('id')}" == model
            ):
                if isinstance(record.get("name"), str):
                    option["display_name"] = record["name"]
                limits = record.get("limit")
                if isinstance(limits, dict):
                    window, output = limits.get("context"), limits.get("output")
                    if (
                        type(window) is int and type(output) is int
                        and 2048 <= window <= 10_000_000
                        and 0 < output <= 1_000_000
                        and output + 512 < window
                    ):
                        option.update(
                            context_window_tokens=window,
                            max_output_tokens=output,
                            safety_margin_tokens=512,
                            capacity_source="opencode_cli_metadata",
                        )
        options.append(option)
    return _merge_model_options(options)


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
    if provider == "opencode_cli":
        return _opencode_model_options(completed.stdout)
    return [
        {"id": model, "display_name": model}
        for model in dict.fromkeys(_extract_discovered_models(completed.stdout))
    ]


def preferred_local_cli_model(provider: str, command: str | None = None) -> str:
    models = discover_local_cli_models(provider, command)
    ids = {item["id"] for item in models}
    preferred = DEFAULT_CLI_MODELS.get(provider, f"{provider}-default")
    if not models:
        return preferred
    if provider == "opencode_cli":
        for candidate in OPENCODE_MODELS:
            if candidate in ids:
                return candidate
        for item in models:
            model_id = str(item.get("id") or "").strip().lower()
            if model_id.startswith("opencode/") and (
                model_id.endswith("-free") or model_id == "opencode/big-pickle"
            ):
                return str(item["id"])
    return preferred if preferred in ids else models[0]["id"]


def local_cli_model_options(
    provider: str,
    command: str | None = None,
    cli_args: str | None = None,
) -> list[dict]:
    default_model = DEFAULT_CLI_MODELS.get(provider, f"{provider}-default")
    fallback = (
        [_model_option(model) for model in OPENCODE_MODELS]
        if provider == "opencode_cli"
        else [_model_option(
            default_model,
            (
                f"跟随 {provider.removesuffix('_cli')} 当前默认模型"
                if is_cli_model_sentinel(provider, default_model)
                else default_model
            ),
        )]
    )
    return _merge_model_options(
        _configured_local_cli_model_options(provider, cli_args),
        discover_local_cli_models(provider, command),
        fallback,
    )


__all__ = [
    "DEFAULT_CLI_COMMANDS",
    "DEFAULT_CLI_MODELS",
    "OPENCODE_DEFAULT_MODEL",
    "OPENCODE_MODELS",
    "OPENCODE_RETIRED_MODELS",
    "discover_local_cli_models",
    "effective_local_cli_model",
    "is_cli_model_sentinel",
    "local_cli_model_options",
    "preferred_local_cli_model",
]
