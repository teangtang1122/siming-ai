"""Packaged desktop launcher for 司命 (Siming)."""
from __future__ import annotations

import os
import json
import hashlib
import socket
import sys
import tempfile
import threading
import traceback
import ctypes
import shutil
import subprocess
import time
from pathlib import Path

from app.core.system_trust import configure_system_trust


SYSTEM_TRUST_STATUS = configure_system_trust()

import uvicorn


APP_NAME = "Siming"
LEGACY_APP_NAMES = ("Moshu", "NovelWritingAgent")
DEFAULT_PORT = 8765
_STDIO_LOG_HANDLES = []


def _launcher_log_path() -> Path:
    try:
        home = _app_home()
        (home / "logs").mkdir(parents=True, exist_ok=True)
        return home / "logs" / "launcher.log"
    except Exception:
        return Path(tempfile.gettempdir()) / "siming-launcher.log"


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
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _app_home() -> Path:
    env_home = os.environ.get("SIMING_HOME") or os.environ.get("MOSHU_HOME") or os.environ.get("NOVEL_AGENT_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        base = Path.home()
    current = base / APP_NAME
    legacy_dirs = [base / name for name in LEGACY_APP_NAMES]
    legacy_dirs.extend(Path.home() / f".{name}" for name in LEGACY_APP_NAMES)
    for legacy_dir in legacy_dirs:
        if not legacy_dir.exists():
            continue
        legacy_db = legacy_dir / "novel_agent.db"
        current_db = current / "novel_agent.db"
        if legacy_db.exists() and legacy_db.stat().st_size > 0:
            if not current_db.exists() or current_db.stat().st_size < legacy_db.stat().st_size:
                return legacy_dir
    return current


def _launcher_settings_path(home: Path) -> Path:
    return home / "launcher-settings.json"


def _load_launcher_settings(home: Path) -> dict:
    path = _launcher_settings_path(home)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_launcher_settings(home: Path, settings: dict) -> None:
    path = _launcher_settings_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_launch_mode(value: object) -> str:
    return "browser" if str(value or "").strip().lower() == "browser" else "desktop"


def _saved_launch_mode(home: Path) -> str:
    return _normalize_launch_mode(_load_launcher_settings(home).get("launch_mode"))


def _use_browser_mode(home: Path, *, force_browser: bool = False, force_desktop: bool = False) -> bool:
    """Resolve one explicit launch mode before importing the WebView runtime."""
    return force_browser or (not force_desktop and _saved_launch_mode(home) == "browser")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().lower()


def _wait_for_process_exit(pid: int, timeout: float = 60.0) -> bool:
    """Wait for the old executable before replacing it from the new one."""
    if pid <= 0 or pid == os.getpid():
        return True
    if os.name == "nt":
        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if handle:
            try:
                result = ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout * 1000))
                return result == 0
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.25)
    return False


