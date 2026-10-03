"""Feature policy for Siming's bundled local runtime."""

from __future__ import annotations

LOCAL_RUNTIME_PROVIDER = "local_llama_cpp"


def local_runtime_enabled() -> bool:
    """Read the desktop user's explicit model-center switch on every request."""
    from ....services.application_settings import load_launcher_settings

    return load_launcher_settings().get("local_runtime_enabled") is not False


def is_local_runtime_provider(provider: str | None) -> bool:
    return provider == LOCAL_RUNTIME_PROVIDER


def local_runtime_disabled(provider: str | None = LOCAL_RUNTIME_PROVIDER) -> bool:
    return provider == LOCAL_RUNTIME_PROVIDER and not local_runtime_enabled()


def local_runtime_disabled_message() -> str:
    return "本地模型已在模型中心关闭；开启后才能使用。"
