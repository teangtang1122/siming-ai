"""Background model/runtime installation jobs."""
from __future__ import annotations

import threading
import hashlib
from datetime import datetime
from pathlib import Path

from ...core.crypto import encrypt
from ...database.models import APIConfig, LocalModel, LocalModelTaskSetting, LocalRuntimeInstallation, ModelDownloadTask
from ...database.session import SessionLocal
from .downloads import download_with_fallback
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
        for setting in db.query(LocalModelTaskSetting).all():
            model_context = context_by_model.get(setting.model_key)
            if model_context and (not setting.context_length or setting.context_length < model_context):
                setting.context_length = model_context
        db.commit()


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
        db.commit()
        task_id = task.id
    _start_thread(task_id, _run_model_download, task_id, model_key)
    return task_id


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
        db.commit()
        task_id = task.id
    _start_thread(task_id, _run_runtime_download, task_id)
    return task_id


def resume_incomplete_downloads() -> None:
    ensure_catalog_rows()
    with SessionLocal() as db:
        tasks = db.query(ModelDownloadTask).filter(
            ModelDownloadTask.status.in_(["queued", "downloading"])
        ).all()
        pending = [(task.id, task.kind, task.target_key) for task in tasks]
    for task_id, kind, target_key in pending:
        if kind == "runtime":
            _start_thread(task_id, _run_runtime_download, task_id)
        elif kind == "model":
            _start_thread(task_id, _run_model_download, task_id, target_key)


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
        for key, value in values.items():
            setattr(task, key, value)
        task.updated_at = datetime.utcnow()
        db.commit()


def _run_model_download(task_id: str, model_key: str) -> None:
    spec = model_spec(model_key)
    if not spec:
        _set_task(task_id, status="failed", error_message="模型清单中不存在该模型")
        return
    destination = model_root() / model_key / spec["file_name"]
    _set_task(task_id, status="downloading", error_message=None)
    try:
        path = download_with_fallback(
            task_id,
            spec["sources"],
            destination,
            expected_sha256=spec.get("sha256"),
        )
        with SessionLocal() as db:
            model = db.query(LocalModel).filter(LocalModel.model_key == model_key).first()
            model.file_path = str(path)
            model.file_size = path.stat().st_size
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                    digest.update(chunk)
            model.sha256 = digest.hexdigest()
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
            db.commit()
        _set_task(
            task_id,
            status="completed",
            downloaded_bytes=path.stat().st_size,
            total_bytes=path.stat().st_size,
            completed_at=datetime.utcnow(),
        )
    except Exception as exc:
        _set_task(task_id, status="failed", error_message=str(exc))


def _run_runtime_download(task_id: str) -> None:
    _set_task(task_id, status="downloading", error_message=None)
    try:
        result = install_llama_cpp(task_id, detect_hardware())
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
            db.commit()
        _set_task(task_id, status="completed", completed_at=datetime.utcnow())
    except Exception as exc:
        _set_task(task_id, status="failed", error_message=str(exc))
