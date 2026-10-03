"""Background model/runtime installation jobs."""
from __future__ import annotations

from app.architecture.uow import commit_session

import threading
import hashlib
from urllib.parse import urlparse
from datetime import datetime
from pathlib import Path

from ...core.crypto import encrypt
from ...database.models import APIConfig, LocalModel, LocalRuntimeInstallation, ModelDownloadTask, ModelTaskSetting, OperationRun
from ...database.session import SessionLocal
from ..operation_runtime import ensure_operation, update_operation
from .downloads import DownloadCancelled, download_with_fallback
from .hardware import detect_hardware
from .manifest import model_spec
from .paths import model_root, runtime_root
from .runtime_installer import install_llama_cpp


_THREADS: dict[str, threading.Thread] = {}
_LOCK = threading.Lock()


def ensure_catalog_rows() -> None:
    from .manifest import model_catalog

    with SessionLocal() as db:
        context_by_model: dict[str, int] = {}
        for item in model_catalog():
            context_by_model[item["model_key"]] = int(item["context_length"])
            row = db.query(LocalModel).filter(LocalModel.model_key == item["model_key"]).first()
            if not row:
                row = LocalModel(
                    model_key=item["model_key"],
                    display_name=item["display_name"],
                    family=item["family"],
                    parameter_size=item["parameter_size"],
                    quantization=item["quantization"],
                    context_length=item["context_length"],
                    license_name=item["license_name"],
                    source="catalog",
                    source_urls=item["sources"],
                    min_ram_gb=item["min_ram_gb"],
                    recommended_vram_gb=item["recommended_vram_gb"],
                    status="available",
                )
                db.add(row)
            else:
                row.display_name = item["display_name"]
                row.family = item["family"]
                row.parameter_size = item["parameter_size"]
                row.quantization = item["quantization"]
                row.context_length = item["context_length"]
                row.license_name = item["license_name"]
                row.source = "catalog"
                row.source_urls = item["sources"]
                row.min_ram_gb = item["min_ram_gb"]
                row.recommended_vram_gb = item["recommended_vram_gb"]
                if row.status == "installed" and (not row.file_path or not Path(row.file_path).exists()):
                    row.status = "available"
                    row.file_path = None
        runtime = db.query(LocalRuntimeInstallation).filter(
            LocalRuntimeInstallation.runtime_key == "llama_cpp"
        ).first()
        if not runtime:
            db.add(LocalRuntimeInstallation(runtime_key="llama_cpp"))
        elif runtime.status in {"running", "starting"}:
            runtime.status = "stopped"
            runtime.port = None
            runtime.pid = None
            runtime.active_model_id = None
        recommended_context = detect_hardware().recommended_context
        for setting in db.query(ModelTaskSetting).filter(
            ModelTaskSetting.provider == "local_llama_cpp"
        ).all():
            model_context = context_by_model.get(setting.model_name)
            if model_context and not setting.context_length:
                # The catalog advertises capacity, not a safe launch default.
                # Never silently expand a user's task context during startup.
                setting.context_length = min(model_context, recommended_context)
        commit_session(db)


def create_model_download(model_key: str) -> str:
    spec = model_spec(model_key)
    if not spec:
        raise ValueError(f"未知模型: {model_key}")
    destination = model_root() / model_key / spec["file_name"]
    with SessionLocal() as db:
        existing = db.query(LocalModel).filter(LocalModel.model_key == model_key).first()
        if existing and existing.status == "installed" and existing.file_path and Path(existing.file_path).exists():
            return ""
        active_task = db.query(ModelDownloadTask).filter(
            ModelDownloadTask.kind == "model",
            ModelDownloadTask.target_key == model_key,
            ModelDownloadTask.status.in_(["queued", "downloading"]),
        ).first()
        if active_task:
            return active_task.id
        task = ModelDownloadTask(
            kind="model",
            target_key=model_key,
            source_url=spec["sources"][0],
            destination_path=str(destination),
            status="queued",
            sha256=spec.get("sha256"),
        )
        db.add(task)
        db.flush()
        operation = ensure_operation(
            db,
            source_kind="download",
            source_id=task.id,
            title=f"下载本机 AI 模型 · {model_key}",
            status="queued",
            phase="queued",
            message="模型下载已排队",
            tool_mode="resumable_download",
            resume_url="/models",
            can_pause=False,
            can_cancel=False,
            can_retry=False,
            progress_mode="indeterminate",
        )
        task.operation_id = operation.id
        commit_session(db)
        task_id = task.id
    _start_thread(task_id, _run_model_download, task_id, model_key)
    return task_id


