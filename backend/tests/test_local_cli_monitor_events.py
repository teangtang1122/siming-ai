"""Native CLI control diagnostics must be distinct from novel/tool payloads."""
import asyncio
import json
import sys

import pytest

from app.ai.local_cli_monitor import (
    communicate_with_cli_quota_detection,
    detect_cli_auth_error,
    detect_cli_permission_request,
    detect_cli_quota_error,
)


NARRATIVE = "沈砚需要授权才能查阅档案。The note says permission required; invalid token; HTTP 429 quota exhausted."


@pytest.mark.parametrize("event", [
    {"type": "tool_use", "part": {"type": "tool", "tool": "siming_turn_save_external_cataloging_facts",
      "state": {"status": "completed", "input": {"facts": [{"payload": {"summary": NARRATIVE}}]},
                "output": "Saved 19 facts"}}},
    {"type": "text", "part": {"text": NARRATIVE}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": NARRATIVE}]}},
    {"type": "result", "is_error": False, "result": NARRATIVE},
])
def test_prose_and_tool_payloads_are_not_cli_control_diagnostics(event):
    line = json.dumps(event, ensure_ascii=False)
    for detect in (detect_cli_permission_request, detect_cli_quota_error, detect_cli_auth_error):
        assert detect(line) == ""
        assert detect(line[:-5]) == ""  # an incomplete JSON chunk is not plaintext


@pytest.mark.parametrize("detect,message", [
    (detect_cli_permission_request, "Permission required for MCP access"),
    (detect_cli_quota_error, "HTTP 429 quota exhausted"),
    (detect_cli_auth_error, "InvalidToken: login required"),
])
def test_native_errors_still_stop_the_cli(detect, message):
    assert detect(json.dumps({"type": "error", "error": {"name": "APIError", "data": {"message": message}}}))


def test_completed_cataloging_receipt_does_not_stop_a_real_subprocess():
    payload = json.dumps({"type": "tool_use", "part": {
        "tool": "siming_turn_save_external_cataloging_facts",
        "state": {"status": "completed", "input": {"facts": [{"payload": {"summary": NARRATIVE}}]},
                  "output": "Saved 19 facts"},
    }}, ensure_ascii=False).encode("utf-8") + b"\n"

    async def run():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", f"import sys; sys.stdout.buffer.write({payload!r}); sys.stdout.flush()",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await communicate_with_cli_quota_detection(
            process, timeout_seconds=5, stop_on_permission_request=True, poll_seconds=0.02,
        )
        assert process.returncode == 0
        assert stdout == payload and stderr == b""

    asyncio.run(run())


def test_authorized_managed_cli_may_stream_plain_novel_permission_words():
    payload = "张建国翻开记录本，核对‘是否允许摘录’一栏后让沈砚确认无误。\n".encode("utf-8")

    async def run():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            f"import sys; sys.stdout.buffer.write({payload!r}); sys.stdout.flush()",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await communicate_with_cli_quota_detection(
            process,
            timeout_seconds=5,
            stop_on_permission_request=False,
            poll_seconds=0.02,
        )
        assert process.returncode == 0
        assert stdout == payload and stderr == b""

    asyncio.run(run())
