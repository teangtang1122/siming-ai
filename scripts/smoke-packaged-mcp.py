"""Verify that the packaged GUI executable also works as an stdio MCP server."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


REQUIRED_TOOLS = {
    "create_project",
    "start_novel_creation_session",
    "patch_creation_artifact",
    "finalize_creation_session",
}
PATCH_MARKER = "MCP_STANDARD_JSON_PATCH_SMOKE"


def _source_head_revision() -> str:
    root = Path(__file__).resolve().parents[1]
    config = Config()
    config.set_main_option("script_location", str(root / "backend" / "alembic"))
    revision = ScriptDirectory.from_config(config).get_current_head()
    if not revision:
        raise SystemExit("source Alembic graph has no head revision")
    return revision


def _run_mcp(
    executable: Path,
    env: dict[str, str],
    requests: list[dict],
    *,
    cwd: Path,
) -> list[dict]:
    completed = subprocess.run(
        [str(executable), "--mcp-server", "--permission-pack", "project_management"],
        input="".join(json.dumps(item) + "\n" for item in requests),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        env=env,
        cwd=cwd,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"packaged MCP exited with {completed.returncode}: "
            f"{(completed.stderr or completed.stdout)[:1000]}"
        )
    return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]


def _tool_payload(response: dict | None) -> dict:
    result = (response or {}).get("result", {})
    if result.get("isError") or not result.get("content"):
        raise SystemExit(f"packaged MCP tool call failed: {result}")
    return json.loads(result["content"][0]["text"])


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: smoke-packaged-mcp.py <Siming.exe>")
    executable = Path(sys.argv[1]).resolve()
    expected_revision = _source_head_revision()
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "package-smoke", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "create_project",
                "arguments": {
                    "title": "MCP package smoke test",
                    "description": "Temporary project used to verify packaged MCP writes.",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "start_novel_creation_session",
                "arguments": {
                    "mode": "external_agent",
                    "user_brief": "MCP standard JSON Patch smoke test",
                    "genre": "test",
                },
            },
        },
    ]
    with tempfile.TemporaryDirectory(prefix="siming-packaged-mcp-smoke-") as temp_dir:
        temp_path = Path(temp_dir)
        foreign_agent_dir = temp_path / "foreign-agent"
        foreign_agent_dir.mkdir()
        # Reproduce the packaged Hermes/CWD incident.  The invalid value for a
        # real Siming field proves that the packaged process does not merely
        # ignore unknown keys: it must not load this foreign dotenv at all.
        (foreign_agent_dir / ".env").write_text(
            "\n".join(
                [
                    "SIMING_RUNTIME_PROFILE=foreign-agent-value",
                    "TERMINAL_MODAL_IMAGE=nikolaik/python-nodejs:python3.11-nodejs20",
                    "TERMINAL_TIMEOUT=60",
                    "TERMINAL_LIFETIME_SECONDS=300",
                    "BROWSERBASE_PROXIES=true",
                    "BROWSERBASE_ADVANCED_STEALTH=false",
                    "BROWSER_SESSION_TIMEOUT=300",
                    "BROWSER_INACTIVITY_TIMEOUT=120",
                    "WEB_TOOLS_DEBUG=false",
                    "VISION_TOOLS_DEBUG=false",
                    "MOA_TOOLS_DEBUG=false",
                    "IMAGE_TOOLS_DEBUG=false",
                ]
            ),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["SIMING_HOME"] = temp_dir
        env["SIMING_CONTENT_ROOT"] = str(temp_path / "projects")
        responses = _run_mcp(executable, env, requests, cwd=foreign_agent_dir)
        creation_payload = _tool_payload(next((item for item in responses if item.get("id") == 4), None))
        session_id = str(creation_payload.get("data", {}).get("session_id") or "")
        if not session_id:
            raise SystemExit(f"packaged MCP did not create a novel session: {creation_payload}")

        # Reproduce databases touched by the short-lived 300a19 build.  The
        # migration was data-only and has been retired, so the packaged app
        # must back up the database and normalize only this exact marker.
        database_path = temp_path / "novel_agent.db"
        with closing(sqlite3.connect(database_path)) as database:
            current = database.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
            if current != (expected_revision,):
                raise SystemExit(f"packaged MCP initialized unexpected revision: {current}")
            database.execute(
                "UPDATE alembic_version SET version_num = ?",
                ("300a19_runtime_readiness",),
            )
            database.execute(
                "UPDATE siming_schema_metadata SET value = ? "
                "WHERE key = 'alembic_revision'",
                ("300a19_runtime_readiness",),
            )
            database.commit()

        patch_responses = _run_mcp(executable, env, [
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "patch_creation_artifact",
                    "arguments": {
                        "session_id": session_id,
                        "artifact": "constraints",
                        "expected_revision": 0,
                        "changes": [{
                            "op": "add",
                            "path": "/special_requirements/-",
                            "value": PATCH_MARKER,
                        }],
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "get_creation_artifact",
                    "arguments": {
                        "session_id": session_id,
                        "artifact": "constraints",
                    },
                },
            },
        ], cwd=foreign_agent_dir)
        patch_payload = _tool_payload(next((item for item in patch_responses if item.get("id") == 5), None))
        verify_payload = _tool_payload(next((item for item in patch_responses if item.get("id") == 6), None))
        with closing(sqlite3.connect(database_path)) as database:
            normalized_revision = database.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
            metadata_revision = database.execute(
                "SELECT value FROM siming_schema_metadata "
                "WHERE key = 'alembic_revision'"
            ).fetchone()
        if normalized_revision != (expected_revision,):
            raise SystemExit(
                f"packaged MCP did not normalize retired revision: {normalized_revision}"
            )
        if metadata_revision != (expected_revision,):
            raise SystemExit(
                f"packaged MCP left stale schema metadata: {metadata_revision}"
            )
        retired_backups = list(
            (temp_path / "backups").glob(
                "novel_agent.pre-*-retired-revision.*.db"
            )
        )
        if len(retired_backups) != 1:
            raise SystemExit(
                "packaged MCP did not create exactly one retired-revision backup: "
                f"{retired_backups}"
            )
    initialize = next((item for item in responses if item.get("id") == 1), None)
    catalog = next((item for item in responses if item.get("id") == 2), None)
    write_result = next((item for item in responses if item.get("id") == 3), None)
    if (initialize or {}).get("result", {}).get("serverInfo", {}).get("name") != "siming":
        raise SystemExit("packaged MCP did not return a valid initialize response")
    tools = (catalog or {}).get("result", {}).get("tools", [])
    names = {str(item.get("name") or "") for item in tools}
    missing = sorted(REQUIRED_TOOLS - names)
    if missing:
        raise SystemExit(f"packaged MCP is missing required tools: {', '.join(missing)}")
    payload = _tool_payload(write_result)
    if payload.get("status") != "ok" or not payload.get("data", {}).get("id"):
        raise SystemExit(f"packaged MCP write returned an invalid result: {payload}")
    if patch_payload.get("status") != "ok":
        raise SystemExit(f"packaged MCP standard JSON Patch failed: {patch_payload}")
    if PATCH_MARKER not in json.dumps(verify_payload, ensure_ascii=False):
        raise SystemExit("packaged MCP standard JSON Patch was not persisted")
    print(
        f"Packaged MCP smoke test passed: {len(names)} tools; foreign dotenv isolation, "
        "write, JSON Patch, and retired revision normalization paths OK"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
