"""Managed OpenCode installation and discovery for first-time Siming users."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.request import Request, urlopen

from app.ai.local_cli_adapter import (
    OPENCODE_DEFAULT_MODEL,
    OPENCODE_MODELS,
    discover_local_cli_models,
    hidden_subprocess_kwargs,
)
from app.architecture.uow import commit_session
from app.services.application_settings import app_home as _app_home
from app.services.opencode_activation import (
    activation_failure_kind as _activation_failure_kind,
)
from app.services.opencode_activation import (
    probe_free_models as _probe_free_models,
)
from app.services.opencode_activation import (
    save_activated_config as _save_activated_config,
)
from app.services.opencode_activation import (
    save_readiness_failure as _save_activation_readiness_failure,
)
from app.services.opencode_activation import (
    test_model as _activation_test_model,
)
from app.services.opencode_release_catalog import managed_windows_release

OPENCODE_RELEASES_URL = "https://github.com/anomalyco/opencode/releases/latest"
OPENCODE_INSTALL_DOCS_URL = "https://opencode.ai/docs/#install"
OPENCODE_MODELS_DOCS_URL = "https://opencode.ai/docs/zen"
OPENCODE_AUTH_URL = "https://opencode.ai/auth"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
INSPECTION_CACHE_SECONDS = 30
ACTIVATION_TEST_TIMEOUT = 60


async def _test_opencode_model(command: str, model: str) -> None:
    await _activation_test_model(command, model, timeout_seconds=ACTIVATION_TEST_TIMEOUT)

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_activation_start_lock = threading.Lock()
_inspection_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_inspection_cache_lock = threading.Lock()
_auth_sessions_lock = threading.Lock()


@dataclass
class _ManagedAuthSession:
    process: Any
    credential: str = ""


_auth_sessions: dict[str, _ManagedAuthSession] = {}
_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_AUTH_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_AUTH_CREDENTIAL_PROMPT_RE = re.compile(
    r"(?i)(paste|enter|input|provide).{0,40}(token|code|credential)|"
    r"(token|code|credential).{0,40}(paste|enter|input|provide)|"
    r"请输入.{0,20}(令牌|验证码|凭据)",
)


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


def _latest_release_asset() -> tuple[str, dict[str, Any]]:
    return managed_windows_release()


def _set_job(job_id: str, **changes: Any) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs[job_id]
        job.update(changes)
        job["updated_at"] = datetime.now(UTC).isoformat()
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


def _mirror_urls(official_url: str, asset_name: str) -> list[str]:
    """Return operator-approved mirrors without trusting them for integrity."""
    configured = os.environ.get("SIMING_OPENCODE_MIRROR_URLS", "")
    urls = [official_url]
    for template in configured.split(";"):
        template = template.strip()
        if not template:
            continue
        candidate = template.replace("{url}", official_url).replace("{asset}", asset_name)
        if "{" not in candidate and candidate.startswith("https://") and candidate not in urls:
            urls.append(candidate)
    return urls


def _download_asset_resumable(
    url: str,
    destination: Path,
    *,
    expected_sha256: str,
    progress: Callable[[int, int], None],
) -> None:
    """Download with Range resume and verify the complete file afterwards."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = destination.stat().st_size if destination.exists() else 0
    if existing:
        digest = hashlib.sha256()
        with destination.open("rb") as source:
            for chunk in iter(lambda: source.read(DOWNLOAD_CHUNK_SIZE), b""):
                digest.update(chunk)
        if digest.hexdigest().lower() == expected_sha256.lower():
            progress(existing, existing)
            return
    headers = {"User-Agent": "Siming-OpenCode-Onboarding"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=60) as response:
        partial = existing > 0 and getattr(response, "status", None) == 206
        mode = "ab" if partial else "wb"
        downloaded = existing if partial else 0
        remaining = int(response.headers.get("Content-Length") or 0)
        total = downloaded + remaining if remaining else 0
        with destination.open(mode) as output:
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                progress(downloaded, total)

    digest = hashlib.sha256()
    with destination.open("rb") as source:
        for chunk in iter(lambda: source.read(DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != expected_sha256.lower():
        destination.unlink(missing_ok=True)
        raise RuntimeError("下载文件与 OpenCode 官方 SHA256 不一致，已删除并停止安装")


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
            "installed_at": datetime.now(UTC).isoformat(),
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
        now = datetime.now(UTC).isoformat()
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


def _activation_payload(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "operation_id": getattr(job, "operation_id", None),
        "status": job.status,
        "phase": job.phase,
        "percent": job.percent,
        "message": job.message or "",
        "error": job.error,
        "failure_kind": job.failure_kind,
        "next_action": job.next_action,
        "auth_mode": getattr(job, "auth_mode", None),
        "auth_status": getattr(job, "auth_status", None),
        "auth_prompt": getattr(job, "auth_prompt", None),
        "auth_url": getattr(job, "auth_url", None),
        "command": job.command,
        "version": job.version,
        "selected_model": job.selected_model,
        "preferred_model": job.preferred_model,
        "free_models": list(job.free_models_json or []),
        "download_url": job.download_url,
        "sha256": job.sha256,
        "bytes_downloaded": job.bytes_downloaded or 0,
        "bytes_total": job.bytes_total or 0,
        "estimated_seconds_remaining": job.estimated_seconds_remaining,
        "attempt_count": job.attempt_count or 0,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def _update_activation(job_id: str, **changes: Any) -> dict[str, Any]:
    from app.database.models import OpenCodeActivationJob
    from app.database.session import SessionLocal

    with SessionLocal() as db:
        job = db.query(OpenCodeActivationJob).filter(OpenCodeActivationJob.id == job_id).first()
        if not job:
            raise RuntimeError("OpenCode 激活任务不存在")
        old_phase = job.phase
        old_status = job.status
        old_message = job.message
        old_model = job.selected_model
        old_percent = int(job.percent or 0)
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = datetime.now(UTC).replace(tzinfo=None)
        if job.operation_id:
            from app.database.models import OperationRun
            from app.services.operation_runtime import update_operation

            operation = db.query(OperationRun).filter(OperationRun.id == job.operation_id).first()
            if operation:
                operation.model_source = job.selected_model or operation.model_source
                lifecycle = {
                    "pending": "queued",
                    "running": "running",
                    "auth_required": "waiting_user",
                    "ready": "completed",
                    "failed": "failed",
                }.get(job.status, "running")
                determinate = bool(job.phase == "downloading" and job.bytes_total)
                meaningful = (
                    old_phase != job.phase
                    or old_status != job.status
                    or old_message != job.message
                    or old_model != job.selected_model
                    or abs(int(job.percent or 0) - old_percent) >= 1
                )
                update_operation(
                    db,
                    operation,
                    status=lifecycle,
                    phase=job.phase,
                    message=job.message,
                    event_type="activation_progress" if meaningful else None,
                    payload={
                        "phase": job.phase,
                        "selected_model": job.selected_model,
                        "previous_model": old_model,
                        "bytes_downloaded": job.bytes_downloaded,
                        "bytes_total": job.bytes_total,
                    } if meaningful else None,
                    progress_mode="determinate" if determinate else "indeterminate",
                    progress_current=int(job.bytes_downloaded or 0) if determinate else None,
                    progress_total=int(job.bytes_total or 0) if determinate else None,
                    failure_class=job.failure_kind,
                    next_action=job.next_action,
                    checkpoint=meaningful and int(job.percent or 0) > old_percent,
                )
        commit_session(db)
        db.refresh(job)
        return _activation_payload(job)


def get_opencode_activation_job(job_id: str) -> dict[str, Any] | None:
    from app.database.models import OpenCodeActivationJob
    from app.database.session import SessionLocal

    with SessionLocal() as db:
        job = db.query(OpenCodeActivationJob).filter(OpenCodeActivationJob.id == job_id).first()
        return _activation_payload(job) if job else None


def get_latest_opencode_activation_job(db: Any | None = None) -> dict[str, Any] | None:
    from app.database.models import OpenCodeActivationJob

    if db is not None:
        job = db.query(OpenCodeActivationJob).order_by(OpenCodeActivationJob.created_at.desc()).first()
        return _activation_payload(job) if job else None
    from app.database.session import SessionLocal

    with SessionLocal() as session:
        job = session.query(OpenCodeActivationJob).order_by(OpenCodeActivationJob.created_at.desc()).first()
        return _activation_payload(job) if job else None


def _safe_auth_text(value: object, credential: str = "") -> str:
    text = _ANSI_ESCAPE_RE.sub("", str(value or "")).replace("\x00", " ")
    if credential:
        text = text.replace(credential, "[redacted]")
    text = re.sub(
        r"(?i)((?:token|credential|secret|authorization)\s*[:=]\s*)[^\s,;]+",
        r"\1[redacted]",
        text,
    )
    return " ".join(text.split())[-600:]


def _spawn_auth_process(command: str) -> Any:
    try:
        from winpty import PtyProcess
    except ImportError as exc:  # pragma: no cover - packaged Windows runtime owns the dependency
        raise RuntimeError("司命缺少托管登录组件，请重新安装当前版本") from exc
    return PtyProcess.spawn(
        _subprocess_command(command, ["auth", "login", "--provider", "opencode"]),
        cwd=tempfile.gettempdir(),
    )


def _auth_list_has_credentials(command: str) -> bool:
    try:
        result = subprocess.run(
            _subprocess_command(command, ["auth", "list"]),
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    output = _safe_auth_text("\n".join([result.stdout or "", result.stderr or ""]))
    lowered = output.lower()
    return result.returncode == 0 and bool(output) and not any(
        token in lowered for token in ("no credentials", "0 credentials", "not logged in")
    )


def _authentication_worker(job_id: str, command: str, process: Any) -> None:
    opened_url = ""
    exit_code: int | None = None
    try:
        while process.isalive():
            try:
                chunk = process.read()
            except EOFError:
                break
            with _auth_sessions_lock:
                session = _auth_sessions.get(job_id)
                credential = session.credential if session else ""
            safe_chunk = _safe_auth_text(chunk, credential)
            if not safe_chunk:
                continue

            urls = _AUTH_URL_RE.findall(safe_chunk)
            auth_url = urls[-1].rstrip(".,);]") if urls else ""
            changes: dict[str, Any] = {
                "auth_prompt": safe_chunk,
                "auth_status": "running",
                "phase": "authenticating",
                "message": "正在等待 OpenCode 完成官方登录",
            }
            if auth_url:
                changes.update({"auth_mode": "browser", "auth_url": auth_url})
                if auth_url != opened_url:
                    import webbrowser

                    webbrowser.open(auth_url)
                    opened_url = auth_url
            if re.search(r"(?i)press\s+enter.{0,40}(browser|login|continue)", safe_chunk):
                process.write("\r")
            elif _AUTH_CREDENTIAL_PROMPT_RE.search(safe_chunk):
                changes.update({
                    "status": "auth_required",
                    "phase": "credential_required",
                    "auth_mode": "credential",
                    "auth_status": "credential_required",
                    "message": "OpenCode 正在等待一次性验证码或令牌",
                    "next_action": "在司命中输入官方页面给出的验证码或令牌；内容不会保存或写入日志。",
                })
            _update_activation(job_id, **changes)

        try:
            exit_code = process.wait()
        except Exception:
            exit_code = getattr(process, "exitstatus", None)

        if exit_code in (None, 0) and _auth_list_has_credentials(command):
            _update_activation(
                job_id,
                status="auth_required",
                phase="auth_required",
                auth_status="completed",
                percent=92,
                message="官方登录已完成，正在重新验证免费模型",
                error=None,
                failure_kind=None,
                next_action=None,
            )
            retry_opencode_activation(job_id)
            return

        _update_activation(
            job_id,
            status="auth_required",
            phase="auth_required",
            auth_status="failed",
            message="官方登录没有完成",
            failure_kind="authentication_required",
            next_action="重新开始登录；如果浏览器没有打开，可复制登录地址到浏览器。",
        )
    except Exception as exc:
        _update_activation(
            job_id,
            status="auth_required",
            phase="auth_required",
            auth_status="failed",
            message="托管登录过程已中断",
            error=_safe_auth_text(exc),
            failure_kind="authentication_required",
            next_action="点击重新登录；司命不会保存本次输入的凭据。",
        )
    finally:
        with _auth_sessions_lock:
            _auth_sessions.pop(job_id, None)


def start_opencode_authentication(job_id: str) -> dict[str, Any]:
    job = get_opencode_activation_job(job_id)
    if not job:
        raise RuntimeError("OpenCode 激活任务不存在")
    command = resolve_opencode_command(job.get("command"))
    if not command:
        raise RuntimeError("没有找到可运行的 OpenCode，请先重新检测或安装")

    with _auth_sessions_lock:
        existing = _auth_sessions.get(job_id)
        if existing and existing.process.isalive():
            return get_opencode_activation_job(job_id) or job
        process = _spawn_auth_process(command)
        _auth_sessions[job_id] = _ManagedAuthSession(process=process)

    payload = _update_activation(
        job_id,
        status="running",
        phase="authenticating",
        auth_mode="browser",
        auth_status="running",
        auth_prompt=None,
        auth_url=None,
        message="正在启动 OpenCode 官方登录",
        error=None,
        next_action="浏览器打开后完成登录；司命会自动继续验证。",
    )
    threading.Thread(
        target=_authentication_worker,
        args=(job_id, command, process),
        daemon=True,
        name=f"opencode-auth-{job_id[:8]}",
    ).start()
    return payload


def submit_opencode_auth_credential(job_id: str, credential: str) -> dict[str, Any]:
    value = str(credential or "").strip()
    if not value:
        raise RuntimeError("请输入官方登录页面提供的验证码或令牌")
    with _auth_sessions_lock:
        session = _auth_sessions.get(job_id)
        if not session or not session.process.isalive():
            raise RuntimeError("这次登录会话已经结束，请重新开始登录")
        session.credential = value
        session.process.write(value + "\r")
    return _update_activation(
        job_id,
        status="running",
        phase="authenticating",
        auth_status="submitted",
        auth_prompt="一次性凭据已提交，正在等待 OpenCode 验证",
        message="正在验证官方登录",
        next_action="请稍候，司命会自动继续。",
    )


def _install_for_activation(job_id: str) -> tuple[str, str, str]:
    root = managed_opencode_root()
    _update_activation(
        job_id,
        status="running",
        phase="checking_release",
        percent=2,
        message="正在选择经过校验的 OpenCode 官方稳定版",
    )
    version, asset = _latest_release_asset()
    expected_sha256 = str(asset["digest"]).removeprefix("sha256:")
    official_url = str(asset.get("browser_download_url") or "")
    if not official_url:
        raise RuntimeError("OpenCode 官方安装包没有下载地址")
    archive_path = root / "downloads" / f"{asset['name']}.part"
    errors: list[str] = []
    download_started = time.monotonic()

    for source_url in _mirror_urls(official_url, str(asset["name"])):
        for attempt in range(2):
            try:
                _update_activation(
                    job_id,
                    status="running",
                    phase="downloading",
                    percent=5,
                    message=f"正在从 OpenCode 官方发行页下载 {version}",
                    download_url=source_url,
                    sha256=expected_sha256,
                )

                def on_progress(downloaded: int, total: int) -> None:
                    fraction = downloaded / total if total else 0
                    elapsed = max(0.1, time.monotonic() - download_started)
                    rate = downloaded / elapsed
                    remaining = int((total - downloaded) / rate) if total and rate > 0 else None
                    _update_activation(
                        job_id,
                        percent=max(5, min(78, int(5 + fraction * 73))),
                        bytes_downloaded=downloaded,
                        bytes_total=total,
                        estimated_seconds_remaining=remaining,
                    )

                _download_asset_resumable(
                    source_url,
                    archive_path,
                    expected_sha256=expected_sha256,
                    progress=on_progress,
                )
                command = managed_opencode_command()
                _update_activation(job_id, phase="verifying", percent=82, message="下载完成，正在校验并安装")
                _extract_opencode(archive_path, command)
                archive_path.unlink(missing_ok=True)
                metadata = {
                    "version": version,
                    "asset": asset["name"],
                    "sha256": expected_sha256,
                    "source": source_url,
                    "command": str(command),
                    "installed_at": datetime.now(UTC).isoformat(),
                }
                (root / "install.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                return str(command), version, expected_sha256
            except Exception as exc:
                errors.append(f"{source_url} 第 {attempt + 1} 次：{exc}")
    raise RuntimeError("所有下载线路均未完成。" + "；".join(errors[-3:]))


def _activation_worker(job_id: str) -> None:
    try:
        current = get_opencode_activation_job(job_id)
        if not current:
            return
        _update_activation(
            job_id,
            status="running",
            phase="checking",
            percent=1,
            message="正在检查这台电脑",
            error=None,
            failure_kind=None,
            next_action=None,
        )
        command = resolve_opencode_command(current.get("command"))
        version = None
        sha256 = None
        inspected = inspect_opencode(command, refresh=True) if command else {"installed": False}
        if not inspected.get("installed"):
            command, version, sha256 = _install_for_activation(job_id)

        _update_activation(job_id, phase="verifying", percent=84, message="正在确认写作引擎可以运行")
        inspected = inspect_opencode(command, timeout=15, refresh=True)
        if not inspected.get("installed"):
            raise RuntimeError("写作引擎已经下载，但被系统或安全软件阻止运行")
        command = str(inspected["command"])
        version = str(inspected.get("version") or version or "")
        free_models = list(inspected.get("free_models") or [])
        if not free_models:
            raise RuntimeError("当前没有发现可免费使用的模型，请稍后重新检测")

        preferred = str(current.get("preferred_model") or "")
        ordered = sorted(
            free_models,
            key=lambda item: (
                str(item.get("id")) != preferred if preferred else not bool(item.get("recommended")),
                not bool(item.get("recommended")),
            ),
        )
        _update_activation(
            job_id,
            phase="discovering_models",
            percent=88,
            message="已找到当前可免费使用的模型",
            command=command,
            version=version,
            sha256=sha256 or current.get("sha256"),
            free_models_json=[{**item, "test_status": "untested"} for item in ordered],
        )

        probe = _probe_free_models(
            job_id=job_id,
            command=command,
            ordered=ordered,
            update_activation=_update_activation,
            test_model_call=_test_opencode_model,
        )
        failures = probe.failures
        model_results = probe.model_results
        if probe.selected_model:
            _save_activated_config(command, probe.selected_model)
            try:
                from app.services.external_agent.mcp_auto_config import (
                    auto_configure_mcp_for_provider,
                )
                auto_configure_mcp_for_provider("opencode_cli", cli_command=command)
            except Exception:
                pass
            _update_activation(
                job_id,
                status="ready",
                phase="ready",
                percent=100,
                message="免费写作能力已经准备好",
                selected_model=probe.selected_model,
                free_models_json=deepcopy(model_results),
                completed_at=datetime.now(UTC).replace(tzinfo=None),
            )
            return

        authentication_failure = next((item for item in failures if item[1] == "authentication_required"), None)
        if authentication_failure:
            _save_activation_readiness_failure(authentication_failure[2])
            _update_activation(
                job_id,
                status="auth_required",
                phase="auth_required",
                percent=90,
                message="需要完成一次免费的官方登录",
                error=authentication_failure[2],
                failure_kind=authentication_failure[1],
                next_action="点击登录按钮，在官方页面完成登录后返回重试。",
            )
            return

        kinds = {item[1] for item in failures}
        if kinds == {"quota_or_rate_limit"}:
            _save_activation_readiness_failure(failures[-1][2])
            _update_activation(
                job_id,
                status="failed",
                phase="failed",
                percent=98,
                message="OpenCode 免费服务已限流",
                error=failures[-1][2],
                failure_kind="quota_or_rate_limit",
                next_action=(
                    f"司命已实际测试 {len(failures)} 个免费模型，第三方均返回 403/429 或额度限制；"
                    "这不是网络故障。可以等待额度恢复后重新检测，或先完成 OpenCode 官方登录，"
                    "再验证个人免费额度。"
                ),
                free_models_json=deepcopy(model_results),
            )
            return
        raise RuntimeError(
            "当前免费模型暂时都不可用，请稍后重试。"
            + (f" 技术详情：{failures[-1][2]}" if failures else "")
        )
    except Exception as exc:
        message = str(exc)
        latest = get_opencode_activation_job(job_id) or {}
        failure_context = (
            "download"
            if latest.get("phase") in {"checking_release", "downloading"}
            else None
        )
        kind = _activation_failure_kind(message, context=failure_context)
        _save_activation_readiness_failure(message, unavailable_fallback=True)
        _update_activation(
            job_id,
            status="failed",
            phase="failed",
            message="免费写作能力暂时没有准备完成",
            error=message,
            failure_kind=kind,
            next_action=(
                "请确认 Windows 日期和时间正确，并完成 Windows 更新后重试。司命会使用系统受信任证书，且不会关闭 HTTPS 校验。"
                if kind == "certificate_verification"
                else (
                    "OpenCode 官方下载服务返回 403/429 限流；这不是本机断网。下载进度已保留，请稍后继续下载。"
                    if kind == "download_rate_limit"
                    else (
                        "请检查网络后点击重试，司命会从上次下载进度继续。"
                        if kind == "network"
                        else "点击重试；如果仍然失败，可导出诊断信息反馈给项目维护者。"
                    )
                )
            ),
        )


def start_opencode_activation(*, preferred_model: str | None = None) -> dict[str, Any]:
    from app.database.models import OpenCodeActivationJob
    from app.database.session import SessionLocal

    if os.name != "nt":
        raise RuntimeError("当前自动安装仅支持 Windows")
    with _activation_start_lock, SessionLocal() as db:
        active = (
            db.query(OpenCodeActivationJob)
            .filter(OpenCodeActivationJob.status.in_(["pending", "running", "auth_required"]))
            .order_by(OpenCodeActivationJob.created_at.desc())
            .first()
        )
        if active:
            return _activation_payload(active)
        job = OpenCodeActivationJob(
            status="pending",
            phase="checking",
            percent=0,
            message="免费体验任务已创建",
            preferred_model=preferred_model,
        )
        db.add(job)
        db.flush()
        from app.services.operation_runtime import ensure_operation

        operation = ensure_operation(
            db,
            source_kind="opencode_activation",
            source_id=job.id,
            title="准备免费写作 AI",
            status="queued",
            phase="checking",
            message="正在检查这台电脑",
            tool_mode="managed_opencode",
            resume_url="/getting-started",
            can_pause=False,
            can_cancel=False,
            can_retry=False,
            progress_mode="indeterminate",
        )
        job.operation_id = operation.id
        commit_session(db)
        db.refresh(job)
        payload = _activation_payload(job)
        job_id = job.id
    threading.Thread(
        target=_activation_worker,
        args=(job_id,),
        daemon=True,
        name=f"opencode-activate-{job_id[:8]}",
    ).start()
    return payload


def retry_opencode_activation(job_id: str) -> dict[str, Any]:
    from app.database.models import OpenCodeActivationJob
    from app.database.session import SessionLocal

    with _activation_start_lock, SessionLocal() as db:
        job = db.query(OpenCodeActivationJob).filter(OpenCodeActivationJob.id == job_id).first()
        if not job:
            raise RuntimeError("OpenCode 激活任务不存在")
        if job.status in {"pending", "running"}:
            return _activation_payload(job)
        job.status = "pending"
        job.phase = "checking"
        job.percent = 0
        job.error = None
        job.failure_kind = None
        job.next_action = None
        job.auth_mode = None
        job.auth_status = None
        job.auth_prompt = None
        job.auth_url = None
        job.attempt_count = (job.attempt_count or 0) + 1
        commit_session(db)
        payload = _activation_payload(job)
    threading.Thread(
        target=_activation_worker,
        args=(job_id,),
        daemon=True,
        name=f"opencode-retry-{job_id[:8]}",
    ).start()
    return payload


def open_opencode_authentication(job_id: str) -> dict[str, Any]:
    return start_opencode_authentication(job_id)


def resume_incomplete_opencode_activations() -> int:
    from app.database.models import OpenCodeActivationJob
    from app.database.session import SessionLocal

    with SessionLocal() as db:
        jobs = db.query(OpenCodeActivationJob).filter(
            OpenCodeActivationJob.status.in_(["pending", "running"])
        ).all()
        job_ids: list[str] = []
        for job in jobs:
            if job.phase in {"authenticating", "credential_required"} or job.auth_status in {
                "running", "submitted", "credential_required"
            }:
                job.status = "auth_required"
                job.phase = "auth_required"
                job.auth_status = "interrupted"
                job.message = "应用重新启动，请重新开始官方登录"
                job.next_action = "点击登录按钮重新开始；上次输入没有保存。"
            else:
                job.status = "pending"
                job.message = "应用重新启动，正在恢复免费体验任务"
                job_ids.append(job.id)
        commit_session(db)
    for job_id in job_ids:
        threading.Thread(
            target=_activation_worker,
            args=(job_id,),
            daemon=True,
            name=f"opencode-resume-{job_id[:8]}",
        ).start()
    return len(job_ids)
