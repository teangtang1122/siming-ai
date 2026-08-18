from unittest.mock import MagicMock, patch

from app.services.external_agent import mcp_preflight


def test_opencode_preflight_requires_configured_connected_siming():
    connected = MagicMock(returncode=0, stdout="siming connected\n", stderr="")
    with patch.object(mcp_preflight, "_resolve_opencode_command", return_value="opencode"), patch.object(
        mcp_preflight.subprocess,
        "run",
        return_value=connected,
    ), patch.object(
        mcp_preflight,
        "_probe_siming_mcp_tools",
        return_value=(set(mcp_preflight.CATALOGING_MCP_TOOL_NAMES), ""),
    ):
        result = mcp_preflight.preflight_cli_integration(
            "opencode_cli",
            cli_command="opencode",
        )

    assert result["ready"] is True
    assert result["connected"] is True
    assert result["missing_tools"] == []


def test_opencode_preflight_reports_missing_mcp_configuration():
    listed = MagicMock(returncode=0, stdout="No MCP servers configured\n", stderr="")
    with patch.object(mcp_preflight, "_resolve_opencode_command", return_value="opencode"), patch.object(
        mcp_preflight.subprocess,
        "run",
        return_value=listed,
    ), patch.object(
        mcp_preflight,
        "_probe_siming_mcp_tools",
        return_value=(set(mcp_preflight.CATALOGING_MCP_TOOL_NAMES), ""),
    ):
        result = mcp_preflight.preflight_cli_integration("opencode_cli")

    assert result["ready"] is False
    assert result["configured"] is False
    assert "尚未配置" in result["detail"]
