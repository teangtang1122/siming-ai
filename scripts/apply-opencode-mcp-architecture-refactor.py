from __future__ import annotations

from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8", newline="\n")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if text.count(old) != 1:
        raise RuntimeError(f"expected one match in {path}: {old[:120]!r}")
    write(path, text.replace(old, new))


def remove_between(path: str, start: str, end: str) -> None:
    text = read(path)
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"start marker not found in {path}: {start!r}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"end marker not found in {path}: {end!r}")
    write(path, text[:start_index] + text[end_index:])


# Keep MCP connectivity/probing out of the already-grandfathered config module.
preflight_path = Path("backend/app/services/external_agent/mcp_preflight.py")
preflight_path.write_text(r'''"""Runtime MCP connectivity checks for managed local CLI workflows."""
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
''', encoding="utf-8", newline="\n")

mcp_config = "backend/app/services/external_agent/mcp_auto_config.py"
remove_between(
    mcp_config,
    'CATALOGING_MCP_TOOL_NAMES = (\n',
    '# OpenCode 1.x applies this per-server timeout to MCP catalog and tool calls.\n',
)
remove_between(
    mcp_config,
    '\ndef _cli_argv(command: str, args: list[str]) -> list[str]:\n',
    '\ndef configure_cli_integration(\n',
)
replace_once(
    mcp_config,
    '''def _configure_opencode(\n    server: dict[str, Any],\n    *,\n    cli_command: str | None = None,\n) -> dict[str, Any]:\n''',
    'def _configure_opencode(server: dict[str, Any], *, cli_command: str | None = None) -> dict[str, Any]:\n',
)

# Router keeps mutation/configuration in mcp_auto_config and probing in its own module.
router = "backend/app/routers/getting_started.py"
replace_once(
    router,
    '''from ..services.external_agent.mcp_auto_config import (\n    configure_cli_integration,\n    preflight_cli_integration,\n    scan_cli_integrations,\n)\n''',
    '''from ..services.external_agent.mcp_auto_config import (\n    configure_cli_integration,\n    scan_cli_integrations,\n)\nfrom ..services.external_agent.mcp_preflight import preflight_cli_integration\n''',
)

# Preflight tests move with the preflight module.
test_config = "backend/tests/test_mcp_auto_config.py"
remove_between(
    test_config,
    '\ndef test_opencode_preflight_requires_configured_connected_siming():\n',
    '\ndef test_opencode_configuration_accepts_managed_command_outside_path():\n',
)
preflight_test = Path("backend/tests/test_mcp_preflight.py")
preflight_test.write_text(r'''from unittest.mock import MagicMock, patch

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
''', encoding="utf-8", newline="\n")

# Keep OpenCode permission construction in a small cataloging-specific module.
cli_mcp = Path("backend/app/services/cataloging/local_cli_mcp.py")
cli_mcp.write_text(r'''"""Narrow MCP and filesystem permissions for managed cataloging CLI turns."""
from __future__ import annotations

import json
from typing import Any

from app.services.external_agent.mcp_preflight import (
    CATALOGING_MCP_TOOL_NAMES,
    preflight_cli_integration,
)


def opencode_cataloging_permission_env() -> str:
    permission: dict[str, Any] = {
        "*": "deny",
        "read": {
            "*": "allow",
            "*.env": "deny",
            "*.env.*": "deny",
            "*.env.example": "allow",
        },
        "glob": "allow",
        "grep": "allow",
        "edit": "deny",
        "bash": "deny",
        "question": "deny",
        "task": "deny",
        "skill": "deny",
        "lsp": "deny",
        "webfetch": "deny",
        "websearch": "deny",
        "external_directory": "deny",
        "doom_loop": "allow",
    }
    for tool_name in CATALOGING_MCP_TOOL_NAMES:
        permission[f"siming_{tool_name}"] = "allow"
    return json.dumps(permission, ensure_ascii=False, separators=(",", ":"))


def preflight_opencode_cataloging(cli_command: str | None) -> dict[str, Any]:
    return preflight_cli_integration(
        "opencode_cli",
        cli_command=cli_command,
        permission_pack="cataloging_worker",
    )
''', encoding="utf-8", newline="\n")

