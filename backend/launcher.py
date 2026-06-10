"""Packaged desktop launcher for 墨枢 (Moshu)."""
from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn


APP_NAME = "Moshu"
LEGACY_APP_NAME = "NovelWritingAgent"
DEFAULT_PORT = 8765


def _app_home() -> Path:
    env_home = os.environ.get("MOSHU_HOME") or os.environ.get("NOVEL_AGENT_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        base = Path.home()
    current = base / APP_NAME
    legacy = base / LEGACY_APP_NAME
    legacy_dot = Path.home() / f".{LEGACY_APP_NAME}"
    # If legacy has a real DB but current is empty/missing, keep using legacy
    for legacy_dir in (legacy, legacy_dot):
        if not legacy_dir.exists():
            continue
        legacy_db = legacy_dir / "novel_agent.db"
        current_db = current / "novel_agent.db"
        if legacy_db.exists() and legacy_db.stat().st_size > 0:
            if not current_db.exists() or current_db.stat().st_size < legacy_db.stat().st_size:
                return legacy_dir
    return current


def _find_free_port(start: int = DEFAULT_PORT, attempts: int = 50) -> int:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"Could not find a free local port from {start} to {start + attempts - 1}.")


def _prepare_data_environment() -> Path:
    home = _app_home()
    home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MOSHU_HOME", str(home))
    os.environ.setdefault("MOSHU_KEY_FILE", str(home / ".crypto_key"))
    os.environ.setdefault("NOVEL_AGENT_HOME", str(home))
    os.environ.setdefault("NOVEL_AGENT_KEY_FILE", str(home / ".crypto_key"))
    os.environ["DATABASE_URL"] = f"sqlite:///{(home / 'novel_agent.db').as_posix()}"
    return home


def _prepare_environment(port: int) -> Path:
    home = _prepare_data_environment()
    os.environ["CORS_ORIGINS"] = ",".join([
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
    ])
    return home


def _run_mcp_server() -> None:
    """Run the MCP server over stdio."""
    import argparse
    parser = argparse.ArgumentParser(prog="mcp-server")
    parser.add_argument("--mcp-server", action="store_true", help="Run MCP server over stdio")
    parser.add_argument(
        "--project-id",
        default="",
        help="Optional default project ID. Omit it to allow global project browsing.",
    )
    parser.add_argument(
        "--permission-pack",
        default=os.environ.get("MOSHU_MCP_PERMISSION_PACK", "auto"),
        choices=[
            "auto",
            "readonly_collaboration",
            "draft_generation",
            "project_writing",
            "project_management",
            "trusted_local_maintenance",
        ],
        help="MCP permission pack to expose. 'auto' resolves from global/project settings.",
    )
    args, _ = parser.parse_known_args()

    _prepare_data_environment()

    from app.database.session import SessionLocal
    from app.mcp.server import serve_stdio

    db = SessionLocal()
    try:
        serve_stdio(db=db, project_id=args.project_id, permission_pack=args.permission_pack)
    finally:
        db.close()


def main() -> None:
    # Check for MCP server mode
    if "--mcp-server" in sys.argv:
        _run_mcp_server()
        return

    port = _find_free_port()
    home = _prepare_environment(port)
    from app.updater import apply_update_if_available

    try:
        if apply_update_if_available(home):
            return
    except Exception as exc:
        print(f"Update check failed: {exc}")

    url = f"http://127.0.0.1:{port}"
    print(f"{APP_NAME} starting...")
    print(f"Data directory: {home}")
    print(f"Open: {url}")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    from app.main import app

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        input("Press Enter to exit...")
        raise