def create_custom_model_download(
    *,
    model_key: str,
    display_name: str,
    source_url: str,
    context_length: int,
) -> str:
    """Register and download a user-selected GGUF without curating its URL.

    The user owns the source choice; this deliberately supplements rather than
    mutates the signed/built-in catalog.
    """
    if Path(urlparse(source_url).path).suffix.lower() != ".gguf":
        raise ValueError("下载地址必须直接指向 .gguf 模型文件")
    destination = model_root() / model_key / Path(urlparse(source_url).path).name
    with SessionLocal() as db:
        existing = db.query(LocalModel).filter(LocalModel.model_key == model_key).first()
        if existing and existing.source == "catalog":
            raise ValueError("模型标识与内置目录冲突，请使用不同的模型标识")
        if existing and existing.status == "installed" and existing.file_path and Path(existing.file_path).exists():
            return ""
        active_task = db.query(ModelDownloadTask).filter(
            ModelDownloadTask.kind == "model",
            ModelDownloadTask.target_key == model_key,
            ModelDownloadTask.status.in_(["queued", "downloading"]),
        ).first()
        if active_task:
            return active_task.id
        if not existing:
            existing = LocalModel(model_key=model_key)
            db.add(existing)
        existing.display_name = display_name
        existing.family = "custom"
        existing.parameter_size = "自定义"
        existing.quantization = "GGUF"
        existing.context_length = context_length
        existing.license_name = "由用户确认"
        existing.source = "custom"
        existing.source_urls = [source_url]
        existing.min_ram_gb = None
        existing.recommended_vram_gb = None
        existing.status = "available"
        task = ModelDownloadTask(
            kind="model",
            target_key=model_key,
            source_url=source_url,
            destination_path=str(destination),
            status="queued",
        )
        db.add(task)
        db.flush()
        operation = ensure_operation(
            db,
            source_kind="download",
            source_id=task.id,
            title=f"下载自有 GGUF 模型 · {model_key}",
            status="queued",
            phase="queued",
            message="模型下载已排队",
            tool_mode="resumable_download",
            resume_url="/models",
            can_pause=False,
            can_cancel=False,
            can_retry=False,
            progress_mode="indeterminate",
        )
        task.operation_id = operation.id
        commit_session(db)
        task_id = task.id
    _start_thread(task_id, _run_model_download, task_id, model_key)
    return task_id