# Move completed-turn/error handling out of the legacy oversized coordinator.
result_module = Path("backend/app/services/cataloging/local_cli_result.py")
result_module.write_text(r'''"""Result handling for one managed local-CLI cataloging turn."""
from __future__ import annotations

import json
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.ai.local_cli_adapter import CLIStalledError
from app.architecture.uow import commit_session
from app.database.models import AgentRun, AgentRunEvent, CatalogingChapterRun, CatalogingJob
from app.database.session import SessionLocal
from app.services.cataloging import orchestrator as cataloging_orchestrator
from app.services.cataloging.job_control import refresh_job_progress
from app.services.external_agent.run_service import add_event, update_run_status
from app.services.operation_runtime import record_operation_signal

_MAX_NO_SAVE_ATTEMPTS = 3
_TERMINAL_RUNS = {"completed", "completed_with_warnings", "skipped_by_user"}
TurnAction = Literal["next", "continue", "return"]


def agent_tool_event_count(agent_run_id: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(AgentRunEvent.id)
            .filter(
                AgentRunEvent.run_id == agent_run_id,
                AgentRunEvent.event_type == "tool_start",
            )
            .count()
        )
    finally:
        db.close()


def _turn_has_no_saved_progress(stage: str, status: str) -> bool:
    if stage in {"full", "merged"}:
        return status in {"pending", "in_progress", "extracting"}
    if stage == "candidates":
        return status == "facts_saved"
    if stage == "apply":
        return status == "awaiting_confirmation"
    return False


async def _consume_cataloging_events(generator: Any) -> None:
    async for _event in generator:
        pass


async def _run_direct_jsonl_cataloging_fallback(
    db: Session,
    *,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    agent_run_id: str,
    stage: str,
    stdout_tail: str = "",
    stderr_tail: str = "",
    failure_reason: str = "",
) -> tuple[bool, str]:
    add_event(
        db,
        agent_run_id,
        "chapter_agent_fallback",
        status="running",
        message=failure_reason or "本机 CLI 未通过 MCP 保存，改用同一模型的直连 JSONL 建档兜底",
        payload_json=json.dumps({
            "job_id": job.id,
            "chapter_id": run.chapter_id,
            "chapter_run_id": run.id,
            "stage": stage,
            "stdout_tail": stdout_tail[-1500:],
            "stderr_tail": stderr_tail[-1500:],
        }, ensure_ascii=False),
    )
    commit_session(db)
    try:
        if stage in {"full", "merged", "candidates"}:
            await _consume_cataloging_events(cataloging_orchestrator._extract_run(db, job, run))
            db.refresh(job)
            db.refresh(run)
            if run.status == "failed":
                return False, run.error or "直连 JSONL 建档未生成可用候选"
            if job.execution_mode == "auto":
                await _consume_cataloging_events(cataloging_orchestrator._apply_run(db, job, run))
        elif stage == "apply":
            await _consume_cataloging_events(cataloging_orchestrator._apply_run(db, job, run))
        else:
            return False, f"未知建档阶段：{stage}"
        db.refresh(job)
        db.refresh(run)
        if run.status == "failed":
            return False, run.error or "直连 JSONL 建档失败"
        add_event(
            db,
            agent_run_id,
            "chapter_agent_fallback_completed",
            status="ok",
            message="直连 JSONL 建档兜底已完成当前章节",
            payload_json=json.dumps({
                "job_id": job.id,
                "chapter_id": run.chapter_id,
                "chapter_run_id": run.id,
                "stage": stage,
                "chapter_status": run.status,
            }, ensure_ascii=False),
        )
        commit_session(db)
        return True, ""
    except Exception as exc:
        db.rollback()
        return False, str(exc)


def handle_cli_turn_exception(
    *,
    job_id: str,
    chapter_run_id: str,
    agent_run_id: str,
    stage: str,
    exc: Exception,
) -> None:
    db = SessionLocal()
    try:
        job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
        run = db.query(CatalogingChapterRun).filter(CatalogingChapterRun.id == chapter_run_id).first()
        if not job or not run:
            return
        run.status = "failed"
        run.error = str(exc)
        job.status = "paused_on_failure"
        job.blocked_chapter_id = run.chapter_id
        job.current_chapter_id = run.chapter_id
        job.error = run.error
        refresh_job_progress(db, job)
        add_event(
            db,
            agent_run_id,
            "chapter_agent_failed",
            status="error",
            message=run.error,
            payload_json=json.dumps({
                "job_id": job.id,
                "chapter_id": run.chapter_id,
                "chapter_run_id": run.id,
                "stage": stage,
            }, ensure_ascii=False),
        )
        commit_session(db)
        update_run_status(db, agent_run_id, "failed", summary=run.error)
        if job.operation_id:
            record_operation_signal(
                job.operation_id,
                "stalled" if isinstance(exc, CLIStalledError) else "error",
                {
                    "chapter_id": run.chapter_id,
                    "chapter_order": run.chapter_order,
                    "error": run.error,
                },
                message=run.error,
                db=db,
            )
    finally:
        db.close()


async def handle_cli_turn_result(
    *,
    job_id: str,
    chapter_run_id: str,
    agent_run_id: str,
    chapter_title: str,
    stage: str,
    returncode: int,
    stdout: str,
    stderr: str,
    tool_events_before: int,
    no_save_attempts: dict[str, int],
) -> TurnAction:
    db = SessionLocal()
    try:
        job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
        run = db.query(CatalogingChapterRun).filter(CatalogingChapterRun.id == chapter_run_id).first()
        if not job or not run:
            return "return"
        add_event(
            db,
            agent_run_id,
            "chapter_agent_finished",
            status="ok" if returncode == 0 else "error",
            message=f"本机 CLI 已结束：{chapter_title}",
            payload_json=json.dumps({
                "returncode": returncode,
                "chapter_status": run.status,
                "stdout_tail": stdout[-1500:],
                "stderr_tail": stderr[-1500:],
            }, ensure_ascii=False),
        )
        tool_activity = agent_tool_event_count(agent_run_id) > tool_events_before
        no_saved = returncode == 0 and _turn_has_no_saved_progress(stage, run.status)
        if no_saved:
            attempt = no_save_attempts.get(run.id, 0) + 1
            no_save_attempts[run.id] = attempt
            if attempt < _MAX_NO_SAVE_ATTEMPTS:
                if stage in {"full", "merged"}:
                    run.status = "pending"
                job.status = "running"
                job.blocked_chapter_id = None
                job.error = None
                prefix = (
                    "MCP 已连接，但模型本轮未调用任何 Siming 工具；"
                    if not tool_activity
                    else "模型调用了 Siming MCP，但未完成本章保存；"
                )
                add_event(
                    db,
                    agent_run_id,
                    "chapter_agent_retry",
                    status="running",
                    message=prefix + f"正在自动重试 {attempt + 1}/{_MAX_NO_SAVE_ATTEMPTS}",
                    payload_json=json.dumps({
                        "job_id": job.id,
                        "chapter_id": run.chapter_id,
                        "chapter_run_id": run.id,
                        "stage": stage,
                        "attempt": attempt + 1,
                        "max_attempts": _MAX_NO_SAVE_ATTEMPTS,
                        "stdout_tail": stdout[-1500:],
                        "stderr_tail": stderr[-1500:],
                    }, ensure_ascii=False),
                )
                commit_session(db)
                return "continue"
        if returncode != 0:
            run.status = "failed"
            run.error = stderr[-2000:] or stdout[-2000:] or f"CLI exit code {returncode}"
        elif _turn_has_no_saved_progress(stage, run.status):
            reason = (
                "MCP 已连接，但模型连续重试后仍未调用建档写入工具；改用同一模型的直连 JSONL 建档兜底"
                if not tool_activity
                else "模型调用了 Siming MCP，但连续重试后仍未完成本章保存；改用同一模型的直连 JSONL 建档兜底"
            )
            ok, fallback_error = await _run_direct_jsonl_cataloging_fallback(
                db,
                job=job,
                run=run,
                agent_run_id=agent_run_id,
                stage=stage,
                stdout_tail=stdout,
                stderr_tail=stderr,
                failure_reason=reason,
            )
            if ok:
                no_save_attempts.pop(run.id, None)
                commit_session(db)
                return "continue"
            run.status = "failed"
            run.error = f"本机 CLI 未通过 MCP 保存本章事实或候选；直连 JSONL 兜底也失败：{fallback_error}"
        if run.status == "failed":
            job.status = "paused_on_failure"
            job.blocked_chapter_id = run.chapter_id
            job.error = run.error
            refresh_job_progress(db, job)
            commit_session(db)
            update_run_status(db, agent_run_id, "failed", summary=run.error)
            if job.operation_id:
                record_operation_signal(
                    job.operation_id,
                    "error",
                    {"chapter_id": run.chapter_id, "error": run.error},
                    message=run.error,
                    db=db,
                )
            return "return"
        if run.status == "awaiting_confirmation" and job.execution_mode == "manual":
            job.status = "waiting_confirmation"
            job.blocked_chapter_id = run.chapter_id
            agent_run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).first()
            if agent_run:
                agent_run.status = "waiting_confirmation"
                agent_run.current_step = f"等待确认：第 {run.chapter_order + 1} 章"
            refresh_job_progress(db, job)
            commit_session(db)
            return "return"
        commit_session(db)
        if job.operation_id and run.status in _TERMINAL_RUNS:
            record_operation_signal(
                job.operation_id,
                "checkpoint",
                {
                    "chapter_id": run.chapter_id,
                    "chapter_order": run.chapter_order,
                    "chapter_status": run.status,
                },
                message=f"第 {run.chapter_order + 1} 章已保存检查点",
                db=db,
            )
        return "next"
    finally:
        db.close()
''', encoding="utf-8", newline="\n")