def _apply_staged_update() -> None:
    """Replace the old binary from a previously verified staged executable."""
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--apply-staged-update", action="store_true")
    parser.add_argument("--update-target", required=True)
    parser.add_argument("--wait-pid", required=True, type=int)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--update-metadata")
    args, _ = parser.parse_known_args()
    update_exe = Path(sys.executable).resolve()
    target_exe = Path(args.update_target).expanduser().resolve()
    if update_exe == target_exe:
        raise RuntimeError("The staged update cannot replace itself.")
    if not update_exe.is_file():
        raise RuntimeError("The staged update executable no longer exists.")
    expected_sha256 = str(args.expected_sha256 or "").strip().lower()
    if len(expected_sha256) != 64 or any(char not in "0123456789abcdef" for char in expected_sha256):
        raise RuntimeError("The staged update does not include a valid SHA-256 checksum.")
    if _sha256_file(update_exe) != expected_sha256:
        raise RuntimeError("The staged update no longer matches its verified SHA-256 checksum.")
    if not _wait_for_process_exit(args.wait_pid):
        raise RuntimeError("Timed out waiting for the previous Siming process to close.")

    replacement = target_exe.with_name(f"{target_exe.name}.updating")
    replacement.unlink(missing_ok=True)
    try:
        shutil.copy2(update_exe, replacement)
        os.replace(replacement, target_exe)
        if args.update_metadata:
            Path(args.update_metadata).expanduser().unlink(missing_ok=True)
        subprocess.Popen(
            [str(target_exe)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(target_exe.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        replacement.unlink(missing_ok=True)


def _pick_content_root(home: Path) -> Path | None:
    if (
        "--mcp-server" in sys.argv
        or os.environ.get("MOSHU_NO_GUI_FOLDER_PICKER")
        or "pytest" in sys.modules
    ):
        return None
    try:
        import tkinter
        from tkinter import filedialog, messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showinfo(
            "Siming 2.5",
            "请选择一个空文件夹作为小说文件镜像目录。\n数据库仍是权威数据源，旧数据会导出为可读镜像，方便 Claude/Codex 读取。",
        )
        while True:
            selected = filedialog.askdirectory(title="选择 Siming 小说数据目录")
            if not selected:
                root.destroy()
                return None
            path = Path(selected).expanduser().resolve()
            path.mkdir(parents=True, exist_ok=True)
            existing = [item for item in path.iterdir() if item.name not in {".DS_Store", "Thumbs.db"}]
            if not existing:
                root.destroy()
                return path
            messagebox.showwarning(
                "Siming 2.5",
                "请选择空目录，避免和已有文件混在一起。\n\n可以新建一个空文件夹后再选择。",
            )
    except Exception as exc:
        _log(f"content root picker skipped: {exc}")
        return None


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
    launcher_settings = _load_launcher_settings(home)
    content_root = (
        os.environ.get("SIMING_CONTENT_ROOT")
        or os.environ.get("MOSHU_CONTENT_ROOT")
        or launcher_settings.get("content_root")
    )
    if not content_root:
        content_root = str(home / "projects")
    os.environ.setdefault("SIMING_HOME", str(home))
    os.environ.setdefault("MOSHU_HOME", str(home))
    os.environ.setdefault("SIMING_CONTENT_ROOT", str(Path(content_root).expanduser().resolve()))
    os.environ.setdefault("MOSHU_CONTENT_ROOT", str(Path(content_root).expanduser().resolve()))
    model_root = os.environ.get("SIMING_MODEL_ROOT") or os.environ.get("MOSHU_MODEL_ROOT") or launcher_settings.get("model_root")
    if not model_root:
        model_root = str(home / "models")
    os.environ.setdefault("SIMING_MODEL_ROOT", str(Path(model_root).expanduser().resolve()))
    os.environ.setdefault("MOSHU_MODEL_ROOT", str(Path(model_root).expanduser().resolve()))
    os.environ.setdefault("SIMING_KEY_FILE", str(home / ".crypto_key"))
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


# ─── Splash HTML (pure local, zero network) ───

SPLASH_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>司命</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #f6f2ea;
    display: flex; align-items: center; justify-content: center;
    height: 100vh;
    font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
    overflow: hidden; -webkit-font-smoothing: antialiased;
    user-select: none;
  }
  .splash { text-align: center; animation: fadeIn 0.5s ease both; }
  .splash-icon {
    width: 80px; height: 80px; margin: 0 auto 24px;
    animation: floatIn 0.7s cubic-bezier(0.22,1,0.36,1) 0.1s both;
  }
  .splash-icon svg { width: 100%; height: 100%; filter: drop-shadow(0 4px 12px rgba(44,36,23,0.1)); }
  .splash-title {
    font-family: 'SimSun', serif;
    font-size: 42px; font-weight: 700; letter-spacing: 0.12em;
    color: #2c2417; margin-bottom: 8px;
    animation: inkReveal 0.6s cubic-bezier(0.22,1,0.36,1) 0.15s both;
  }
  .splash-sub {
    font-size: 15px; color: #a89c88; letter-spacing: 0.15em;
    font-weight: 300; margin-bottom: 48px;
    animation: fadeIn 0.4s ease 0.35s both;
  }
  .splash-divider {
    width: 64px; height: 2px; margin: 0 auto 40px;
    background: linear-gradient(90deg, transparent, #7c5e2a 20%, #7c5e2a 60%, transparent);
    opacity: 0.4; animation: brushReveal 0.7s cubic-bezier(0.22,1,0.36,1) 0.4s both;
  }
  .splash-status {
    font-size: 14px; color: #a89c88; letter-spacing: 0.04em;
    min-height: 22px; transition: opacity 0.3s ease;
  }
  .splash-dots {
    display: inline-flex; gap: 6px; margin-top: 20px;
    animation: fadeIn 0.3s ease 0.5s both;
  }
  .splash-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: #7c5e2a; opacity: 0.3;
    animation: dotPulse 1.4s ease-in-out infinite;
  }
  .splash-dot:nth-child(2) { animation-delay: 0.2s; }
  .splash-dot:nth-child(3) { animation-delay: 0.4s; }
  body::before {
    content: ''; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    opacity: 0.018; pointer-events: none; z-index: 9999;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
    background-repeat: repeat; background-size: 256px 256px; mix-blend-mode: multiply;
  }
  .splash-glow {
    position: fixed; width: 400px; height: 400px; border-radius: 50%;
    background: radial-gradient(circle, rgba(124,94,42,0.04) 0%, transparent 70%);
    pointer-events: none; animation: floatGlow 6s ease-in-out infinite;
  }
  .splash-glow-1 { top: 10%; left: 15%; }
  .splash-glow-2 { bottom: 10%; right: 15%; animation-delay: 3s; }
  @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
  @keyframes floatIn { from { opacity: 0; transform: translateY(16px) scale(0.95); } to { opacity: 1; transform: translateY(0) scale(1); } }
  @keyframes inkReveal { 0% { opacity: 0; transform: scale(0.92); filter: blur(4px); } 60% { opacity: 1; filter: blur(0); } 100% { opacity: 1; transform: scale(1); filter: blur(0); } }
  @keyframes brushReveal { from { clip-path: inset(0 100% 0 0); opacity: 0.2; } to { clip-path: inset(0 0 0 0); opacity: 0.4; } }
  @keyframes dotPulse { 0%, 100% { opacity: 0.2; transform: scale(0.8); } 50% { opacity: 0.8; transform: scale(1.2); } }
  @keyframes floatGlow { 0%, 100% { transform: translate(0, 0); } 50% { transform: translate(10px, -10px); } }
</style>
</head>
<body>
  <div class="splash-glow splash-glow-1"></div>
  <div class="splash-glow splash-glow-2"></div>
  <div class="splash">
    <div class="splash-icon">
      <svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
        <defs><linearGradient id="ink" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#2c2417"/><stop offset="50%" stop-color="#4a3c28"/><stop offset="100%" stop-color="#7c5e2a"/></linearGradient></defs>
        <rect width="64" height="64" rx="14" fill="#f6f2ea" stroke="#e4ddd0" stroke-width="1"/>
        <path d="M18 14 Q20 12 22 14 L22 50 Q20 52 18 50 Z" fill="url(#ink)" opacity="0.9"/>
        <path d="M26 20 Q28 18 44 20 Q46 22 44 24 L28 24 Q26 22 26 20 Z" fill="url(#ink)" opacity="0.8"/>
        <path d="M26 30 Q28 28 40 30 Q42 32 40 34 L28 34 Q26 32 26 30 Z" fill="url(#ink)" opacity="0.7"/>
        <path d="M26 40 Q28 38 36 40 Q38 42 36 44 L28 44 Q26 42 26 40 Z" fill="url(#ink)" opacity="0.6"/>
        <circle cx="48" cy="48" r="4" fill="#7c5e2a" opacity="0.25"/>
      </svg>
    </div>
    <div class="splash-title">司命</div>
    <div class="splash-sub">长篇小说的命运织机</div>
    <div class="splash-divider"></div>
    <div class="splash-status" id="status">正在启动</div>
    <div class="splash-dots" id="dots"><div class="splash-dot"></div><div class="splash-dot"></div><div class="splash-dot"></div></div>
  </div>
</body>
</html>"""


def _run_mcp_server() -> None:
    """Run the MCP server over stdio."""
    import argparse
    parser = argparse.ArgumentParser(prog="mcp-server")
    parser.add_argument("--mcp-server", action="store_true", help="Run MCP server over stdio")
    parser.add_argument("--project-id", default="", help="Optional default project ID.")
    parser.add_argument(
        "--permission-pack",
        default=os.environ.get("MOSHU_MCP_PERMISSION_PACK", "auto"),
        choices=["auto", "readonly_collaboration", "draft_generation", "project_writing",
                 "project_management", "internal_llm", "trusted_local_maintenance",
                 "cataloging_worker"],
    )
    args, _ = parser.parse_known_args()
    _configure_stdio_utf8()
    _prepare_data_environment()
    from app.database.bootstrap import bootstrap_database
    from app.database.session import SessionLocal, engine

    bootstrap = bootstrap_database(engine)
    if bootstrap.read_only:
        raise RuntimeError(
            "MCP cannot start while the database is in read-only recovery mode: "
            + bootstrap.message
        )
    from app.bootstrap.composition import configure_application_services
    from app.mcp.server import serve_stdio

    configure_application_services()
    db = SessionLocal()
    try:
        serve_stdio(db=db, project_id=args.project_id, permission_pack=args.permission_pack)
    finally:
        db.close()
        from app.services.local_runtime import get_runtime_manager

        get_runtime_manager().stop()


def _wait_for_server(host: str, port: int, timeout: float = 30.0) -> bool:
    """Poll until the server accepts TCP connections."""
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
    _log(
        "HTTPS trust backend: "
        f"{SYSTEM_TRUST_STATUS.backend}; enabled={SYSTEM_TRUST_STATUS.enabled}"
    )
    if "--mcp-server" in sys.argv:
        _run_mcp_server()
        return

    _redirect_missing_stdio_to_log()

    if "--apply-staged-update" in sys.argv:
        try:
            _apply_staged_update()
        except Exception as exc:
            _show_error(f"{APP_NAME} 更新失败", str(exc))
            _log("Staged update failed:\n" + traceback.format_exc())
        return

    force_browser = "--browser" in sys.argv
    force_desktop = "--desktop" in sys.argv
    if force_browser:
        sys.argv.remove("--browser")
    if force_desktop:
        sys.argv.remove("--desktop")

    # ── Absolute minimum before window: just find a port and set env vars ──
    port = _find_free_port()
    home = _prepare_environment(port)
    use_browser = _use_browser_mode(home, force_browser=force_browser, force_desktop=force_desktop)

    gui_url = f"http://127.0.0.1:{port}/gui"
    _log(f"Port: {port}; Data: {home}; Launch mode: {'browser' if use_browser else 'desktop'}")

    if use_browser:
        # Browser mode: need server first
        from app.main import app
        threading.Thread(target=lambda: uvicorn.run(app, host="127.0.0.1", port=port, log_level="info", access_log=False), daemon=True).start()
        if not _wait_for_server("127.0.0.1", port, timeout=30):
            _show_error(f"{APP_NAME} 启动失败", f"后端超时。\n日志：{_launcher_log_path()}")
            return
        import webbrowser
        webbrowser.open(gui_url)
        threading.Event().wait()  # block forever
        return

    # ── Native GUI: window opens NOW, everything else in background ──
    try:
        import webview
    except Exception:
        _log("pywebview not available:\n" + traceback.format_exc())
        _show_error(f"{APP_NAME} 启动失败", f"pywebview 不可用。\n日志：{_launcher_log_path()}")
        return

    # Create window — this returns immediately, window is visible
    window = webview.create_window(
        title=f"{APP_NAME}",
        html=SPLASH_HTML,
        width=1400,
        height=900,
        min_size=(800, 600),
        text_select=True,
    )

    def _boot():
        """Run application startup in the background after the window is visible."""
        try:
            # Automatic updates are intentionally disabled; Settings owns the flow.
            # Import FastAPI app (DB init and migrations are the heavy part).
            _log("Importing app.main...")
            from app.main import app
            _log("app.main imported")

            # Start uvicorn.
            threading.Thread(
                target=lambda: uvicorn.run(app, host="127.0.0.1", port=port, log_level="info", access_log=False),
                daemon=True,
            ).start()

            # Wait for TCP readiness.
            if _wait_for_server("127.0.0.1", port, timeout=30):
                _log(f"Server ready → {gui_url}")
                time.sleep(0.3)
                window.load_url(gui_url)
            else:
                _log("Server timeout (30s)")
                window.evaluate_js(
                    "document.getElementById('status').textContent='启动超时，请检查日志后重试';"
                    "document.getElementById('status').style.color='#b84233';"
                    "document.getElementById('dots').style.display='none';"
                )
        except Exception:
            _log("Boot failed:\n" + traceback.format_exc())
            try:
                window.evaluate_js(
                    "document.getElementById('status').textContent='启动失败';"
                    "document.getElementById('status').style.color='#b84233';"
                    "document.getElementById('dots').style.display='none';"
                )
            except Exception:
                pass

    threading.Thread(target=_boot, daemon=True).start()
    _log("Window visible, boot thread started")
    webview.start()
    _log("pywebview closed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _log("Fatal:\n" + traceback.format_exc())
        if "--mcp-server" not in sys.argv:
            _show_error(f"{APP_NAME} 启动失败", f"{exc}\n\n日志：{_launcher_log_path()}")
        _safe_print(f"Startup failed: {exc}", error=True)
        if getattr(sys.stdin, "isatty", lambda: False)():
            input("Press Enter to exit...")
        raise
