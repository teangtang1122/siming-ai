"""Application contract for project MCP server configuration."""

from __future__ import annotations

from typing import Any, Protocol


class McpServerConfigurationPort(Protocol):
    def list(self, session: Any, project_id: str) -> list[dict]: ...

    def create(self, session: Any, project_id: str, values: dict) -> dict: ...

    def get(self, session: Any, project_id: str, config_id: str) -> dict | None: ...

    def update(
        self,
        session: Any,
        project_id: str,
        config_id: str,
        values: dict,
    ) -> dict | None: ...

    def delete(self, session: Any, project_id: str, config_id: str) -> bool: ...


_configuration: McpServerConfigurationPort | None = None


def configure_mcp_server_configuration(configuration: McpServerConfigurationPort) -> None:
    global _configuration
    _configuration = configuration


def get_mcp_server_configuration() -> McpServerConfigurationPort:
    if _configuration is None:
        raise RuntimeError("MCP server configuration has not been configured")
    return _configuration


__all__ = [
    "McpServerConfigurationPort",
    "configure_mcp_server_configuration",
    "get_mcp_server_configuration",
]
