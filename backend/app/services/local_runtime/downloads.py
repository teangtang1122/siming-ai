"""Resumable downloads with source fallback, progress persistence, and checksums."""
from __future__ import annotations

from app.architecture.uow import commit_session

import hashlib
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

import httpx

from ...database.models import ModelDownloadTask, OperationRun
from ...database.session import SessionLocal
from ..operation_runtime import update_operation


ProgressCallback = Callable[[dict], None]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_with_fallback(
    task_id: str,
    urls: list[str],
    destination: Path,
    *,
    expected_sha256: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    last_error: Exception | None = None

    for url in urls:
        try:
            offset = partial.stat().st_size if partial.exists() else 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60) as response:
                if response.status_code == 416 and partial.exists():
                    partial.replace(destination)
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
                        handle.write(chunk)
                        downloaded += len(chunk)
                        payload = {"downloaded_bytes": downloaded, "total_bytes": total, "source_url": url}
                        _persist_progress(task_id, **payload)
                        if on_progress:
                            on_progress(payload)
            partial.replace(destination)
            break
        except Exception as exc:
            last_error = exc
            _persist_progress(task_id, source_url=url, error_message=str(exc))
    else:
        raise RuntimeError(f"所有下载源均失败: {last_error}")

    if expected_sha256:
        actual = _sha256(destination)
        if actual.lower() != expected_sha256.lower():
            destination.unlink(missing_ok=True)
            raise RuntimeError("下载文件 SHA256 校验失败")
    return destination


def _persist_progress(task_id: str, **values) -> None:
    with SessionLocal() as db:
        task = db.query(ModelDownloadTask).filter(ModelDownloadTask.id == task_id).first()
        if not task:
            return
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
