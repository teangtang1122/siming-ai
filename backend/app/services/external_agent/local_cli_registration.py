"""Persist explicitly discovered local CLI model providers."""
from __future__ import annotations

import json
from collections.abc import Callable

from app.architecture.uow import commit_session


def ensure_detected_local_cli_model_configs(
    db,
    *,
    explicit_consent: bool,
    resolve_command: Callable[[str | None, list[str]], str | None],
    cursor_command: Callable[[], str | None],
    hermes_command: Callable[[], str | None],
) -> list[str]:
    """Register installed CLIs only inside an explicitly authorized flow."""

    if not explicit_consent:
        return []

    from app.ai.local_cli_adapter import (
        DEFAULT_CLI_ARGS,
        DEFAULT_CLI_MODELS,
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
        ("dsh_cli", ["dsh.cmd", "dsh", "dsh.exe"]),
    ]
    created: list[str] = []
    changed = False
    for provider, commands in descriptors:
        if provider == "cursor_cli":
            command = cursor_command()
        elif provider == "hermes_cli":
            command = hermes_command()
        else:
            command = resolve_command(None, commands)
        if not command:
            continue
        existing = db.query(APIConfig).filter(APIConfig.provider == provider).first()
        if existing:
            if provider == "mimocode_cli" and existing.default_model == "mimocode-cli":
                existing.default_model = preferred_local_cli_model(provider, command)
                changed = True
            continue
        default_model = (
            preferred_local_cli_model(provider, command)
            if provider in {"opencode_cli", "mimocode_cli"}
            else DEFAULT_CLI_MODELS[provider]
        )
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
