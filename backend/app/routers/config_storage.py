"""Application home, logs, and content-root configuration routes."""
from __future__ import annotations

import os
import webbrowser
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.architecture.uow import commit_session
from app.core.exceptions import ValidationError
from app.core.legacy_env import set_compatible_env
from app.core.response import ApiResponse
from app.database.session import get_db
from app.services.application_settings import app_home as _app_home
from app.services.application_settings import load_launcher_settings as _load_launcher_settings
from app.services.application_settings import save_launcher_settings as _save_launcher_settings
from app.services.content_store import content_root as resolve_content_root
from app.services.content_store import migrate_projects_to_content_root


router = APIRouter(tags=["config"])


class ContentRootUpdateRequest(BaseModel):
    path: str = Field(..., min_length=1)


def _default_content_root() -> Path:
    return (_app_home() / "projects").expanduser().resolve()


def _path_is_empty(path: Path) -> bool:
    if not path.exists():
        return True
    ignored = {".DS_Store", "Thumbs.db", "desktop.ini"}
    return not any(item.name not in ignored for item in path.iterdir())


def _looks_like_siming_content_root(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    for child in path.iterdir():
        if child.is_dir() and (child / "moshu-project.json").exists():
            return True
    return False


def _content_root_payload(extra: dict | None = None) -> dict:
    settings = _load_launcher_settings()
    configured = settings.get("content_root")
    current = resolve_content_root()
    default = _default_content_root()
    looks_like_root = _looks_like_siming_content_root(current)
    payload = {
        "current_path": str(current),
        "configured_path": configured,
        "default_path": str(default),
        "is_default": not configured and current == default,
        "exists": current.exists(),
        "is_empty": _path_is_empty(current),
        "looks_like_siming_root": looks_like_root,
        "looks_like_moshu_root": looks_like_root,
    }
    if extra:
        payload.update(extra)
    return payload


def _apply_content_root(db: Session, raw_path: str) -> dict:
    target = Path(raw_path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    current = resolve_content_root()
    if target != current and not _path_is_empty(target) and not _looks_like_siming_content_root(target):
        raise ValidationError("小说数据目录必须是空文件夹，或已经是 Siming 小说数据目录")
    settings = _load_launcher_settings()
    previous = current
    set_compatible_env("SIMING_CONTENT_ROOT", str(target))
    settings["content_root"] = str(target)
    _save_launcher_settings(settings)
    migration = migrate_projects_to_content_root(db, target, previous_root=previous, cleanup_old=True)
    commit_session(db)
    return _content_root_payload({"migration": migration})


def _pick_empty_content_root() -> Path | None:
    try:
        import tkinter
        from tkinter import filedialog, messagebox

        root = tkinter.Tk()
        root.withdraw()
        while True:
            selected = filedialog.askdirectory(title="选择 Siming 小说数据目录")
            if not selected:
                root.destroy()
                return None
            path = Path(selected).expanduser().resolve()
            path.mkdir(parents=True, exist_ok=True)
            if _path_is_empty(path) or _looks_like_siming_content_root(path):
                root.destroy()
                return path
            messagebox.showwarning(
                "Siming 小说数据目录",
                "请选择空目录，或已经由 Siming 创建过的小说数据目录。",
            )
    except Exception as exc:
        raise ValidationError(f"无法打开文件夹选择器：{exc}")


@router.post("/system/open-home")
def open_home_in_default_browser(request: Request):
    """Open the Siming web home in the user's default browser."""
    home_url = str(request.base_url).rstrip("/") + "/"
    webbrowser.open(home_url)
    return ApiResponse.success(data={"url": home_url}, message="Siming home opened in the default browser")


@router.get("/config/content-root")
def get_content_root_settings():
    """Return the current Siming 2.x novel data directory setting."""
    return ApiResponse.success(data=_content_root_payload())


@router.put("/config/content-root")
def update_content_root_settings(payload: ContentRootUpdateRequest, db: Session = Depends(get_db)):
    """Set the Siming novel data directory and migrate existing project files."""
    return ApiResponse.success(data=_apply_content_root(db, payload.path), message="小说数据目录已更新")


@router.post("/config/content-root/pick")
def pick_content_root_settings(db: Session = Depends(get_db)):
    """Open a native folder picker and set the selected Siming data directory."""
    selected = _pick_empty_content_root()
    if not selected:
        return ApiResponse.success(data=_content_root_payload({"cancelled": True}), message="已取消选择")
    return ApiResponse.success(data=_apply_content_root(db, str(selected)), message="小说数据目录已更新")


def _system_log_path() -> Path:
    """Resolve the log file shown by the GUI terminal page.

    Application runtime logs (``siming.log``) are preferred once they exist;
    the legacy startup-only ``launcher.log`` remains the fallback so boot-time
    diagnostics stay reachable.
    """
    home = _app_home()
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        home / "logs" / "siming.log",
        home / "logs" / "launcher.log",
    ]
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "NovelWritingAgent" / "logs" / "launcher.log"
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    # No log file yet (fresh install before first server run): keep reporting
    # the location that will exist so the UI shows an actionable path.
    return home / "logs" / "launcher.log"


@router.get("/system/logs")
def get_system_logs(lines: int = 200):
    """Read the last N lines of the backend runtime or launcher log file."""
    log_path = _system_log_path()
    if not log_path.exists():
        return ApiResponse.success(data={"path": str(log_path), "content": "(log file not found)", "lines": 0})

    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as stream:
            all_lines = stream.readlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return ApiResponse.success(data={
            "path": str(log_path),
            "content": "".join(tail),
            "lines": len(tail),
            "total": len(all_lines),
        })
    except Exception as exc:
        return ApiResponse.success(data={"path": str(log_path), "content": f"(read failed: {exc})", "lines": 0})
