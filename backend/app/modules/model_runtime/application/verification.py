"""Application contract for model discovery and real-connection probes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ModelProbeRequest:
    provider: str
    model: str | None = None
    api_key: str = ""
    base_url: str = ""
    api_protocol: str = "auto"
    cli_command: str | None = None
    cli_args: str | None = None
    timeout_seconds: int | None = None
    content_root: Path | None = None


class ModelVerificationPort(Protocol):
    async def list_models(self, request: ModelProbeRequest) -> list[dict]: ...

    async def verify(self, request: ModelProbeRequest) -> dict: ...


_verification: ModelVerificationPort | None = None


def configure_model_verification(verification: ModelVerificationPort) -> None:
    global _verification
    _verification = verification


def get_model_verification() -> ModelVerificationPort:
    if _verification is None:
        raise RuntimeError("Model verification has not been configured")
    return _verification


__all__ = [
    "ModelProbeRequest",
    "ModelVerificationPort",
    "configure_model_verification",
    "get_model_verification",
]
