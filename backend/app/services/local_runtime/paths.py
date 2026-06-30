"""Filesystem layout for downloaded local AI assets."""
from __future__ import annotations

import os
from pathlib import Path


def moshu_home() -> Path:
    configured = os.environ.get("SIMING_HOME") or os.environ.get("MOSHU_HOME") or os.environ.get("NOVEL_AGENT_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return (Path(local_app_data) / "Siming").resolve()
    return (Path.home() / "Siming").resolve()


def model_root() -> Path:
    configured = os.environ.get("SIMING_MODEL_ROOT") or os.environ.get("MOSHU_MODEL_ROOT")
    path = Path(configured).expanduser() if configured else moshu_home() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def runtime_root() -> Path:
    path = moshu_home() / "runtimes"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def training_root() -> Path:
    path = moshu_home() / "training"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def downloads_root() -> Path:
    path = moshu_home() / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()