agent = "backend/app/services/cataloging/local_cli_agent.py"
replace_once(agent, '    CLIStalledError,\n', '')
replace_once(agent, 'from app.services.cataloging import orchestrator as cataloging_orchestrator\n', '')
replace_once(
    agent,
    '''from app.services.cataloging.job_control import refresh_job_progress\nfrom app.services.cataloging.orchestrator import job_to_dict, run_to_dict, sse_event\nfrom app.services.external_agent.mcp_auto_config import (\n    CATALOGING_MCP_TOOL_NAMES,\n    preflight_cli_integration,\n)\n''',
    '''from app.services.cataloging.job_control import refresh_job_progress\nfrom app.services.cataloging.local_cli_mcp import (\n    opencode_cataloging_permission_env,\n    preflight_opencode_cataloging,\n)\nfrom app.services.cataloging.local_cli_result import (\n    agent_tool_event_count,\n    handle_cli_turn_exception,\n    handle_cli_turn_result,\n)\nfrom app.services.cataloging.orchestrator import job_to_dict, run_to_dict, sse_event\n''',
)
replace_once(agent, '_MAX_NO_SAVE_ATTEMPTS = 3\n', '')
remove_between(agent, '\ndef _opencode_cataloging_permission_env() -> str:\n', '\ndef _latest_agent_event_at(agent_run_id: str) -> datetime | None:\n')
replace_once(
    agent,
    '''    if provider == "opencode_cli":\n        mcp_preflight = preflight_cli_integration(\n            provider,\n            cli_command=config.cli_command,\n            permission_pack="cataloging_worker",\n        )\n''',
    '''    if provider == "opencode_cli":\n        mcp_preflight = preflight_opencode_cataloging(config.cli_command)\n''',
)
remove_between(agent, '\ndef _turn_has_no_saved_progress(stage: str, status: str) -> bool:\n', '\nasync def _run_cli_turn(\n')
replace_once(agent, 'env["OPENCODE_PERMISSION"] = _opencode_cataloging_permission_env()\n', 'env["OPENCODE_PERMISSION"] = opencode_cataloging_permission_env()\n')
replace_once(agent, '            tool_events_before = _agent_tool_event_count(agent_run_id)\n', '            tool_events_before = agent_tool_event_count(agent_run_id)\n')

