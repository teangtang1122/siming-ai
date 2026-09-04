"""Resolve the Siming stdio MCP command without touching client config."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.crypto import key_file_path
from app.core.legacy_env import compatible_env_names
from app.services.application_settings import app_home


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def managed_mcp_environment() -> dict[str, str]:
    """Pin a transient MCP to its owning runtime, independent of child cwd.

    Settings may come from .env rather than os.environ. Without this explicit
    handoff, the source MCP launcher chooses the installed desktop database,
    where this runtime's turn guard and project do not exist.
    """
    from app.services.content_store import content_root

    url = make_url(get_settings().database_url)
    if url.get_backend_name() == "sqlite":
        if url.database in {None, "", ":memory:"} or url.query.get("mode") == "memory":
            raise ValueError("临时 MCP 需要与主进程共享持久数据库，不能使用内存数据库")
        url = url.set(database=str(Path(url.database).expanduser().resolve()))
    runtime_home = str(Path(key_file_path()).expanduser().resolve().parent)
    environment = {
        "DATABASE_URL": url.render_as_string(hide_password=False),
        "SIMING_CONTENT_ROOT": str(content_root()),
        "SIMING_KEY_FILE": key_file_path(),
        # Agent CLIs may launch stdio MCP servers with a filtered environment.
        # Pin every home alias as well as the concrete stores so the child
        # cannot rediscover an older installed-data directory.
        "SIMING_HOME": runtime_home,
    }
    for name in compatible_env_names("SIMING_HOME"):
        environment[name] = runtime_home
    return environment


def resolve_siming_mcp_server(
    *,
    permission_pack: str,
    project_id: str = "",
    creation_session_id: str = "",
    tool_category_state_file: str = "",
    direct_mcp_lease_token: str = "",
) -> dict[str, Any]:
    """Return an executable MCP spec for persistent or process-scoped clients."""

    scope_args = ["--permission-pack", permission_pack]
    if project_id:
        scope_args.extend(["--project-id", project_id])
    if creation_session_id:
        scope_args.extend(["--creation-session-id", creation_session_id])
    if tool_category_state_file:
        scope_args.extend(["--tool-category-state-file", tool_category_state_file])
    if direct_mcp_lease_token:
        scope_args.extend(["--direct-mcp-lease-token", direct_mcp_lease_token])

    if getattr(sys, "frozen", False):
        return {
            "mode": "exe",
            "command": str(Path(sys.executable).resolve()),
            "args": ["--mcp-server", *scope_args],
            # Never inherit another Agent's working directory. This also
            # prevents foreign dotenv/project configuration from leaking in.
            "cwd": str(app_home().resolve()),
        }

    root = _repo_root()
    entry = root / "scripts" / "moshu-mcp-server.py"
    if entry.exists():
        return {
            "mode": "source",
            "command": str(Path(sys.executable).resolve()),
            "args": [str(entry.resolve()), *scope_args],
            "cwd": str(root),
        }

    return {
        "mode": "python_module",
        "command": str(Path(sys.executable).resolve()),
        "args": ["-m", "app.mcp.server", *scope_args],
        "cwd": str(root),
    }


__all__ = ["managed_mcp_environment", "resolve_siming_mcp_server"]
