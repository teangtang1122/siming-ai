"""One persisted launch profile per model for manual and automatic starts."""
from __future__ import annotations

from ...schemas.local_model import RuntimeLaunchSettings
from ..application_settings import load_launcher_settings, save_launcher_settings

_PROFILES_KEY = "local_runtime_launch_profiles"


def default_launch_settings(
    model_key: str, *, context_length: int, gpu_layers: int, threads: int
) -> RuntimeLaunchSettings:
    common = {
        "context_length": context_length,
        "gpu_layers": gpu_layers,
        "threads": threads,
    }
    if model_key == "qwen3.8-27b-q3":
        return RuntimeLaunchSettings(**common, flash_attention="on", fit="off",
            kv_cache_type="q4_0", mtp_draft_tokens=2, cache_ram_mb=2048,
            reasoning_effort="medium", temperature=1.0, top_p=0.95,
            top_k=20, min_p=0, repeat_penalty=1.0)
    return RuntimeLaunchSettings(**common)


def load_launch_settings(
    model_key: str, *, context_length: int, gpu_layers: int, threads: int
) -> tuple[RuntimeLaunchSettings, bool]:
    profiles = load_launcher_settings().get(_PROFILES_KEY) or {}
    saved = profiles.get(model_key) if isinstance(profiles, dict) else None
    if saved is None:
        return default_launch_settings(
            model_key, context_length=context_length, gpu_layers=gpu_layers, threads=threads
        ), False
    return RuntimeLaunchSettings.model_validate(saved), True


def save_launch_settings(model_key: str, profile: RuntimeLaunchSettings) -> None:
    settings = load_launcher_settings()
    profiles = settings.get(_PROFILES_KEY)
    if not isinstance(profiles, dict):
        profiles = {}
    profiles[model_key] = profile.model_dump()
    settings[_PROFILES_KEY] = profiles
    save_launcher_settings(settings)


def reset_launch_settings(model_key: str) -> None:
    settings = load_launcher_settings()
    profiles = settings.get(_PROFILES_KEY)
    if isinstance(profiles, dict) and model_key in profiles:
        profiles.pop(model_key)
        settings[_PROFILES_KEY] = profiles
        save_launcher_settings(settings)
