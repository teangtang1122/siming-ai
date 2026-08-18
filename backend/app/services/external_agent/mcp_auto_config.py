"""User-triggered Siming MCP configuration for trusted local CLI providers.

Scanning, configuration and restoration are invoked only from explicit UI/API
actions. Existing client settings are preserved and every write is recorded so
the user can restore the previous state.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import tomllib
import uuid
from base64 import b64decode, b64encode
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml

from app.ai.local_cli_adapter import hidden_subprocess_kwargs
from app.architecture.uow import commit_session
from app.core.crypto import decrypt, encrypt
from app.core.legacy_env import compatible_env_enabled
from app.services.application_settings import app_home
from app.services.external_agent.extended_clients import (
    configure_hermes,
    configure_kilocode,
    configure_openclaw,
    configure_qwen_code,
    cursor_command,
    hermes_command,
)
from app.services.external_agent.mcp_server_spec import resolve_siming_mcp_server

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
    "trae_cli",
    "custom_cli",
}
DEFAULT_PERMISSION_PACK = "auto"
MCP_SERVER_NAME = "siming"
LEGACY_MCP_SERVER_NAMES = ("moshu",)
# OpenCode 1.x applies this per-server timeout to MCP catalog and tool calls.
# Novel planning and long-form write/archive operations can legitimately run
# for much longer than the client's historical five-second default.
OPENCODE_MCP_TIMEOUT_MS = 12 * 60 * 60 * 1000
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
    "trae_cli": "trae",
}

CLI_INTEGRATION_LABELS = {
    "claude_cli": "Claude Code",
    "codex_cli": "Codex CLI",
    "opencode_cli": "OpenCode",
    "mimocode_cli": "MiMo Code",
    "cursor_cli": "Cursor Agent",
    "trae_cli": "Trae",
    "kilocode_cli": "Kilo Code",
    "qwen_code_cli": "Qwen Code",
    "hermes_cli": "Hermes Agent",
    "openclaw_cli": "OpenClaw",
}

CLI_INTEGRATION_COMMANDS = {
    "claude_cli": ["claude", "claude.cmd", "claude.exe"],
    "codex_cli": ["codex.cmd", "codex", "codex.exe"],
    "opencode_cli": ["opencode.cmd", "opencode", "opencode.exe"],
    "mimocode_cli": ["mimo.cmd", "mimo", "mimo.exe"],
    "cursor_cli": ["cursor-agent.cmd", "cursor-agent", "agent.cmd", "agent", "cursor"],
    "trae_cli": ["trae.cmd", "trae", "trae-agent.cmd", "trae-agent"],
    "kilocode_cli": ["kilo.cmd", "kilo", "kilocode.cmd", "kilocode"],
    "qwen_code_cli": ["qwen.cmd", "qwen", "qwencode.cmd", "qwencode"],
    "hermes_cli": ["hermes.exe", "hermes"],
    "openclaw_cli": ["openclaw.cmd", "openclaw", "openclaw.exe"],
}

_CONFIG_TRANSACTION_LOCK = threading.Lock()


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
        client = _configure_opencode(server, cli_command=cli_command)
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
    elif provider == "trae_cli":
        client = _configure_trae(server)
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


def _candidate_config_paths(provider: str) -> list[Path]:
    app_data = Path(os.environ.get("APPDATA") or Path.home())
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    paths: dict[str, list[Path]] = {
        "claude_cli": [Path.home() / ".claude.json", _claude_settings_path()],
        "codex_cli": [_codex_config_path()],
        "opencode_cli": [_opencode_config_path()],
        "mimocode_cli": [_mimocode_config_path()],
        "cursor_cli": [Path.home() / ".cursor" / "mcp.json"],
        "trae_cli": [Path.home() / ".trae" / "mcp.json", app_data / "Trae" / "User" / "mcp.json"],
        "kilocode_cli": [Path.home() / ".config" / "kilo" / "kilo.jsonc"],
        "qwen_code_cli": [Path.home() / ".qwen" / "settings.json"],
        "hermes_cli": [
            Path(os.environ.get("HERMES_HOME") or local_app_data / "hermes") / "config.yaml"
        ],
        "openclaw_cli": [Path.home() / ".openclaw" / "openclaw.json"],
    }
    return paths.get(provider, [])


def _resolved_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _file_hash(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return sha256(path.read_bytes()).hexdigest()


def _transaction_path(provider: str) -> Path:
    if provider not in CLI_INTEGRATION_LABELS:
        raise ValueError("Unsupported CLI integration")
    return app_home() / "cli-integration-backups" / f"{provider}.json"


def _read_transaction(provider: str) -> dict[str, Any] | None:
    path = _transaction_path(provider)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) and payload.get("provider") == provider else None


def _write_transaction(provider: str, payload: dict[str, Any]) -> None:
    path = _transaction_path(provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _replace_file_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _snapshot(path: Path) -> dict[str, Any]:
    resolved = _resolved_path(path)
    exists = resolved.exists() and resolved.is_file()
    content = resolved.read_bytes() if exists else b""
    return {
        "path": str(resolved),
        "pre_exists": exists,
        "pre_hash": sha256(content).hexdigest() if exists else None,
        # CLI files can contain credentials. The rollback copy is encrypted at
        # rest and is never included in an API response.
        "pre_content_encrypted": encrypt(b64encode(content).decode("ascii")) if exists else None,
    }


def _contains_siming_mcp_mapping(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[-_]", "", str(key)).lower()
            if (
                normalized in {"mcp", "mcpservers"}
                and isinstance(child, dict)
                and any(str(server_name).lower() == MCP_SERVER_NAME for server_name in child)
            ):
                return True
            if _contains_siming_mcp_mapping(child):
                return True
    elif isinstance(value, list):
        return any(_contains_siming_mcp_mapping(child) for child in value)
    return False


def _configuration_marker_present(path: Path) -> bool:
    try:
        if not path.exists() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            return False
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return False
    for loader in (json.loads, tomllib.loads, yaml.safe_load):
        try:
            if _contains_siming_mcp_mapping(loader(text)):
                return True
        except (TypeError, ValueError, yaml.YAMLError):
            continue
    # JSONC cannot be parsed by the standard JSON loader. Keep this fallback
    # structural and scoped to an MCP mapping; a permission wildcard alone
    # must not make an unconfigured client appear connected.
    return bool(re.search(
        r"(?is)[\"']?(?:mcp|mcpservers|mcp_servers)[\"']?\s*[:=]\s*\{.{0,262144}?[\"']siming[\"']\s*:",
        text,
    )) or bool(re.search(r"(?im)^\s*\[mcp_servers\.siming\]\s*$", text))


def scan_cli_integrations() -> dict[str, Any]:
    """Read-only discovery, called only after an explicit UI action."""

    clients: list[dict[str, Any]] = []
    for provider, label in CLI_INTEGRATION_LABELS.items():
        if provider == "cursor_cli":
            command = cursor_command()
        elif provider == "hermes_cli":
            command = hermes_command()
        else:
            command = _resolve_command(None, CLI_INTEGRATION_COMMANDS[provider])
        config_paths = _candidate_config_paths(provider)
        existing_paths = [_resolved_path(path) for path in config_paths if _resolved_path(path).exists()]
        transaction = _read_transaction(provider)
        can_restore = bool(transaction and not transaction.get("restored"))
        detected = bool(command or existing_paths or can_restore)
        if not detected:
            continue
        clients.append({
            "provider": provider,
            "label": label,
            "detected": True,
            "command": command,
            "config_path": str(existing_paths[0]) if existing_paths else None,
            "configured": any(_configuration_marker_present(path) for path in existing_paths),
            "can_restore": can_restore,
        })
    return {
        "status": "scanned",
        "clients": clients,
        "detected_count": len(clients),
        "supported_count": len(CLI_INTEGRATION_LABELS),
    }


def configure_cli_integration(
    provider: str,
    *,
    cli_command: str | None = None,
    permission_pack: str = DEFAULT_PERMISSION_PACK,
) -> dict[str, Any]:
    """Configure one CLI after its dedicated consent action."""

    if provider not in CLI_INTEGRATION_LABELS:
        return {"provider": provider, "status": "error", "detail": "Unsupported CLI integration"}
    with _CONFIG_TRANSACTION_LOCK:
        before = {
            str(_resolved_path(path)): _snapshot(path)
            for path in _candidate_config_paths(provider)
        }
        try:
            result = auto_configure_mcp_for_provider(
                provider,
                cli_command=cli_command,
                permission_pack=permission_pack,
            )
        except Exception as exc:
            # A third-party writer can fail after touching one of its files.
            # Preserve the pre-action snapshot so the author can still undo
            # that partial change from the same per-CLI control.
            result = {
                "enabled": True,
                "provider": provider,
                "status": "error",
                "detail": f"CLI 配置未完成：{exc}",
            }
        changed_files: list[dict[str, Any]] = []
        for path_text, snapshot in before.items():
            path = Path(path_text)
            post_exists = path.exists() and path.is_file()
            post_hash = _file_hash(path)
            if snapshot["pre_exists"] != post_exists or snapshot["pre_hash"] != post_hash:
                changed_files.append({
                    **snapshot,
                    "post_exists": post_exists,
                    "post_hash": post_hash,
                })
        if changed_files:
            _write_transaction(provider, {
                "version": 1,
                "provider": provider,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "restored": False,
                "configure_status": result.get("status"),
                "files": changed_files,
            })
        transaction = _read_transaction(provider)
        return {
            **result,
            "label": CLI_INTEGRATION_LABELS[provider],
            "configured": result.get("status") == "configured",
            "changed": bool(changed_files),
            "can_restore": bool(transaction and not transaction.get("restored")),
        }


def restore_cli_integration(provider: str) -> dict[str, Any]:
    """Restore the exact pre-configuration files if they remain unchanged."""

    if provider not in CLI_INTEGRATION_LABELS:
        return {"provider": provider, "status": "error", "detail": "Unsupported CLI integration"}
    with _CONFIG_TRANSACTION_LOCK:
        transaction = _read_transaction(provider)
        if not transaction or transaction.get("restored"):
            return {
                "provider": provider,
                "label": CLI_INTEGRATION_LABELS[provider],
                "status": "skipped",
                "detail": "没有可还原的司命配置记录",
                "can_restore": False,
            }
        allowed_paths = {str(_resolved_path(path)) for path in _candidate_config_paths(provider)}
        files = transaction.get("files") if isinstance(transaction.get("files"), list) else []
        conflicts: list[str] = []
        for item in files:
            path_text = str(item.get("path") or "")
            if path_text not in allowed_paths:
                conflicts.append(path_text or "unknown")
                continue
            path = Path(path_text)
            current_exists = path.exists() and path.is_file()
            if current_exists != bool(item.get("post_exists")) or _file_hash(path) != item.get("post_hash"):
                conflicts.append(path_text)
        if conflicts:
            return {
                "provider": provider,
                "label": CLI_INTEGRATION_LABELS[provider],
                "status": "conflict",
                "detail": "配置后文件又被修改，为避免覆盖你的新内容，司命没有执行还原。",
                "conflicts": conflicts,
                "can_restore": True,
            }
        current_states: dict[str, bytes | None] = {}
        for item in files:
            path = Path(str(item["path"]))
            current_states[str(path)] = path.read_bytes() if path.exists() and path.is_file() else None
        try:
            for item in files:
                path = Path(str(item["path"]))
                if item.get("pre_exists"):
                    encoded = decrypt(str(item["pre_content_encrypted"]))
                    content = b64decode(encoded.encode("ascii"))
                    _replace_file_bytes(path, content)
                elif path.exists():
                    path.unlink()
        except Exception as exc:
            compensation_errors: list[str] = []
            for path_text, content in current_states.items():
                path = Path(path_text)
                try:
                    if content is None:
                        if path.exists():
                            path.unlink()
                    else:
                        _replace_file_bytes(path, content)
                except Exception:
                    compensation_errors.append(path_text)
            detail = f"还原失败，已恢复到还原操作前的状态：{exc}"
            if compensation_errors:
                detail = f"还原失败，且以下文件未能自动恢复：{', '.join(compensation_errors)}"
            return {
                "provider": provider,
                "label": CLI_INTEGRATION_LABELS[provider],
                "status": "error",
                "detail": detail,
                "can_restore": True,
            }
        transaction["restored"] = True
        transaction["restored_at"] = datetime.utcnow().isoformat() + "Z"
        _write_transaction(provider, transaction)
        return {
            "provider": provider,
            "label": CLI_INTEGRATION_LABELS[provider],
            "status": "restored",
            "detail": "已还原为司命配置前的文件内容",
            "configured": False,
            "can_restore": False,
        }


def auto_configure_detected_mcp_clients(
    *,
    permission_pack: str = DEFAULT_PERMISSION_PACK,
    explicit_consent: bool = False,
) -> dict[str, Any]:
    """Legacy bulk helper guarded by an explicit consent flag.

    Product flows use per-client configuration instead. The default is a
    no-op so an old caller can never resume silent bulk modification.
    """

    if not explicit_consent:
        return {
            "enabled": False,
            "status": "consent_required",
            "detail": "Explicit user consent is required before bulk CLI configuration",
            "clients": [],
        }

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


def ensure_detected_local_cli_model_configs(db, *, explicit_consent: bool = False) -> list[str]:
    """Register installed CLIs only inside an explicitly authorized flow."""

    if not explicit_consent:
        return []

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


def migrate_legacy_external_agent_defaults(db, *, explicit_consent: bool = False) -> bool:
    """Upgrade legacy trust defaults only after explicit authorization."""

    if not explicit_consent:
        return False

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


def _resolve_moshu_mcp_server(*, permission_pack: str) -> dict[str, Any]:
    # Compatibility wrapper retained for integrations/tests that imported the
    # historical private helper. Resolution itself is shared with transient
    # in-chat MCP injection and never writes client configuration.
    return resolve_siming_mcp_server(permission_pack=permission_pack)


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

    return {
        "client": "claude",
        "status": "configured",
        "detail": (
            f"Claude Code MCP server '{MCP_SERVER_NAME}' configured; "
            "existing permission settings preserved"
        ),
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
        mcp = config.setdefault("mcp", {})
        if not isinstance(mcp, dict):
            mcp = {}
            config["mcp"] = mcp
        _remove_legacy_mcp_entries(mcp)
        mcp[MCP_SERVER_NAME] = {
            "type": "local",
            "command": [server["command"], *server["args"]],
            "enabled": True,
            "timeout": OPENCODE_MCP_TIMEOUT_MS,
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
        "detail": (
            f"{client} MCP server '{MCP_SERVER_NAME}' configured; "
            "existing permission settings preserved"
        ),
        "config_path": str(config_path),
    }


def _configure_opencode(server: dict[str, Any], *, cli_command: str | None = None) -> dict[str, Any]:
    opencode = _resolve_command(cli_command, ["opencode.cmd", "opencode", "opencode.exe"])
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
        return _configure_opencode(server, cli_command=cli_command)
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
