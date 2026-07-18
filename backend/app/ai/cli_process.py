"""Cross-provider helpers for launching and terminating local CLI processes."""

from __future__ import annotations

import asyncio
import os
import subprocess
from contextlib import suppress


def hidden_subprocess_kwargs() -> dict:
    """Hide transient CLI windows when Siming launches model CLIs on Windows."""
    if os.name != "nt":
        return {}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": creationflags} if creationflags else {}


async def terminate_cli_process_tree(process: asyncio.subprocess.Process) -> None:
    """Terminate a CLI tree without blocking the event loop on Windows."""
    if process.returncode is not None:
        return
    if os.name == "nt":
        taskkill_process: asyncio.subprocess.Process | None = None
        try:
            taskkill_process = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/F",
                "/T",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **hidden_subprocess_kwargs(),
            )
            await asyncio.wait_for(taskkill_process.wait(), timeout=1.0)
        except Exception:
            if taskkill_process and taskkill_process.returncode is None:
                with suppress(ProcessLookupError):
                    taskkill_process.kill()
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
    else:
        with suppress(ProcessLookupError):
            process.kill()
    with suppress(Exception):
        await asyncio.wait_for(process.wait(), timeout=1.0)
