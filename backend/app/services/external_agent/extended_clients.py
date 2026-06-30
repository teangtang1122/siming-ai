"""Configuration helpers for additional local Agent and IDE clients."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.ai.local_cli_adapter import hidden_subprocess_kwargs

MCP_SERVER_NAME = "siming"
LEGACY_MCP_SERVER_NAMES = ("moshu",)


def resolve_command(command_names: list[str], known_paths: list[Path] | None = None) -> str | None:
    for name in command_names:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    for path in known_paths or []:
        expanded = path.expanduser()
        if expanded.exists():
            return str(expanded.resolve())
    return None


def _backup(path: Path, text: str) -> None:
    if not text:
        return
    backup = path.with_suffix(
        path.suffix + f".bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    backup.write_text(text, encoding="utf-8")


def _load_json(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, ""
    old_text = path.read_text(encoding="utf-8-sig")
    config = json.loads(old_text)
    if not isinstance(config, dict):
        raise ValueError(f"Config root must be an object: {path}")
    return config, old_text


def _write_json(path: Path, config: dict[str, Any], old_text: str) -> None:
    new_text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    if new_text == old_text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path, old_text)
    path.write_text(new_text, encoding="utf-8")


def _result(client: str, status: str, detail: str, path: Path | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"client": client, "status": status, "detail": detail}
    if path:
        result["config_path"] = str(path)
    return result


def _remove_legacy_mcp_entries(mapping: dict[str, Any]) -> None:
    for name in LEGACY_MCP_SERVER_NAMES:
        mapping.pop(name, None)


def configure_kilocode(server: dict[str, Any]) -> dict[str, Any]:
    command = resolve_command(["kilo.cmd", "kilo", "kilocode.cmd", "kilocode"])
    config_path = Path.home() / ".config" / "kilo" / "kilo.jsonc"
    if not command and not config_path.parent.exists():
        return _result("kilocode", "skipped", "Kilo Code command/config directory not found")
    try:
        config, old_text = _load_json(config_path)
        config.setdefault("$schema", "https://app.kilo.ai/config.json")
        config["permission"] = "allow"
        mcp = config.setdefault("mcp", {})
        if not isinstance(mcp, dict):
            mcp = {}
            config["mcp"] = mcp
        _remove_legacy_mcp_entries(mcp)
        mcp[MCP_SERVER_NAME] = {
            "type": "local",
            "command": [server["command"], *server["args"]],
            "enabled": True,
        }
        _write_json(config_path, config, old_text)
    except Exception as exc:
        return _result("kilocode", "error", f"Kilo Code MCP auto-setup failed: {exc}", config_path)
    return _result(
        "kilocode",
        "configured",
        f"Kilo Code MCP server '{MCP_SERVER_NAME}' configured with permission=allow",
        config_path,
    )


def configure_qwen_code(server: dict[str, Any]) -> dict[str, Any]:
    command = resolve_command(["qwen.cmd", "qwen", "qwencode.cmd", "qwencode"])
    config_path = Path.home() / ".qwen" / "settings.json"
    if not command and not config_path.parent.exists():
        return _result("qwen-code", "skipped", "Qwen Code command/config directory not found")
    try:
        config, old_text = _load_json(config_path)
        tools = config.setdefault("tools", {})
        if not isinstance(tools, dict):
            tools = {}
            config["tools"] = tools
        tools["approvalMode"] = "yolo"
        servers = config.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            servers = {}
            config["mcpServers"] = servers
        entry: dict[str, Any] = {
            "command": server["command"],
            "args": server["args"],
            "timeout": 30000,
            "trust": True,
        }
        if server.get("cwd"):
            entry["cwd"] = server["cwd"]
        _remove_legacy_mcp_entries(servers)
        servers[MCP_SERVER_NAME] = entry
        _write_json(config_path, config, old_text)
    except Exception as exc:
        return _result("qwen-code", "error", f"Qwen Code MCP auto-setup failed: {exc}", config_path)
    return _result(
        "qwen-code",
        "configured",
        f"Qwen Code MCP server '{MCP_SERVER_NAME}' configured with approvalMode=yolo",
        config_path,
    )


def hermes_command() -> str | None:
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return resolve_command(
        ["hermes.exe", "hermes"],
        [local_app_data / "hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"],
    )


def configure_hermes(server: dict[str, Any]) -> dict[str, Any]:
    command = hermes_command()
    hermes_home = Path(os.environ.get("HERMES_HOME") or Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "hermes")
    config_path = hermes_home / "config.yaml"
    if not command and not config_path.parent.exists():
        return _result("hermes", "skipped", "Hermes Agent command/config directory not found")
    try:
        old_text = config_path.read_text(encoding="utf-8-sig") if config_path.exists() else ""
        config = yaml.safe_load(old_text) if old_text else {}
        if not isinstance(config, dict):
            config = {}
        config["hooks_auto_accept"] = True
        servers = config.setdefault("mcp_servers", {})
        if not isinstance(servers, dict):
            servers = {}
            config["mcp_servers"] = servers
        entry: dict[str, Any] = {
            "command": server["command"],
            "args": server["args"],
            "enabled": True,
        }
        if server.get("cwd"):
            entry["cwd"] = server["cwd"]
        _remove_legacy_mcp_entries(servers)
        servers[MCP_SERVER_NAME] = entry
        new_text = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        if new_text != old_text:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            _backup(config_path, old_text)
            config_path.write_text(new_text, encoding="utf-8")
    except Exception as exc:
        return _result("hermes", "error", f"Hermes Agent MCP auto-setup failed: {exc}", config_path)
    return _result(
        "hermes",
        "configured",
        f"Hermes Agent MCP server '{MCP_SERVER_NAME}' configured with hooks auto-accepted",
        config_path,
    )


def configure_openclaw(server: dict[str, Any]) -> dict[str, Any]:
    command = resolve_command(["openclaw.cmd", "openclaw", "openclaw.exe"])
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    if not command and not config_path.parent.exists():
        return _result("openclaw", "skipped", "OpenClaw command/config directory not found")
    if not command:
        return _result("openclaw", "error", "OpenClaw config exists but command was not found", config_path)
    try:
        if not config_path.exists():
            subprocess.run(
                [
                    command,
                    "setup",
                    "--non-interactive",
                    "--accept-risk",
                    "--mode",
                    "local",
                    "--workspace",
                    str(Path.home() / ".openclaw" / "workspace"),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=90,
                **hidden_subprocess_kwargs(),
            )
        subprocess.run(
            [command, "mcp", "unset", MCP_SERVER_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            **hidden_subprocess_kwargs(),
        )
        for legacy_name in LEGACY_MCP_SERVER_NAMES:
            subprocess.run(
                [command, "mcp", "unset", legacy_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                **hidden_subprocess_kwargs(),
            )
        configure_args = [
            command,
            "mcp",
            "add",
            MCP_SERVER_NAME,
            "--command",
            server["command"],
        ]
        for argument in server["args"]:
            configure_args.append(f"--arg={argument}")
        if server.get("cwd"):
            configure_args.extend(["--cwd", server["cwd"]])
        configure_args.extend(
            [
                "--connect-timeout",
                "30",
                "--timeout",
                "600",
                "--parallel",
            ]
        )
        completed = subprocess.run(
            configure_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **hidden_subprocess_kwargs(),
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            return _result("openclaw", "error", f"OpenClaw MCP auto-setup failed: {detail}", config_path)
        subprocess.run(
            [command, "exec-policy", "preset", "yolo"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            **hidden_subprocess_kwargs(),
        )
    except Exception as exc:
        return _result("openclaw", "error", f"OpenClaw MCP auto-setup failed: {exc}", config_path)
    return _result(
        "openclaw",
        "configured",
        f"OpenClaw MCP server '{MCP_SERVER_NAME}' configured with yolo exec policy",
        config_path,
    )


def cursor_command() -> str | None:
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return resolve_command(
        ["cursor-agent.cmd", "cursor-agent", "agent.cmd", "agent", "cursor"],
        [
            local_app_data / "cursor-agent" / "agent.cmd",
            local_app_data / "cursor-agent" / "cursor-agent.cmd",
        ],
    )
