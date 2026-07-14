"""Managed OpenCode installation and discovery for first-time Siming users."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.request import Request, urlopen

from app.ai.local_cli_adapter import (
    OPENCODE_DEFAULT_MODEL,
    OPENCODE_MODELS,
    discover_local_cli_models,
    hidden_subprocess_kwargs,
)


OPENCODE_RELEASE_API = "https://api.github.com/repos/anomalyco/opencode/releases/latest"
OPENCODE_RELEASES_URL = "https://github.com/anomalyco/opencode/releases/latest"
OPENCODE_INSTALL_DOCS_URL = "https://opencode.ai/docs/#install"
OPENCODE_MODELS_DOCS_URL = "https://opencode.ai/docs/providers/#opencode-zen"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
INSPECTION_CACHE_SECONDS = 30

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_inspection_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_inspection_cache_lock = threading.Lock()


def _app_home() -> Path:
    configured = os.environ.get("SIMING_HOME") or os.environ.get("MOSHU_HOME") or os.environ.get("NOVEL_AGENT_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return (Path(local_app_data) / "Siming").resolve()
    return (Path.home() / "Siming").resolve()


def managed_opencode_root() -> Path:
    return _app_home() / "managed-cli" / "opencode"


def managed_opencode_command() -> Path:
    return managed_opencode_root() / "bin" / "opencode.exe"


def _resolve_candidate(candidate: str | None) -> str | None:
    value = str(candidate or "").strip().strip('"')
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_file():
        return str(path.resolve())
    resolved = shutil.which(value)
    return str(Path(resolved).resolve()) if resolved else None


def resolve_opencode_command(preferred: str | None = None) -> str | None:
    candidates = [preferred, str(managed_opencode_command()), "opencode.cmd", "opencode.exe", "opencode"]
    for candidate in candidates:
        resolved = _resolve_candidate(candidate)
        if resolved:
            return resolved
    return None


def _subprocess_command(command: str, args: list[str]) -> list[str]:
    if os.name == "nt" and Path(command).suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/d", "/s", "/c", command, *args]
    return [command, *args]


def _command_version(command: str, *, timeout: int = 5) -> str | None:
    try:
        result = subprocess.run(
            _subprocess_command(command, ["--version"]),
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = (result.stdout or result.stderr or "").strip().splitlines()
    return value[0][:100] if value else None


def is_free_opencode_model(model_id: str) -> bool:
    normalized = str(model_id or "").strip().lower()
    return normalized.endswith("-free") or normalized == "opencode/big-pickle"


def _free_model_options(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    free = []
    for item in models:
        model_id = str(item.get("id") or "").strip()
        if not is_free_opencode_model(model_id):
            continue
        free.append({
            "id": model_id,
            "display_name": str(item.get("display_name") or model_id),
            "recommended": model_id == OPENCODE_DEFAULT_MODEL,
        })
    return free


def _inspection_cache_key(command: str | None) -> tuple[str, int]:
    if not command:
        return ("", 0)
    try:
        modified = Path(command).stat().st_mtime_ns
    except OSError:
        modified = 0
    return (command, modified)


def clear_opencode_inspection_cache() -> None:
    with _inspection_cache_lock:
        _inspection_cache.clear()


def inspect_opencode(
    preferred_command: str | None = None,
    *,
    timeout: int = 8,
    refresh: bool = False,
) -> dict[str, Any]:
    command = resolve_opencode_command(preferred_command)
    cache_key = _inspection_cache_key(command)
    now = time.monotonic()
    if not refresh:
        with _inspection_cache_lock:
            cached = _inspection_cache.get(cache_key)
            if cached and now - cached[0] < INSPECTION_CACHE_SECONDS:
                return deepcopy(cached[1])

    version = _command_version(command, timeout=min(max(timeout, 2), 6)) if command else None
    discovered = discover_local_cli_models("opencode_cli", command, timeout=timeout) if command else []
    model_source = "cli"
    if command and not discovered:
        discovered = [{"id": model, "display_name": model} for model in OPENCODE_MODELS]
        model_source = "fallback"
    free_models = _free_model_options(discovered)
    recommended = next((item["id"] for item in free_models if item["recommended"]), None)
    if not recommended and free_models:
        recommended = free_models[0]["id"]
    managed_root = managed_opencode_root().resolve()
    managed = False
    if command:
        try:
            Path(command).resolve().relative_to(managed_root)
            managed = True
        except (OSError, ValueError):
            managed = False
    result = {
        "installed": bool(command and version),
        "command": command,
        "version": version,
        "managed_by_siming": managed,
        "models": discovered,
        "model_source": model_source if command else "none",
        "free_models": free_models,
        "recommended_model": recommended,
        "platform_supported": os.name == "nt" and platform.machine().lower() in {"amd64", "x86_64", "arm64", "aarch64"},
        "install_location": str(managed_opencode_command()),
        "official_links": {
            "releases": OPENCODE_RELEASES_URL,
            "install_docs": OPENCODE_INSTALL_DOCS_URL,
            "model_docs": OPENCODE_MODELS_DOCS_URL,
        },
    }
    with _inspection_cache_lock:
        _inspection_cache.clear()
        _inspection_cache[cache_key] = (now, deepcopy(result))
    return result


def _release_asset_name() -> str:
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "opencode-windows-arm64.zip"
    if machine in {"amd64", "x86_64"}:
        return "opencode-windows-x64.zip"
    raise RuntimeError(f"暂不支持当前 Windows 架构：{platform.machine() or 'unknown'}")


def _load_json(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Siming-OpenCode-Onboarding",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _latest_release_asset() -> tuple[str, dict[str, Any]]:
    release = _load_json(OPENCODE_RELEASE_API)
    version = str(release.get("tag_name") or "").strip()
    asset_name = _release_asset_name()
    asset = next((item for item in release.get("assets", []) if item.get("name") == asset_name), None)
    if not version or not asset:
        raise RuntimeError("OpenCode 官方发行页暂时没有可用的 Windows CLI 安装包")
    digest = str(asset.get("digest") or "")
    if not digest.startswith("sha256:"):
        raise RuntimeError("OpenCode 官方安装包缺少 SHA256 摘要，司命已停止自动安装")
    return version, asset


def _set_job(job_id: str, **changes: Any) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs[job_id]
        job.update(changes)
        job["updated_at"] = datetime.now(timezone.utc).isoformat()
        return dict(job)


def _download_asset(
    url: str,
    destination: Path,
    *,
    expected_sha256: str,
    progress: Callable[[int, int], None],
) -> None:
    request = Request(url, headers={"User-Agent": "Siming-OpenCode-Onboarding"})
    sha256 = hashlib.sha256()
    with urlopen(request, timeout=60) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        while True:
            chunk = response.read(DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            output.write(chunk)
            sha256.update(chunk)
            downloaded += len(chunk)
            progress(downloaded, total)
    actual = sha256.hexdigest().lower()
    if actual != expected_sha256.lower():
        destination.unlink(missing_ok=True)
        raise RuntimeError("OpenCode 安装包 SHA256 校验失败，文件已删除")


def _extract_opencode(zip_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        member = next(
            (
                item for item in archive.infolist()
                if not item.is_dir() and PurePosixPath(item.filename).name.lower() == "opencode.exe"
            ),
            None,
        )
        if member is None:
            raise RuntimeError("OpenCode 官方安装包中没有找到 opencode.exe")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".exe.new")
        with archive.open(member) as source, temporary.open("wb") as output:
            shutil.copyfileobj(source, output, length=DOWNLOAD_CHUNK_SIZE)
        os.replace(temporary, destination)


def _install_worker(job_id: str) -> None:
    root = managed_opencode_root()
    try:
        _set_job(job_id, status="running", phase="checking_release", percent=2, message="正在读取 OpenCode 官方发行信息")
        version, asset = _latest_release_asset()
        expected_sha256 = str(asset["digest"]).removeprefix("sha256:")
        download_url = str(asset.get("browser_download_url") or "")
        if not download_url:
            raise RuntimeError("OpenCode 官方安装包没有下载地址")
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="download-", dir=root) as temporary_dir:
            archive_path = Path(temporary_dir) / str(asset["name"])

            def on_progress(downloaded: int, total: int) -> None:
                fraction = downloaded / total if total else 0
                _set_job(
                    job_id,
                    phase="downloading",
                    percent=max(5, min(85, int(5 + fraction * 80))),
                    bytes_downloaded=downloaded,
                    bytes_total=total,
                    message="正在从 OpenCode 官方 GitHub 下载 Windows CLI",
                )

            _download_asset(download_url, archive_path, expected_sha256=expected_sha256, progress=on_progress)
            _set_job(job_id, phase="installing", percent=90, message="下载完成，正在解压到司命专用目录")
            command = managed_opencode_command()
            _extract_opencode(archive_path, command)

        _set_job(job_id, phase="verifying", percent=96, message="正在检查 OpenCode 是否可以运行")
        inspected = inspect_opencode(str(managed_opencode_command()), timeout=15, refresh=True)
        if not inspected["installed"]:
            raise RuntimeError("OpenCode 已下载，但启动检查失败")
        metadata = {
            "version": version,
            "asset": asset["name"],
            "sha256": expected_sha256,
            "source": download_url,
            "command": str(managed_opencode_command()),
            "installed_at": datetime.now(timezone.utc).isoformat(),
        }
        metadata_path = root / "install.json"
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        _set_job(
            job_id,
            status="completed",
            phase="completed",
            percent=100,
            message="OpenCode 已安装，可以选择免费模型",
            command=inspected["command"],
            version=inspected["version"] or version,
            free_models=inspected["free_models"],
            recommended_model=inspected["recommended_model"],
            sha256=expected_sha256,
        )
    except Exception as exc:
        _set_job(
            job_id,
            status="failed",
            phase="failed",
            message="OpenCode 自动安装没有完成",
            error=str(exc),
            next_action="检查网络后重试；也可以打开 OpenCode 官方发行页手动下载。",
        )


def start_opencode_install() -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("当前自动安装仅支持 Windows")
    with _jobs_lock:
        running = next((dict(job) for job in _jobs.values() if job.get("status") in {"pending", "running"}), None)
        if running:
            return running
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        _jobs[job_id] = {
            "id": job_id,
            "status": "pending",
            "phase": "queued",
            "percent": 0,
            "message": "安装任务已创建",
            "bytes_downloaded": 0,
            "bytes_total": 0,
            "created_at": now,
            "updated_at": now,
        }
        result = dict(_jobs[job_id])
    threading.Thread(target=_install_worker, args=(job_id,), daemon=True, name=f"opencode-install-{job_id[:8]}").start()
    return result


def get_opencode_install_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None
