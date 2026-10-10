"""Application boundary for first-run model readiness."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class GettingStartedModelState:
    has_any_model: bool
    global_provider: str | None
    global_model: str | None
    usable_provider: str | None
    usable_model: str | None


class GettingStartedConfigurationPort(Protocol):
    def state(self, session: Any) -> GettingStartedModelState: ...


_configuration: GettingStartedConfigurationPort | None = None


def configure_getting_started_configuration(configuration: GettingStartedConfigurationPort) -> None:
    global _configuration
    _configuration = configuration


def get_getting_started_configuration() -> GettingStartedConfigurationPort:
    if _configuration is None:
        raise RuntimeError("Getting-started configuration has not been configured")
    return _configuration