text = read(agent)
start = text.find('            except Exception as exc:\n                db = SessionLocal()\n', text.find('tool_events_before = agent_tool_event_count'))
if start < 0:
    raise RuntimeError("coordinator result block start not found")
end = text.find('    except asyncio.CancelledError:\n', start)
if end < 0:
    raise RuntimeError("coordinator result block end not found")
replacement = '''            except Exception as exc:\n                handle_cli_turn_exception(\n                    job_id=job_id,\n                    chapter_run_id=run_snapshot.id,\n                    agent_run_id=agent_run_id,\n                    stage=stage,\n                    exc=exc,\n                )\n                return\n\n            action = await handle_cli_turn_result(\n                job_id=job_id,\n                chapter_run_id=run_snapshot.id,\n                agent_run_id=agent_run_id,\n                chapter_title=chapter_snapshot.title,\n                stage=stage,\n                returncode=returncode,\n                stdout=stdout,\n                stderr=stderr,\n                tool_events_before=tool_events_before,\n                no_save_attempts=no_save_attempts,\n            )\n            if action == "return":\n                return\n            if action == "continue":\n                continue\n'''
write(agent, text[:start] + replacement + text[end:])

# Permission contract test now targets the small boundary module.
test_agent = "backend/tests/test_local_cli_cataloging_agent.py"
replace_once(
    test_agent,
    '    from app.services.cataloging.local_cli_agent import _opencode_cataloging_permission_env\n    from app.services.external_agent.mcp_auto_config import CATALOGING_MCP_TOOL_NAMES\n\n    permissions = json.loads(_opencode_cataloging_permission_env())\n',
    '    from app.services.cataloging.local_cli_mcp import opencode_cataloging_permission_env\n    from app.services.external_agent.mcp_preflight import CATALOGING_MCP_TOOL_NAMES\n\n    permissions = json.loads(opencode_cataloging_permission_env())\n',
)

print("OpenCode MCP architecture refactor applied")