def import_custom_model(
    *,
    model_key: str,
    display_name: str,
    file_path: str,
    context_length: int,
) -> None:
    """Register an existing local GGUF in place; it is never copied or moved."""
    path = Path(file_path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".gguf":
        raise ValueError("请选择存在的 .gguf 模型文件")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    with SessionLocal() as db:
        existing = db.query(LocalModel).filter(LocalModel.model_key == model_key).first()
        if existing and existing.source == "catalog":
            raise ValueError("模型标识与内置目录冲突，请使用不同的模型标识")
        if not existing:
            existing = LocalModel(model_key=model_key)
            db.add(existing)
        existing.display_name = display_name
        existing.family = "custom"
        existing.parameter_size = "自定义"
        existing.quantization = "GGUF"
        existing.context_length = context_length
        existing.file_path = str(path)
        existing.file_size = path.stat().st_size
        existing.sha256 = digest.hexdigest()
        existing.license_name = "由用户确认"
        existing.source = "custom"
        existing.source_urls = []
        existing.status = "installed"
        existing.installed_at = datetime.utcnow()
        commit_session(db)


def register_catalog_model_file(model_key: str, file_path: str) -> None:
    """Use an existing curated GGUF in place after checking its identity."""
    spec = model_spec(model_key)
    if not spec:
        raise ValueError("模型不在当前内置目录中")
    path = Path(file_path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".gguf":
        raise ValueError("请选择存在的 .gguf 模型文件")
    cancel_active_downloads("model", model_key)
    with path.open("rb") as handle:
        if handle.read(4) != b"GGUF":
            raise ValueError("文件不是有效的 GGUF 模型")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    actual_sha = digest.hexdigest()
    if spec.get("sha256") and actual_sha.lower() != spec["sha256"].lower():
        raise ValueError("文件 SHA256 与该内置模型不符，请核对版本或作为自有 GGUF 登记")
    ensure_catalog_rows()
    with SessionLocal() as db:
        row = db.query(LocalModel).filter(LocalModel.model_key == model_key).first()
        if not row or row.source != "catalog":
            raise ValueError("内置模型不存在")
        row.file_path = str(path)
        row.file_size = path.stat().st_size
        row.sha256 = actual_sha
        row.status = "installed"
        row.installed_at = datetime.utcnow()
        commit_session(db)


def create_runtime_download() -> str:
    with SessionLocal() as db:
        runtime = db.query(LocalRuntimeInstallation).filter(
            LocalRuntimeInstallation.runtime_key == "llama_cpp"
        ).first()
        if runtime and runtime.executable_path and Path(runtime.executable_path).exists():
            return ""
        active_task = db.query(ModelDownloadTask).filter(
            ModelDownloadTask.kind == "runtime",
            ModelDownloadTask.target_key == "llama_cpp",
            ModelDownloadTask.status.in_(["queued", "downloading"]),
        ).first()
        if active_task:
            return active_task.id
        task = ModelDownloadTask(
            kind="runtime",
            target_key="llama_cpp",
            destination_path=str(runtime_root() / "llama_cpp"),
            status="queued",
        )
        db.add(task)
        db.flush()
        operation = ensure_operation(
            db,
            source_kind="download",
            source_id=task.id,
            title="下载本机 AI 运行环境",
            status="queued",
            phase="queued",
            message="运行环境下载已排队",
            tool_mode="resumable_download",
            resume_url="/models",
            can_pause=False,
            can_cancel=False,
            can_retry=False,
            progress_mode="indeterminate",
        )
        task.operation_id = operation.id
        commit_session(db)
        task_id = task.id
    _start_thread(task_id, _run_runtime_download, task_id)
    return task_id


def resume_incomplete_downloads() -> None:
    ensure_catalog_rows()
    with SessionLocal() as db:
        tasks = db.query(ModelDownloadTask).filter(
            ModelDownloadTask.status.in_(["queued", "downloading"])
        ).all()
        for task in tasks:
            if task.kind == "model" and not _model_download_allowed(db, task.target_key):
                task.status = "cancelled"
                task.error_message = None
                if task.operation_id:
                    operation = db.query(OperationRun).filter(OperationRun.id == task.operation_id).first()
                    if operation:
                        update_operation(
                            db, operation, status="cancelled", phase="cancelled",
                            message="模型已退出内置目录，下载已取消",
                            event_type="cancelled", output=False,
                        )
                        operation.can_retry = False
                continue
            if task.operation_id:
                continue
            operation = ensure_operation(
                db,
                source_kind="download",
                source_id=task.id,
                title=(f"下载本机 AI 模型 · {task.target_key}" if task.kind == "model" else "下载本机 AI 运行环境"),
                status="queued" if task.status == "queued" else "running",
                phase=task.status,
                message="正在恢复未完成的下载",
                tool_mode="resumable_download",
                resume_url="/models",
                can_pause=False,
                can_cancel=False,
                can_retry=False,
                progress_mode="determinate" if task.total_bytes else "indeterminate",
                progress_current=int(task.downloaded_bytes or 0),
                progress_total=int(task.total_bytes) if task.total_bytes else None,
            )
            task.operation_id = operation.id
        commit_session(db)
        pending = [(task.id, task.kind, task.target_key) for task in tasks if task.status != "cancelled"]
    for task_id, kind, target_key in pending:
        if kind == "runtime":
            _start_thread(task_id, _run_runtime_download, task_id)
        elif kind == "model":
            _start_thread(task_id, _run_model_download, task_id, target_key)


def resume_download(task_id: str) -> None:
    """Reconnect an interrupted download to its persisted partial file."""
    with SessionLocal() as db:
        task = db.query(ModelDownloadTask).filter(ModelDownloadTask.id == task_id).first()
        if not task:
            raise ValueError("下载任务不存在")
        if task.status != "failed":
            raise ValueError("只有失败的下载任务可以续传")
        if task.kind == "model" and not _model_download_allowed(db, task.target_key):
            raise ValueError("该内置模型已退出目录，不能继续下载；请移除旧记录")
        task.status = "queued"
        task.error_message = None
        task.updated_at = datetime.utcnow()
        if task.operation_id:
            operation = db.query(OperationRun).filter(OperationRun.id == task.operation_id).first()
            if operation:
                update_operation(
                    db,
                    operation,
                    status="queued",
                    health_status="active",
                    phase="queued",
                    message="正在从已保存进度继续下载",
                    progress_mode="determinate" if task.total_bytes else "indeterminate",
                    progress_current=int(task.downloaded_bytes or 0),
                    progress_total=int(task.total_bytes) if task.total_bytes else None,
                    output=True,
                )
        kind = task.kind
        target_key = task.target_key
        commit_session(db)
    if kind == "runtime":
        _start_thread(task_id, _run_runtime_download, task_id)
    elif kind == "model":
        _start_thread(task_id, _run_model_download, task_id, target_key)


def _model_download_allowed(db, model_key: str) -> bool:
    row = db.query(LocalModel).filter(LocalModel.model_key == model_key).first()
    return bool(model_spec(model_key) or (row and row.source == "custom"))


def cancel_download(task_id: str) -> None:
    """Cancel an active task or dismiss a failed one, preserving partial bytes."""
    with SessionLocal() as db:
        task = db.query(ModelDownloadTask).filter(ModelDownloadTask.id == task_id).first()
        if not task:
            raise ValueError("下载任务不存在")
        if task.status == "completed":
            raise ValueError("已完成的下载不能取消")
        if task.status == "cancelled":
            return
        task.status = "cancelled"
        task.error_message = None
        task.updated_at = datetime.utcnow()
        if task.operation_id:
            operation = db.query(OperationRun).filter(OperationRun.id == task.operation_id).first()
            if operation:
                update_operation(
                    db, operation, status="cancelled", phase="cancelled",
                    message="下载已取消", event_type="cancelled", output=False,
                )
                operation.can_retry = False
        commit_session(db)


def cancel_active_downloads(kind: str, target_key: str) -> None:
    with SessionLocal() as db:
        task_ids = [task.id for task in db.query(ModelDownloadTask).filter(
            ModelDownloadTask.kind == kind,
            ModelDownloadTask.target_key == target_key,
            ModelDownloadTask.status.in_(["queued", "downloading"]),
        ).all()]
    for task_id in task_ids:
        cancel_download(task_id)


def _download_cancelled(task_id: str) -> bool:
    with SessionLocal() as db:
        task = db.query(ModelDownloadTask).filter(ModelDownloadTask.id == task_id).first()
        return task is None or task.status == "cancelled"


def _start_thread(key: str, target, *args) -> None:
    with _LOCK:
        current = _THREADS.get(key)
        if current and current.is_alive():
            return
        thread = threading.Thread(target=target, args=args, daemon=True, name=f"siming-download-{key}")
        _THREADS[key] = thread
        thread.start()


def _set_task(task_id: str, **values) -> None:
    with SessionLocal() as db:
        task = db.query(ModelDownloadTask).filter(ModelDownloadTask.id == task_id).first()
        if not task:
            return
        if task.status == "cancelled" and values.get("status") != "cancelled":
            return
        for key, value in values.items():
            setattr(task, key, value)
        task.updated_at = datetime.utcnow()
        if task.operation_id:
            from ...database.models import OperationRun

            operation = db.query(OperationRun).filter(OperationRun.id == task.operation_id).first()
            if operation:
                lifecycle = {
                    "queued": "queued",
                    "downloading": "running",
                    "completed": "completed",
                    "failed": "failed",
                    "cancelled": "cancelled",
                }.get(task.status, "running")
                label = "本机 AI 模型" if task.kind == "model" else "本机 AI 运行环境"
                if lifecycle == "completed":
                    message = f"{label}下载完成"
                elif lifecycle == "failed":
                    message = task.error_message or f"{label}下载失败"
                elif lifecycle == "cancelled":
                    message = f"{label}下载已取消"
                else:
                    message = f"正在下载{label}"
                update_operation(
                    db,
                    operation,
                    status=lifecycle,
                    health_status="active",
                    phase=task.status,
                    message=message,
                    event_type=lifecycle if lifecycle in {"completed", "failed", "cancelled"} else None,
                    progress_mode="determinate" if task.total_bytes else "indeterminate",
                    progress_current=int(task.downloaded_bytes or 0),
                    progress_total=int(task.total_bytes) if task.total_bytes else None,
                    output=task.status == "downloading",
                    failure_class="download_error" if lifecycle == "failed" else None,
                    next_action="返回模型中心检查下载源和磁盘空间" if lifecycle == "failed" else None,
                )
        commit_session(db)


def _run_model_download(task_id: str, model_key: str) -> None:
    spec = model_spec(model_key)
    with SessionLocal() as db:
        task = db.query(ModelDownloadTask).filter(ModelDownloadTask.id == task_id).first()
        source_url = task.source_url if task else None
        destination_path = task.destination_path if task else None
    sources = spec["sources"] if spec else ([source_url] if source_url else [])
    if not sources or not destination_path:
        _set_task(task_id, status="failed", error_message="模型下载信息不完整")
        return
    destination = Path(destination_path)
    _set_task(task_id, status="downloading", error_message=None)
    try:
        if _download_cancelled(task_id):
            raise DownloadCancelled()
        path = download_with_fallback(
            task_id,
            sources,
            destination,
            expected_sha256=spec.get("sha256") if spec else None,
            should_cancel=lambda: _download_cancelled(task_id),
        )
        if _download_cancelled(task_id):
            raise DownloadCancelled()
        with SessionLocal() as db:
            model = db.query(LocalModel).filter(LocalModel.model_key == model_key).first()
            model.file_path = str(path)
            model.file_size = path.stat().st_size
            if spec and spec.get("sha256"):
                model.sha256 = spec["sha256"]
            else:
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                        if _download_cancelled(task_id):
                            raise DownloadCancelled()
                        digest.update(chunk)
                model.sha256 = digest.hexdigest()
            if _download_cancelled(task_id):
                raise DownloadCancelled()
            model.status = "installed"
            model.installed_at = datetime.utcnow()
            config = db.query(APIConfig).filter(APIConfig.provider == "local_llama_cpp").first()
            if not config:
                db.add(APIConfig(
                    provider="local_llama_cpp",
                    api_key_encrypted=encrypt("__local_runtime__"),
                    default_model=model_key,
                    provider_type="local_runtime",
                    max_output_tokens=16384,
                    is_global_default=db.query(APIConfig).count() == 0,
                ))
            commit_session(db)
        _set_task(
            task_id,
            status="completed",
            downloaded_bytes=path.stat().st_size,
            total_bytes=path.stat().st_size,
            completed_at=datetime.utcnow(),
        )
    except DownloadCancelled:
        _set_task(task_id, status="cancelled", error_message=None)
    except Exception as exc:
        _set_task(task_id, status="failed", error_message=str(exc))


def _run_runtime_download(task_id: str) -> None:
    _set_task(task_id, status="downloading", error_message=None)
    try:
        if _download_cancelled(task_id):
            raise DownloadCancelled()
        result = install_llama_cpp(task_id, detect_hardware(), should_cancel=lambda: _download_cancelled(task_id))
        if _download_cancelled(task_id):
            raise DownloadCancelled()
        with SessionLocal() as db:
            runtime = db.query(LocalRuntimeInstallation).filter(
                LocalRuntimeInstallation.runtime_key == "llama_cpp"
            ).first()
            if not runtime:
                runtime = LocalRuntimeInstallation(runtime_key="llama_cpp")
                db.add(runtime)
            runtime.version = result["version"]
            runtime.backend = result["backend"]
            runtime.install_path = result["install_path"]
            runtime.executable_path = result["executable_path"]
            runtime.status = "stopped"
            runtime.last_error = None
            commit_session(db)
        _set_task(task_id, status="completed", completed_at=datetime.utcnow())
    except DownloadCancelled:
        _set_task(task_id, status="cancelled", error_message=None)
    except Exception as exc:
        _set_task(task_id, status="failed", error_message=str(exc))
