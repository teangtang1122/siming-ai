"""Packaged desktop launcher for 墨枢 (Moshu)."""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import traceback
from pathlib import Path

import uvicorn


APP_NAME = "Moshu"
LEGACY_APP_NAME = "NovelWritingAgent"
DEFAULT_PORT = 8765
_STDIO_LOG_HANDLES = []


def _launcher_log_path() -> Path:
    try:
        home = _app_home()
        (home / "logs").mkdir(parents=True, exist_ok=True)
        return home / "logs" / "launcher.log"
    except Exception:
        return Path(tempfile.gettempdir()) / "moshu-launcher.log"


def _log(message: str) -> None:
    from datetime import datetime

    try:
        path = _launcher_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")
    except Exception:
        pass


def _show_error(title: str, message: str) -> None:
    _log(f"{title}: {message}")
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        pass


def _safe_print(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    if stream is None:
        _log(message)
        return
    try:
        print(message, file=stream)
    except Exception:
        _log(message)


def _redirect_missing_stdio_to_log() -> None:
    """Windowed executables may have sys.stdout/stderr set to None.

    Uvicorn's logging setup expects stderr to exist and have isatty(), so route
    missing streams to the launcher log in GUI mode. MCP stdio mode must not call
    this because stdout/stdin are the protocol transport.
    """
    path = _launcher_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        stdout_log = path.open("a", encoding="utf-8", buffering=1)
        sys.stdout = stdout_log
        _STDIO_LOG_HANDLES.append(stdout_log)
    if sys.stderr is None:
        stderr_log = path.open("a", encoding="utf-8", buffering=1)
        sys.stderr = stderr_log
        _STDIO_LOG_HANDLES.append(stderr_log)


def _configure_stdio_utf8() -> None:
    """Prefer UTF-8 stdio for MCP mode on Windows hosts."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


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
            "internal_llm",
            "trusted_local_maintenance",
        ],
        help="MCP permission pack to expose. 'auto' resolves from global/project settings.",
    )
    args, _ = parser.parse_known_args()
    _configure_stdio_utf8()

    _prepare_data_environment()

    from app.database.session import SessionLocal
    from app.mcp.server import serve_stdio

    db = SessionLocal()
    try:
        serve_stdio(db=db, project_id=args.project_id, permission_pack=args.permission_pack)
    finally:
        db.close()


def _run_server_in_background(app, host: str, port: int) -> None:
    """Run uvicorn in a background thread so the GUI event loop can run on the main thread."""
    try:
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
        )
    except Exception:
        _log("Server thread crashed:\n" + traceback.format_exc())
        raise


def _wait_for_server(host: str, port: int, timeout: float = 30.0) -> bool:
    """Poll until the server is accepting connections, or timeout."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main() -> None:
    _log(f"{APP_NAME} launcher entered with argv={sys.argv!r}")
    # Check for MCP server mode
    if "--mcp-server" in sys.argv:
        _log("Starting MCP stdio server")
        _run_mcp_server()
        return

    _redirect_missing_stdio_to_log()

    # Check for --browser flag to use old browser-based launch
    use_browser = "--browser" in sys.argv
    if use_browser:
        sys.argv.remove("--browser")

    port = _find_free_port()
    home = _prepare_environment(port)
    from app.updater import apply_update_if_available

    try:
        if apply_update_if_available(home):
            _log("Update was scheduled; exiting current process.")
            return
    except Exception as exc:
        _log("Update check failed:\n" + traceback.format_exc())
        _safe_print(f"Update check failed: {exc}")

    url = f"http://127.0.0.1:{port}"
    gui_url = f"{url}/#/gui"
    _safe_print(f"{APP_NAME} starting...")
    _safe_print(f"Data directory: {home}")
    _safe_print(f"Open: {url}")
    _log(f"Data directory: {home}")
    _log(f"HTTP URL: {url}; GUI URL: {gui_url}")

    from app.main import app

    # Start the server in a background thread
    server_thread = threading.Thread(
        target=_run_server_in_background,
        args=(app, "127.0.0.1", port),
        daemon=True,
    )
    server_thread.start()

    # Wait for the server to be ready before opening any window
    _safe_print("Waiting for server to start...")
    if not _wait_for_server("127.0.0.1", port, timeout=30):
        log_path = _launcher_log_path()
        message = (
            f"后端服务没有在 30 秒内启动。\n\n"
            f"端口：{port}\n"
            f"日志：{log_path}\n\n"
            "可以稍后重试，或用命令行添加 --browser 以浏览器模式启动。"
        )
        _safe_print(f"Server did not start within 30 seconds on port {port}.")
        _show_error(f"{APP_NAME} 启动失败", message)
        return
    _safe_print(f"Server ready on port {port}.")
    _log(f"Server ready on port {port}")

    if use_browser:
        # Legacy browser mode
        import webbrowser
        webbrowser.open(url)
        server_thread.join()
    else:
        # Native GUI mode with pywebview
        try:
            import webview
            webview.create_window(
                title=f"{APP_NAME} — 控制面板",
                url=gui_url,
                width=1100,
                height=750,
                min_size=(800, 600),
                text_select=True,
            )
            _log("Starting pywebview GUI")
            webview.start()
            _log("pywebview GUI closed")
        except Exception:
            # Fallback to browser if pywebview is unavailable or the WebView runtime fails.
            import webbrowser
            _log("pywebview failed; falling back to browser:\n" + traceback.format_exc())
            _show_error(
                f"{APP_NAME} 图形窗口启动失败",
                f"桌面窗口启动失败，已尝试改用浏览器打开。\n\n日志：{_launcher_log_path()}",
            )
            webbrowser.open(gui_url)
            server_thread.join()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _log("Fatal startup failure:\n" + traceback.format_exc())
        if "--mcp-server" not in sys.argv:
            _show_error(f"{APP_NAME} 启动失败", f"{exc}\n\n日志：{_launcher_log_path()}")
        _safe_print(f"Startup failed: {exc}", error=True)
        if getattr(sys.stdin, "isatty", lambda: False)():
            input("Press Enter to exit...")
        raise
