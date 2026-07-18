"""Filesystem layout for downloaded local AI assets."""
from __future__ import annotations

import os
from pathlib import Path

from ...core.legacy_env import get_compatible_env


def siming_home() -> Path:
    configured = get_compatible_env("SIMING_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return (Path(local_app_data) / "Siming").resolve()
    return (Path.home() / "Siming").resolve()


def model_root() -> Path:
    configured = get_compatible_env("SIMING_MODEL_ROOT")
    path = Path(configured).expanduser() if configured else siming_home() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def runtime_root() -> Path:
    path = siming_home() / "runtimes"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def training_root() -> Path:
    path = siming_home() / "training"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def downloads_root() -> Path:
    path = siming_home() / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


# Compatibility export for older internal extensions.
moshu_home = siming_home
