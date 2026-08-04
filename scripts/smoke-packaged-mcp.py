"""Verify that the packaged GUI executable also works as an stdio MCP server."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REQUIRED_TOOLS = {
    "create_project",
    "start_novel_creation_session",
    "submit_novel_creation_stage",
    "finalize_creation_session",
    "create_chapter",
    "archive_chapter_after_write",
}
PATCH_MARKER = "MCP_STANDARD_JSON_PATCH_SMOKE"


def _run_mcp(executable: Path, env: dict[str, str], requests: list[dict]) -> list[dict]:
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
        env = os.environ.copy()
        env["SIMING_HOME"] = temp_dir
        env["SIMING_CONTENT_ROOT"] = str(Path(temp_dir) / "projects")
        responses = _run_mcp(executable, env, requests)
        creation_payload = _tool_payload(next((item for item in responses if item.get("id") == 4), None))
        session_id = str(creation_payload.get("data", {}).get("session_id") or "")
        if not session_id:
            raise SystemExit(f"packaged MCP did not create a novel session: {creation_payload}")
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
                    "name": "get_creation_session",
                    "arguments": {"session_id": session_id},
                },
            },
        ])
        patch_payload = _tool_payload(next((item for item in patch_responses if item.get("id") == 5), None))
        verify_payload = _tool_payload(next((item for item in patch_responses if item.get("id") == 6), None))
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
    print(f"Packaged MCP smoke test passed: {len(names)} tools; write and JSON Patch paths OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
