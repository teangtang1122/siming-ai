"""Persistent launcher preferences shared by the API and packaged runtime."""
from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ..core.legacy_env import get_compatible_env
from ..updater import resolve_update_channel

DESKTOP_PET_MIN_SCALE = 0.7
DESKTOP_PET_MAX_SCALE = 1.35
DESKTOP_PET_MIN_OPACITY = 0.55
DESKTOP_PET_MAX_OPACITY = 1.0
_SETTINGS_LOCK = threading.RLock()


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
    with _SETTINGS_LOCK:
        path = launcher_settings_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}


def save_launcher_settings(settings: dict) -> None:
    with _SETTINGS_LOCK:
        path = launcher_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def update_launcher_preferences(values: Mapping[str, object]) -> dict:
    """Merge launcher preferences through one process-wide write boundary."""

    with _SETTINGS_LOCK:
        settings = load_launcher_settings()
        settings.update(values)
        save_launcher_settings(settings)
        return settings


def _bounded_number(value: object, *, default: float, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return round(min(maximum, max(minimum, number)), 2)


def _boolean_setting(settings: Mapping[str, object], key: str, default: bool) -> bool:
    value = settings.get(key)
    return value if isinstance(value, bool) else default


def normalize_desktop_pet_settings(settings: Mapping[str, object] | None = None) -> dict:
    """Return the canonical fixed-character desktop-pet preferences."""

    source = settings or {}
    return {
        "desktop_pet_enabled": _boolean_setting(source, "desktop_pet_enabled", True),
        "desktop_pet_scale": _bounded_number(
            source.get("desktop_pet_scale"),
            default=0.8,
            minimum=DESKTOP_PET_MIN_SCALE,
            maximum=DESKTOP_PET_MAX_SCALE,
        ),
        "desktop_pet_opacity": _bounded_number(
            source.get("desktop_pet_opacity"),
            default=0.96,
            minimum=DESKTOP_PET_MIN_OPACITY,
            maximum=DESKTOP_PET_MAX_OPACITY,
        ),
        "desktop_pet_muted": _boolean_setting(source, "desktop_pet_muted", True),
        "desktop_pet_on_top": _boolean_setting(source, "desktop_pet_on_top", True),
    }


_HOST_PATTERN = re.compile(
    r"^(?:\*\.)?(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|"
    r"\[[0-9A-Fa-f:]+\]|[0-9A-Fa-f:.]+)$"
)


def normalize_gateway_advertised_url(value: str | None) -> str:
    """Validate the optional public base URL without retaining credentials."""

    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Gateway 公布地址必须是无账号、无路径的 HTTP 或 HTTPS 地址")
    hostname = parsed.hostname
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname if parsed.port is None else f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def normalize_gateway_allowed_hosts(value: str | None) -> str:
    """Normalize an explicit TrustedHost allowlist for the next launch."""

    raw_hosts = [item.strip().lower() for item in str(value or "").split(",")]
    hosts: list[str] = []
    for host in raw_hosts:
        if not host:
            continue
        if len(host) > 255 or not _HOST_PATTERN.fullmatch(host):
            raise ValueError(f"Gateway 允许主机格式无效：{host}")
        if host not in hosts:
            hosts.append(host)
    return ",".join(hosts)


def launcher_settings_payload() -> dict:
    settings = load_launcher_settings()
    runtime_profile = os.environ.get("SIMING_RUNTIME_PROFILE", "desktop-standalone")
    gateway_headless = runtime_profile == "gateway" and os.environ.get(
        "SIMING_GATEWAY_HEADLESS", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    launch_mode = (
        "browser"
        if str(settings.get("launch_mode") or "").strip().lower() == "browser"
        else "desktop"
    )
    desktop_pet_supported = os.name == "nt" and not gateway_headless
    return {
        "launch_mode": launch_mode,
        "update_channel": resolve_update_channel(settings.get("update_channel")),
        "gateway_enabled": True if gateway_headless else bool(settings.get("gateway_enabled")),
        "gateway_runtime_active": (
            runtime_profile == "gateway"
        ),
        "gateway_headless": gateway_headless,
        "gateway_advertised_url": normalize_gateway_advertised_url(
            os.environ.get("SIMING_GATEWAY_ADVERTISED_URL", "")
            if gateway_headless
            else settings.get("gateway_advertised_url")
        ),
        "gateway_allowed_hosts": normalize_gateway_allowed_hosts(
            os.environ.get(
                "SIMING_GATEWAY_ALLOWED_HOSTS",
                "localhost,127.0.0.1,*.local,*.ts.net",
            )
            if gateway_headless
            else settings.get("gateway_allowed_hosts")
        ),
        "restart_required": True,
        "browser_mode_description": (
            "Use the default browser on the next launch instead of the embedded "
            "WebView2 window."
        ),
        **normalize_desktop_pet_settings(settings),
        "desktop_pet_supported": desktop_pet_supported,
        "desktop_pet_runtime_active": (
            desktop_pet_supported
            and os.environ.get("SIMING_DESKTOP_WEBVIEW") == "1"
        ),
    }


__all__ = [
    "app_home",
    "DESKTOP_PET_MAX_OPACITY",
    "DESKTOP_PET_MAX_SCALE",
    "DESKTOP_PET_MIN_OPACITY",
    "DESKTOP_PET_MIN_SCALE",
    "launcher_settings_payload",
    "launcher_settings_path",
    "load_launcher_settings",
    "normalize_gateway_advertised_url",
    "normalize_gateway_allowed_hosts",
    "normalize_desktop_pet_settings",
    "save_launcher_settings",
    "update_launcher_preferences",
]
