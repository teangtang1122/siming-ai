"""Persistent launcher preferences shared by the API and packaged runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..core.legacy_env import get_compatible_env
from ..updater import resolve_update_channel


def app_home() -> Path:
    configured = get_compatible_env("SIMING_HOME")
    if configured:
        return Path(configured)
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        return Path(local_app_data) / "Siming"
    return Path.home() / "Siming"


def launcher_settings_path() -> Path:
    return app_home() / "launcher-settings.json"


def load_launcher_settings() -> dict:
    path = launcher_settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def save_launcher_settings(settings: dict) -> None:
    path = launcher_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def launcher_settings_payload() -> dict:
    settings = load_launcher_settings()
    launch_mode = (
        "browser"
        if str(settings.get("launch_mode") or "").strip().lower() == "browser"
        else "desktop"
    )
    return {
        "launch_mode": launch_mode,
        "update_channel": resolve_update_channel(settings.get("update_channel")),
        "restart_required": True,
        "browser_mode_description": (
            "Use the default browser on the next launch instead of the embedded "
            "WebView2 window."
        ),
    }


__all__ = [
    "app_home",
    "launcher_settings_payload",
    "launcher_settings_path",
    "load_launcher_settings",
    "save_launcher_settings",
]
