"""Runtime MCP connectivity checks for managed local CLI workflows."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.ai.local_cli_adapter import hidden_subprocess_kwargs
from app.services.application_settings import app_home
from app.services.external_agent.mcp_server_spec import resolve_siming_mcp_server

MCP_SERVER_NAME = "siming"
CATALOGING_MCP_TOOL_NAMES = (
    "report_agent_plan",
    "report_agent_progress",
    "report_context_selected",
    "get_next_external_cataloging_chapter",
    "save_external_cataloging_facts",
    "save_external_cataloging_candidates",
    "verify_external_cataloging_progress",
    "get_cataloging_control_state",
    "list_cataloging_facts",
    "apply_pending_cataloging",
)


def _cli_argv(command: str, args: list[str]) -> list[str]:
    if os.name == "nt" and Path(command).suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/d", "/s", "/c", command, *args]
    return [command, *args]


def _resolve_opencode_command(command: str | None) -> str | None:
    candidates = [command, "opencode.cmd", "opencode", "opencode.exe"]
    for candidate in candidates:
        value = str(candidate or "").strip().strip('"')
        if not value:
            continue
        path = Path(value).expanduser()
        if path.is_file():
            return str(path.resolve())
        resolved = shutil.which(value)
        if resolved:
            return str(Path(resolved).resolve())
    return None


def _probe_siming_mcp_tools(
    *,
    permission_pack: str,
    timeout: int = 20,
) -> tuple[set[str], str]:
    """Start Siming MCP directly and verify the exact permission-pack surface."""

    server = resolve_siming_mcp_server(permission_pack=permission_pack)
    requests = "\n".join([
        json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "siming-preflight", "version": "1"},
            },
        }),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        "",
    ])
    env = os.environ.copy()
    if permission_pack == "cataloging_worker":
        env["SIMING_MANAGED_AGENT_KIND"] = "cataloging"
    try:
        completed = subprocess.run(
            _cli_argv(str(server["command"]), [str(item) for item in server.get("args") or []]),
            input=requests,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=server.get("cwd") or str(app_home()),
            env=env,
            **hidden_subprocess_kwargs(),
        )
    except Exception as exc:
        return set(), f"Siming MCP 启动检查失败：{exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "MCP process exited").strip()[-800:]
        return set(), f"Siming MCP 启动失败：{detail}"
    for raw_line in completed.stdout.splitlines():
        try:
            payload = json.loads(raw_line)
        except (TypeError, json.JSONDecodeError):
            continue
        if payload.get("id") != 2:
            continue
        tools = ((payload.get("result") or {}).get("tools") or [])
        names = {
            str(item.get("name") or "").strip()
            for item in tools
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        return names, ""
    detail = (completed.stderr or completed.stdout or "tools/list did not return a result").strip()[-800:]
    return set(), f"Siming MCP 未返回工具列表：{detail}"


def preflight_cli_integration(
    provider: str,
    *,
    cli_command: str | None = None,
    permission_pack: str = "cataloging_worker",
    required_tools: tuple[str, ...] | list[str] | set[str] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    """Verify OpenCode's connection and Siming's managed tool surface."""

    provider = str(provider or "").strip()
    required = set(required_tools or CATALOGING_MCP_TOOL_NAMES)
    if provider != "opencode_cli":
        return {
            "provider": provider,
            "ready": False,
            "configured": False,
            "connected": False,
            "tool_surface_ready": False,
            "missing_tools": sorted(required),
            "detail": "当前仅对 OpenCode 建档执行自动 MCP 启动检查",
        }
    command = _resolve_opencode_command(cli_command)
    if not command:
        return {
            "provider": provider,
            "ready": False,
            "configured": False,
            "connected": False,
            "tool_surface_ready": False,
            "missing_tools": sorted(required),
            "detail": "没有找到可运行的 OpenCode，无法检查 Siming MCP",
        }

    env = os.environ.copy()
    if permission_pack == "cataloging_worker":
        env["SIMING_MANAGED_AGENT_KIND"] = "cataloging"
    try:
        completed = subprocess.run(
            _cli_argv(command, ["mcp", "list"]),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(app_home()),
            env=env,
            **hidden_subprocess_kwargs(),
        )
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    except Exception as exc:
        return {
            "provider": provider,
            "ready": False,
            "configured": False,
            "connected": False,
            "tool_surface_ready": False,
            "missing_tools": sorted(required),
            "detail": f"OpenCode MCP 连接检查失败：{exc}",
        }

    siming_lines = [line.strip() for line in output.splitlines() if MCP_SERVER_NAME in line.lower()]
    configured = bool(siming_lines)
    negative = ("failed", "error", "disconnected", "disabled", "offline", "unavailable")
    connection_failed = any(any(marker in line.lower() for marker in negative) for line in siming_lines)
    connected = completed.returncode == 0 and configured and not connection_failed
    tool_names, tool_error = _probe_siming_mcp_tools(permission_pack=permission_pack, timeout=timeout)
    missing = sorted(required - tool_names)
    tool_surface_ready = not tool_error and not missing
    ready = connected and tool_surface_ready

    if not configured:
        detail = "OpenCode 尚未配置 Siming MCP；请先在快速开始或系统设置中授权配置"
    elif not connected:
        detail = "OpenCode 已配置 Siming MCP，但连接状态异常；请重新配置后再试"
    elif tool_error:
        detail = tool_error
    elif missing:
        detail = "Siming MCP 已连接，但缺少建档工具：" + ", ".join(missing)
    else:
        detail = "OpenCode 与 Siming MCP 已连接，建档写入工具可用"

    return {
        "provider": provider,
        "ready": ready,
        "configured": configured,
        "connected": connected,
        "tool_surface_ready": tool_surface_ready,
        "missing_tools": missing,
        "available_tools": sorted(tool_names & required),
        "detail": detail,
        "mcp_list_output": output[-1000:],
    }
