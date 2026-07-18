"""Feature policy for the optional bundled local runtime."""

from __future__ import annotations

import os

LOCAL_RUNTIME_PROVIDER = "local_llama_cpp"
ENABLE_LOCAL_RUNTIME_ENV = "SIMING_ENABLE_LOCAL_RUNTIME"


def local_runtime_enabled() -> bool:
    value = os.environ.get(ENABLE_LOCAL_RUNTIME_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def is_local_runtime_provider(provider: str | None) -> bool:
    return provider == LOCAL_RUNTIME_PROVIDER


def local_runtime_disabled(provider: str | None = LOCAL_RUNTIME_PROVIDER) -> bool:
    return is_local_runtime_provider(provider) and not local_runtime_enabled()


def local_runtime_disabled_message() -> str:
    return (
        "本地 AI 模型暂时已停用。请使用 API 或本机 CLI 模型；"
        f"如需临时恢复本地模型，可设置环境变量 {ENABLE_LOCAL_RUNTIME_ENV}=1。"
    )
