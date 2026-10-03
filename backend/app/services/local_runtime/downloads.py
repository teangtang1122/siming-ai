"""Resumable downloads with source fallback, progress persistence, and checksums."""
from __future__ import annotations

from app.architecture.uow import commit_session

import hashlib
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import httpx

from ...database.models import ModelDownloadTask, OperationRun
from ...database.session import SessionLocal
from ..operation_runtime import update_operation


ProgressCallback = Callable[[dict], None]
DOWNLOAD_ATTEMPTS_PER_SOURCE = 4
DOWNLOAD_RETRY_DELAYS_SECONDS = (1, 2, 4)


class DownloadCancelled(Exception):
    """The user stopped a download; never retry it as a network failure."""


def _sha256(path: Path, should_cancel: Callable[[], bool] | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            if should_cancel and should_cancel():
                raise DownloadCancelled("下载已取消")
            digest.update(chunk)
    return digest.hexdigest()


def _is_transient_download_error(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {408, 425, 429} or error.response.status_code >= 500
    return isinstance(
        error,
        (
            httpx.ConnectError,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            httpx.TimeoutException,
        ),
    )


def download_with_fallback(
    task_id: str,
    urls: list[str],
    destination: Path,
    *,
    expected_sha256: str | None = None,
    on_progress: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Path:
    def check_cancelled() -> None:
        if should_cancel and should_cancel():
            raise DownloadCancelled("下载已取消")

    check_cancelled()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    last_error: Exception | None = None

    # Completed downloads are moved into place atomically. Reuse that durable
    # result when a later step in a multi-archive install needs to be retried;
    # otherwise a CUDA dependency failure would download the main runtime again.
    if destination.is_file():
        if expected_sha256 and _sha256(destination, should_cancel).lower() != expected_sha256.lower():
            destination.unlink(missing_ok=True)
        else:
            size = destination.stat().st_size
            payload = {
                "downloaded_bytes": size,
                "total_bytes": size,
                "error_message": None,
            }
            _persist_progress(task_id, **payload)
            if on_progress:
                on_progress(payload)
            return destination

    for url in urls:
        source_completed = False
        for attempt in range(DOWNLOAD_ATTEMPTS_PER_SOURCE):
            try:
                check_cancelled()
                offset = partial.stat().st_size if partial.exists() else 0
                headers = {"Range": f"bytes={offset}-"} if offset else {}
                with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60) as response:
                    if response.status_code == 416 and partial.exists():
                        check_cancelled()
                        _persist_progress(task_id)
                        partial.replace(destination)
                        source_completed = True
                        break
                    response.raise_for_status()
                    if offset and response.status_code == 200:
                        partial.unlink(missing_ok=True)
                        offset = 0
                    content_length = int(response.headers.get("content-length") or 0)
                    total = offset + content_length if content_length else None
                    if total:
                        free = shutil.disk_usage(destination.parent).free
                        if free < max(512 * 1024 * 1024, total - offset):
                            raise OSError("磁盘空间不足，无法完成模型下载")
                    with partial.open("ab" if offset else "wb") as handle:
                        downloaded = offset
                        for chunk in response.iter_bytes(1024 * 1024):
                            check_cancelled()
                            handle.write(chunk)
                            downloaded += len(chunk)
                            payload = {
                                "downloaded_bytes": downloaded,
                                "total_bytes": total,
                                "source_url": url,
                                "error_message": None,
                            }
                            _persist_progress(task_id, **payload)
                            if on_progress:
                                on_progress(payload)
                check_cancelled()
                _persist_progress(task_id)
                partial.replace(destination)
                source_completed = True
                break
            except DownloadCancelled:
                raise
            except Exception as exc:
                last_error = exc
                can_retry = _is_transient_download_error(exc) and attempt < DOWNLOAD_ATTEMPTS_PER_SOURCE - 1
                if not can_retry:
                    _persist_progress(task_id, source_url=url, error_message=str(exc))
                    break
                delay = DOWNLOAD_RETRY_DELAYS_SECONDS[min(attempt, len(DOWNLOAD_RETRY_DELAYS_SECONDS) - 1)]
                _persist_progress(
                    task_id,
                    source_url=url,
                    error_message=(
                        f"网络连接中断，{delay} 秒后自动续传"
                        f"（{attempt + 1}/{DOWNLOAD_ATTEMPTS_PER_SOURCE - 1}）"
                    ),
                )
                time.sleep(delay)
        if source_completed:
            break
    else:
        raise RuntimeError(f"所有下载源均失败: {last_error}")

    if expected_sha256:
        check_cancelled()
        actual = _sha256(destination, should_cancel)
        if actual.lower() != expected_sha256.lower():
            destination.unlink(missing_ok=True)
            raise RuntimeError("下载文件 SHA256 校验失败")
    return destination


def _persist_progress(task_id: str, **values) -> None:
    with SessionLocal() as db:
        task = db.query(ModelDownloadTask).filter(ModelDownloadTask.id == task_id).first()
        if not task:
            return
        if task.status == "cancelled":
            raise DownloadCancelled("下载已取消")
        for key, value in values.items():
            if hasattr(task, key):
                setattr(task, key, value)
        task.updated_at = datetime.utcnow()
        if task.operation_id:
            operation = db.query(OperationRun).filter(OperationRun.id == task.operation_id).first()
            if operation:
                update_operation(
                    db,
                    operation,
                    status="running",
                    health_status="active",
                    phase="downloading",
                    message=f"正在下载 {task.target_key}",
                    progress_mode="determinate" if task.total_bytes else "indeterminate",
                    progress_current=int(task.downloaded_bytes or 0),
                    progress_total=int(task.total_bytes) if task.total_bytes else None,
                    output=True,
                )
        commit_session(db)
