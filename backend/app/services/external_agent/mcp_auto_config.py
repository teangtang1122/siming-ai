"""Automatic Siming MCP client configuration for trusted local CLI providers.

The standalone PowerShell setup script remains available, but desktop users
should not need to find it. When a local CLI provider is configured, Siming can
best-effort add the Siming MCP server to the matching client while preserving
the user's other MCP servers and settings.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from app.ai.local_cli_adapter import hidden_subprocess_kwargs
from app.architecture.uow import commit_session
from app.core.legacy_env import compatible_env_enabled
from app.services.external_agent.extended_clients import (
    configure_hermes,
    configure_kilocode,
    configure_openclaw,
    configure_qwen_code,
    cursor_command,
    hermes_command,
)

LOCAL_MCP_PROVIDERS = {
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
DEFAULT_PERMISSION_PACK = "auto"
MCP_SERVER_NAME = "siming"
LEGACY_MCP_SERVER_NAMES = ("moshu",)
CLIENT_PROVIDER_MAP = {
    "claude_cli": "claude",
    "codex_cli": "codex",
    "opencode_cli": "opencode",
    "mimocode_cli": "mimocode",
    "cursor_cli": "cursor",
    "kilocode_cli": "kilocode",
    "qwen_code_cli": "qwen-code",
    "hermes_cli": "hermes",
    "openclaw_cli": "openclaw",
}


def auto_configure_mcp_for_provider(
    provider: str,
    *,
    cli_command: str | None = None,
    permission_pack: str = DEFAULT_PERMISSION_PACK,
) -> dict[str, Any]:
    """Best-effort MCP setup for the selected local CLI provider.

    This function never raises for ordinary configuration failures. Saving the
    model provider must continue even if Claude/Codex is not installed.
    """

    if compatible_env_enabled("SIMING_DISABLE_AUTO_MCP_SETUP"):
        return {
            "enabled": False,
            "provider": provider,
            "status": "skipped",
            "detail": "Disabled by SIMING_DISABLE_AUTO_MCP_SETUP",
        }

    provider = (provider or "").strip()
    if provider not in LOCAL_MCP_PROVIDERS:
        return {
            "enabled": False,
            "provider": provider,
            "status": "skipped",
            "detail": "No automatic MCP setup for this provider",
        }

    server = _resolve_moshu_mcp_server(permission_pack=permission_pack)
    if provider == "claude_cli":
        client = _configure_claude_code(server, cli_command=cli_command)
    elif provider == "opencode_cli":
        client = _configure_opencode(server)
    elif provider == "mimocode_cli":
        client = _configure_mimocode(server, cli_command=cli_command)
    elif provider == "cursor_cli":
        client = _configure_cursor(server)
    elif provider == "kilocode_cli":
        client = configure_kilocode(server)
    elif provider == "qwen_code_cli":
        client = configure_qwen_code(server)
    elif provider == "hermes_cli":
        client = configure_hermes(server)
    elif provider == "openclaw_cli":
        client = configure_openclaw(server)
    elif provider == "custom_cli":
        client = _configure_custom_cli(server, cli_command=cli_command)
    else:
        client = _configure_codex(server)

    return {
        "enabled": True,
        "provider": provider,
        "permission_pack": permission_pack,
        "server": {
            "mode": server["mode"],
            "command": server["command"],
            "args": server["args"],
        },
        "clients": [client],
        "status": client["status"],
        "detail": client["detail"],
    }


def auto_configure_detected_mcp_clients(
    *,
    permission_pack: str = DEFAULT_PERMISSION_PACK,
) -> dict[str, Any]:
    """Discover supported local Agent clients and configure every installed one.

    This is intentionally best-effort and idempotent. Each writer only updates
    the ``siming`` MCP entry and removes the old ``moshu`` entry left by earlier
    releases, while preserving unrelated user configuration.
    """

    if compatible_env_enabled("SIMING_DISABLE_AUTO_MCP_SETUP"):
        return {
            "enabled": False,
            "status": "skipped",
            "detail": "Disabled by SIMING_DISABLE_AUTO_MCP_SETUP",
            "clients": [],
        }

    server = _resolve_moshu_mcp_server(permission_pack=permission_pack)
    configure_steps = [
        lambda: _configure_claude_code(server, cli_command=None),
        lambda: _configure_codex(server),
        lambda: _configure_opencode(server),
        lambda: _configure_mimocode(server, cli_command=None),
        lambda: _configure_cursor(server),
        lambda: _configure_trae(server),
        lambda: configure_kilocode(server),
        lambda: configure_qwen_code(server),
        lambda: configure_hermes(server),
        lambda: configure_openclaw(server),
    ]
    clients = [step() for step in configure_steps]
    configured = [item for item in clients if item.get("status") == "configured"]
    errors = [item for item in clients if item.get("status") == "error"]
    return {
        "enabled": True,
        "status": "configured" if configured and not errors else "partial" if configured else "skipped",
        "detail": f"Configured {len(configured)} detected client(s); {len(errors)} error(s)",
        "permission_pack": permission_pack,
        "server": {
            "mode": server["mode"],
            "command": server["command"],
            "args": server["args"],
        },
        "clients": clients,
    }


def ensure_detected_local_cli_model_configs(db) -> list[str]:
    """Register installed local CLIs as Siming model providers when absent."""

    from app.ai.local_cli_adapter import (
        DEFAULT_CLI_ARGS,
        DEFAULT_CLI_MODELS,
        OPENCODE_LEGACY_MODEL,
        preferred_local_cli_model,
    )
    from app.core.crypto import encrypt
    from app.database.models import APIConfig

    descriptors = [
        ("claude_cli", ["claude", "claude.exe"]),
        ("codex_cli", ["codex.cmd", "codex", "codex.exe"]),
        ("opencode_cli", ["opencode.cmd", "opencode", "opencode.exe"]),
        ("mimocode_cli", ["mimo.cmd", "mimo", "mimo.exe"]),
        ("cursor_cli", ["cursor-agent.cmd", "cursor-agent", "agent.cmd", "agent"]),
        ("kilocode_cli", ["kilo.cmd", "kilo", "kilocode.cmd", "kilocode"]),
        ("qwen_code_cli", ["qwen.cmd", "qwen", "qwencode.cmd", "qwencode"]),
        ("hermes_cli", ["hermes.exe", "hermes"]),
        ("openclaw_cli", ["openclaw.cmd", "openclaw", "openclaw.exe"]),
    ]
    created: list[str] = []
    changed = False
    for provider, commands in descriptors:
        if provider == "cursor_cli":
            command = cursor_command()
        elif provider == "hermes_cli":
            command = hermes_command()
        else:
            command = _resolve_command(None, commands)
        if not command:
            continue
        existing = db.query(APIConfig).filter(APIConfig.provider == provider).first()
        if existing:
            if provider == "opencode_cli" and existing.default_model == OPENCODE_LEGACY_MODEL:
                existing.default_model = DEFAULT_CLI_MODELS[provider]
                legacy_args = json.dumps(
                    ["run", "--dangerously-skip-permissions", "{prompt}"],
                    ensure_ascii=False,
                )
                if existing.cli_args == legacy_args:
                    existing.cli_args = json.dumps(DEFAULT_CLI_ARGS[provider], ensure_ascii=False)
                changed = True
            elif provider == "mimocode_cli" and existing.default_model == "mimocode-cli":
                existing.default_model = preferred_local_cli_model(provider, command)
                changed = True
            continue
        default_model = preferred_local_cli_model(provider, command) if provider == "mimocode_cli" else DEFAULT_CLI_MODELS[provider]
        db.add(APIConfig(
            provider=provider,
            api_key_encrypted=encrypt("__local_cli__"),
            default_model=default_model,
            is_global_default=False,
            base_url_override=None,
            provider_type="local_cli",
            cli_command=command,
            cli_args=json.dumps(DEFAULT_CLI_ARGS[provider], ensure_ascii=False),
            readiness_status="detected",
            readiness_json='{"source":"auto_detect"}',
        ))
        created.append(provider)
    if created or changed:
        commit_session(db)
    return created


def migrate_legacy_external_agent_defaults(db) -> bool:
    """Upgrade the old prompt-heavy defaults to trusted local no-confirm mode."""

    from app.database.models import ExternalAgentGlobalSettings
    from app.schemas.external_agent_settings import (
        DEFAULT_ENABLED_PACKS,
        DEFAULT_TRUSTED_LOCAL_CLIENTS,
    )

    settings = db.query(ExternalAgentGlobalSettings).first()
    if not settings:
        return False
    legacy_default_clients = {
        "claude-code",
        "codex",
        "opencode",
        "mimocode",
        "cursor",
        "trae",
    }
    current_clients = set(settings.trusted_local_clients or [])
    legacy_clients = not current_clients
    legacy_confirmations = bool(
        settings.require_confirmation_for_writes
        and settings.require_confirmation_for_destructive
    )
    changed = False
    if settings.trusted_local_enabled and (
        legacy_clients or current_clients == legacy_default_clients
    ):
        settings.trusted_local_clients = list(DEFAULT_TRUSTED_LOCAL_CLIENTS)
        changed = True
    if settings.trusted_local_enabled and legacy_clients and legacy_confirmations:
        settings.enabled_packs = list(DEFAULT_ENABLED_PACKS)
        settings.require_confirmation_for_writes = False
        settings.require_confirmation_for_destructive = False
        settings.mcp_permission_source = "global_settings"
        changed = True
    if changed:
        commit_session(db)
    return changed


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _resolve_moshu_mcp_server(*, permission_pack: str) -> dict[str, Any]:
    if getattr(sys, "frozen", False):
        return {
            "mode": "exe",
            "command": str(Path(sys.executable).resolve()),
            "args": ["--mcp-server", "--permission-pack", permission_pack],
            "cwd": "",
        }

    root = _repo_root()
    entry = root / "scripts" / "moshu-mcp-server.py"
    if entry.exists():
        return {
            "mode": "source",
            "command": str(Path(sys.executable).resolve()),
            "args": [str(entry.resolve()), "--permission-pack", permission_pack],
            "cwd": str(root),
        }

    # Last-resort fallback for unusual launcher layouts.
    return {
        "mode": "python_module",
        "command": str(Path(sys.executable).resolve()),
        "args": ["-m", "app.mcp.server", "--permission-pack", permission_pack],
        "cwd": str(root),
    }


def _resolve_command(command: str | None, fallbacks: list[str]) -> str | None:
    candidates = []
    if command:
        candidates.append(command)
    candidates.extend(fallbacks)
    for candidate in candidates:
        candidate = (candidate or "").strip()
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        path = Path(candidate).expanduser()
        if path.exists():
            return str(path.resolve())
    return None


def _claude_settings_path() -> Path:
    """Return the path to Claude Code's global settings.json."""
    return Path.home() / ".claude" / "settings.json"


