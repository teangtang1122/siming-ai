"""Application boundary for first-run model configuration state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class GettingStartedModelState:
    opencode_command: str | None
    configured_model: str | None
    configured: bool
    opencode_is_global: bool
    has_any_model: bool
    has_detected_models: bool
    has_usable_models: bool
    global_provider: str | None
    global_model: str | None


@dataclass(frozen=True)
class ConfiguredOpenCode:
    provider: str
    model: str
    command: str
    cli_args: str


class GettingStartedConfigurationPort(Protocol):
    def state(self, session: Any) -> GettingStartedModelState: ...

    def configure_opencode(
        self,
        session: Any,
        *,
        command: str,
        model: str,
        cli_args: str,
    ) -> ConfiguredOpenCode: ...


_configuration: GettingStartedConfigurationPort | None = None


def configure_getting_started_configuration(
    configuration: GettingStartedConfigurationPort,
) -> None:
    global _configuration
    _configuration = configuration


def get_getting_started_configuration() -> GettingStartedConfigurationPort:
    if _configuration is None:
        raise RuntimeError("Getting-started configuration has not been configured")
    return _configuration


__all__ = [
    "ConfiguredOpenCode",
    "GettingStartedConfigurationPort",
    "GettingStartedModelState",
    "configure_getting_started_configuration",
    "get_getting_started_configuration",
]