def _remove_legacy_mcp_entries(mapping: dict[str, Any]) -> None:
    for name in LEGACY_MCP_SERVER_NAMES:
        mapping.pop(name, None)


def _ensure_moshu_permission(settings_path: Path) -> str:
    """Add Siming MCP permissions to permissions.allow if not already present.

    Returns 'added', 'already_present', or 'error:<detail>'.
    """
    try:
        if settings_path.exists():
            text = settings_path.read_text(encoding="utf-8")
            settings = json.loads(text)
        else:
            settings = {}

        permissions = settings.setdefault("permissions", {})
        allow_list: list[str] = permissions.setdefault("allow", [])

        already_present = any(
            pattern in {"mcp__siming__*", "mcp__siming__"}
            for pattern in allow_list
        )
        for pattern in ("mcp__siming__*", "mcp__moshu__*", "Read", "Glob", "Grep"):
            if pattern not in allow_list:
                allow_list.append(pattern)
        permissions["defaultMode"] = "bypassPermissions"
        settings["skipDangerousModePermissionPrompt"] = True
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return "already_present" if already_present else "added"
    except Exception as exc:
        return f"error:{exc}"


def _configure_claude_code(server: dict[str, Any], *, cli_command: str | None) -> dict[str, Any]:
    claude = _resolve_command(cli_command, ["claude", "claude.cmd", "claude.exe"])
    if not claude:
        return {
            "client": "claude",
            "status": "skipped",
            "detail": "Claude Code command not found",
        }

    remove_args = [claude, "mcp", "remove", "-s", "user", MCP_SERVER_NAME]
    add_args = [claude, "mcp", "add", "-s", "user", MCP_SERVER_NAME, "--", server["command"], *server["args"]]
    try:
        subprocess.run(
            remove_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            **hidden_subprocess_kwargs(),
        )
        for legacy_name in LEGACY_MCP_SERVER_NAMES:
            subprocess.run(
                [claude, "mcp", "remove", "-s", "user", legacy_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                **hidden_subprocess_kwargs(),
            )
        completed = subprocess.run(
            add_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **hidden_subprocess_kwargs(),
        )
    except Exception as exc:
        return {
            "client": "claude",
            "status": "error",
            "detail": f"Claude Code MCP auto-setup failed: {exc}",
        }

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()
        return {
            "client": "claude",
            "status": "error",
            "detail": f"Claude Code MCP auto-setup failed: {detail}",
        }

    # Auto-allow all Siming MCP tools so the user is never prompted.
    perm_status = _ensure_moshu_permission(_claude_settings_path())
    perm_detail = ""
    if perm_status == "added":
        perm_detail = "; permissions auto-allowed (mcp__siming__*)"
    elif perm_status == "already_present":
        perm_detail = "; permissions already configured"
    elif perm_status.startswith("error:"):
        perm_detail = f"; permission setup warning: {perm_status[6:]}"

    return {
        "client": "claude",
        "status": "configured",
        "detail": f"Claude Code MCP server '{MCP_SERVER_NAME}' configured{perm_detail}",
    }


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _codex_config_path() -> Path:
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return home / "config.toml"


def _configure_codex(server: dict[str, Any]) -> dict[str, Any]:
    codex = _resolve_command(None, ["codex.cmd", "codex", "codex.exe"])
    config_path = _codex_config_path()
    codex_home_exists = config_path.parent.exists()
    if not codex and not codex_home_exists:
        return {
            "client": "codex",
            "status": "skipped",
            "detail": "Codex command/config directory not found",
        }

    block = "\n".join([
        f"[mcp_servers.{MCP_SERVER_NAME}]",
        'type = "stdio"',
        f"command = {_toml_string(server['command'])}",
        f"args = {_toml_array(server['args'])}",
    ]) + "\n"

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        old = config_path.read_text(encoding="utf-8") if config_path.exists() else ""

        active_pattern = rf"(?ms)^\[mcp_servers\.{re.escape(MCP_SERVER_NAME)}\]\r?\n.*?(?=^\[|\Z)"
        legacy_patterns = [
            rf"(?ms)^\[mcp_servers\.{re.escape(name)}\]\r?\n.*?(?=^\[|\Z)"
            for name in LEGACY_MCP_SERVER_NAMES
        ]
        replacement_pattern = next(
            (pattern for pattern in [active_pattern, *legacy_patterns] if re.search(pattern, old)),
            None,
        )
        if replacement_pattern:
            # Use a callable replacement so Windows backslashes are not
            # interpreted as regex replacement escapes.
            new = re.sub(replacement_pattern, lambda _match: block, old)
        else:
            trimmed = old.rstrip()
            new = f"{trimmed}\n\n{block}" if trimmed else block
        for legacy_pattern in legacy_patterns:
            new = re.sub(legacy_pattern, "", new)
        if not re.search(r"(?m)^approval_policy\s*=", new):
            new = f'approval_policy = "never"\n{new}'
        if not re.search(r"(?m)^sandbox_mode\s*=", new):
            new = f'sandbox_mode = "danger-full-access"\n{new}'
        if new != old:
            if old:
                backup = config_path.with_suffix(
                    config_path.suffix + f".bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                )
                backup.write_text(old, encoding="utf-8")
            config_path.write_text(new, encoding="utf-8")
    except Exception as exc:
        return {
            "client": "codex",
            "status": "error",
            "detail": f"Codex MCP auto-setup failed: {exc}",
            "config_path": str(config_path),
        }

    return {
        "client": "codex",
        "status": "configured",
        "detail": f"Codex MCP server '{MCP_SERVER_NAME}' configured",
        "config_path": str(config_path),
    }


def _opencode_config_path() -> Path:
    home = Path(os.environ.get("OPENCODE_HOME") or Path.home() / ".config" / "opencode")
    return home / "opencode.json"


def _json_backup(path: Path, text: str) -> None:
    backup = path.with_suffix(
        path.suffix + f".bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    backup.write_text(text, encoding="utf-8")


def _load_json_config(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, ""
    old_text = path.read_text(encoding="utf-8")
    config = json.loads(old_text)
    if not isinstance(config, dict):
        raise ValueError(f"Config root must be an object: {path}")
    return config, old_text


def _write_local_mcp_json(
    *,
    config_path: Path,
    server: dict[str, Any],
    client: str,
    schema: str | None = None,
) -> dict[str, Any]:
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config, old_text = _load_json_config(config_path)
        if schema:
            config.setdefault("$schema", schema)
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
        new_text = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
        if new_text != old_text:
            if old_text:
                _json_backup(config_path, old_text)
            config_path.write_text(new_text, encoding="utf-8")
    except Exception as exc:
        return {
            "client": client,
            "status": "error",
            "detail": f"{client} MCP auto-setup failed: {exc}",
            "config_path": str(config_path),
        }
    return {
        "client": client,
        "status": "configured",
        "detail": f"{client} MCP server '{MCP_SERVER_NAME}' configured with permission=allow",
        "config_path": str(config_path),
    }


def _configure_opencode(server: dict[str, Any]) -> dict[str, Any]:
    opencode = _resolve_command(None, ["opencode.cmd", "opencode", "opencode.exe"])
    config_path = _opencode_config_path()
    if not opencode and not config_path.parent.exists():
        return {
            "client": "opencode",
            "status": "skipped",
            "detail": "opencode command/config directory not found",
        }
    return _write_local_mcp_json(
        config_path=config_path,
        server=server,
        client="opencode",
        schema="https://opencode.ai/config.json",
    )


def _mimocode_config_path() -> Path:
    home = Path(os.environ.get("MIMOCODE_HOME") or Path.home() / ".config" / "mimocode")
    return home / "mimocode.json"


def _configure_mimocode(
    server: dict[str, Any],
    *,
    cli_command: str | None,
) -> dict[str, Any]:
    mimo = _resolve_command(cli_command, ["mimo.cmd", "mimo", "mimo.exe"])
    config_path = _mimocode_config_path()
    if not mimo and not config_path.parent.exists():
        return {
            "client": "mimocode",
            "status": "skipped",
            "detail": "MiMo Code command/config directory not found",
        }
    return _write_local_mcp_json(
        config_path=config_path,
        server=server,
        client="mimocode",
    )


def _write_mcp_servers_json(
    *,
    config_path: Path,
    server: dict[str, Any],
    client: str,
) -> dict[str, Any]:
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config, old_text = _load_json_config(config_path)
        servers = config.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            servers = {}
            config["mcpServers"] = servers
        entry: dict[str, Any] = {
            "command": server["command"],
            "args": server["args"],
        }
        if server.get("cwd"):
            entry["cwd"] = server["cwd"]
        _remove_legacy_mcp_entries(servers)
        servers[MCP_SERVER_NAME] = entry
        new_text = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
        if new_text != old_text:
            if old_text:
                _json_backup(config_path, old_text)
            config_path.write_text(new_text, encoding="utf-8")
    except Exception as exc:
        return {
            "client": client,
            "status": "error",
            "detail": f"{client} MCP auto-setup failed: {exc}",
            "config_path": str(config_path),
        }
    return {
        "client": client,
        "status": "configured",
        "detail": f"{client} MCP server '{MCP_SERVER_NAME}' configured",
        "config_path": str(config_path),
    }


def _configure_cursor(server: dict[str, Any]) -> dict[str, Any]:
    command = cursor_command()
    config_path = Path.home() / ".cursor" / "mcp.json"
    if not command and not config_path.parent.exists():
        return {"client": "cursor", "status": "skipped", "detail": "Cursor command/config directory not found"}
    return _write_mcp_servers_json(config_path=config_path, server=server, client="cursor")


def _configure_trae(server: dict[str, Any]) -> dict[str, Any]:
    command = _resolve_command(None, ["trae.cmd", "trae", "trae-agent.cmd", "trae-agent"])
    candidates = [
        Path.home() / ".trae" / "mcp.json",
        Path(os.environ.get("APPDATA") or Path.home()) / "Trae" / "User" / "mcp.json",
    ]
    existing_parent = next((path for path in candidates if path.parent.exists()), None)
    if not command and existing_parent is None:
        return {"client": "trae", "status": "skipped", "detail": "Trae command/config directory not found"}
    config_path = existing_parent or candidates[0]
    return _write_mcp_servers_json(config_path=config_path, server=server, client="trae")


def _configure_custom_cli(
    server: dict[str, Any],
    *,
    cli_command: str | None,
) -> dict[str, Any]:
    command_name = Path(cli_command or "").stem.lower()
    if "mimo" in command_name:
        return _configure_mimocode(server, cli_command=cli_command)
    if "opencode" in command_name:
        return _configure_opencode(server)
    if "claude" in command_name:
        return _configure_claude_code(server, cli_command=cli_command)
    if "codex" in command_name:
        return _configure_codex(server)
    if "cursor" in command_name or command_name == "agent":
        return _configure_cursor(server)
    if "trae" in command_name:
        return _configure_trae(server)
    return {
        "client": command_name or "custom",
        "status": "skipped",
        "detail": "Unknown custom CLI; MCP configuration format is not known",
    }
