"""Local model center, managed runtime, and LoRA training beta APIs."""
from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.architecture.uow import commit_session

from ..ai.local_runtime_policy import local_runtime_disabled, local_runtime_disabled_message
from ..core.crypto import encrypt
from ..core.exceptions import ValidationError
from ..core.legacy_env import get_compatible_env, set_compatible_env
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.model_runtime.application.execution import model_executor as LLMGateway
from ..modules.model_runtime.infrastructure.readiness import mark_model_ready
from ..modules.model_runtime.interfaces.config_dependencies import model_config_crud
from ..modules.model_runtime.interfaces.local_model_dependencies import local_model_store
from ..schemas.local_model import (
    AdapterCompareRequest,
    AdapterUpdateRequest,
    BenchmarkRequest,
    CustomModelDownloadRequest,
    CustomModelImportRequest,
    DatasetCreateRequest,
    ModelInstallRequest,
    ModelRootUpdateRequest,
    CatalogModelFileRequest,
    QualificationRequest,
    RuntimeLaunchSettings,
    RuntimeExecutableRequest,
    RuntimeStartRequest,
    RuntimeUsageRequest,
    TrainingJobCreateRequest,
)
from ..services.local_runtime import get_runtime_manager
from ..services.local_runtime.datasets import build_training_dataset
from ..services.local_runtime.hardware import detect_hardware
from ..services.local_runtime.launch_settings import (
    load_launch_settings, reset_launch_settings, save_launch_settings,
)
from ..services.local_runtime.manifest import model_catalog
from ..services.local_runtime.model_jobs import (
    create_custom_model_download,
    create_model_download,
    create_runtime_download,
    cancel_download,
    cancel_active_downloads,
    ensure_catalog_rows,
    import_custom_model,
    register_catalog_model_file,
    resume_download,
)
from ..services.local_runtime.paths import model_root
from ..services.local_runtime.runtime_files import register_runtime_file
from ..services.application_settings import load_launcher_settings, save_launcher_settings
from ..services.local_runtime.qualification import qualify_local_model
from ..services.local_runtime.training import (
    control_training_job,
    create_training_job,
)

router = APIRouter(prefix="/local-models", tags=["local-models"])
_ADAPTER_COMPARISONS: dict[str, dict] = {}


def _ensure_local_runtime_usage_enabled() -> None:
    if local_runtime_disabled("local_llama_cpp"):
        raise ValidationError(local_runtime_disabled_message())


def _launcher_settings_path() -> Path:
    home = get_compatible_env("SIMING_HOME")
    if home:
        return Path(home) / "launcher-settings.json"
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Siming" / "launcher-settings.json"


def _pick_local_path(*, directory: bool, executable: bool = False) -> Path | None:
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = (
            filedialog.askdirectory(title="选择本地模型存储文件夹", parent=root)
            if directory
            else filedialog.askopenfilename(
                title="选择 llama-server.exe" if executable else "选择本地 GGUF 模型",
                filetypes=([("llama.cpp 服务程序", "*.exe"), ("所有文件", "*.*")]
                           if executable else [("GGUF 模型", "*.gguf"), ("所有文件", "*.*")]),
                parent=root,
            )
        )
        root.destroy()
        return Path(selected).expanduser().resolve() if selected else None
    except Exception as exc:
        raise ValidationError(f"无法打开本机选择器：{exc}")


def _model_payload(model: Any) -> dict:
    return {
        "id": model.id,
        "model_key": model.model_key,
        "display_name": model.display_name,
        "family": model.family,
        "parameter_size": model.parameter_size,
        "quantization": model.quantization,
        "context_length": model.context_length,
        "file_path": model.file_path,
        "file_size": model.file_size,
        "sha256": model.sha256,
        "license_name": model.license_name,
        "source": model.source,
        "source_urls": model.source_urls or [],
        "min_ram_gb": model.min_ram_gb,
        "recommended_vram_gb": model.recommended_vram_gb,
        "status": model.status,
        "installed_at": model.installed_at.isoformat() if model.installed_at else None,
    }


def _task_payload(task: Any) -> dict:
    return {
        "id": task.id,
        "operation_id": task.operation_id,
        "kind": task.kind,
        "target_key": task.target_key,
        "source_url": task.source_url,
        "destination_path": task.destination_path,
        "status": task.status,
        "downloaded_bytes": task.downloaded_bytes or 0,
        "total_bytes": task.total_bytes,
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


def _managed_model_file(model: Any, root: Path | None = None) -> bool:
    if not model.file_path:
        return False
    base = (root or model_root()) / model.model_key
    return Path(model.file_path).resolve().is_relative_to(base.resolve())


def _launch_settings_for(model_key: str, db: Session) -> tuple[RuntimeLaunchSettings, bool]:
    model = local_model_store(db).model(model_key)
    if not model:
        raise ValidationError("模型不存在")
    hardware = detect_hardware()
    return load_launch_settings(
        model_key,
        context_length=min(int(model.context_length or hardware.recommended_context), hardware.recommended_context),
        gpu_layers=99 if hardware.nvidia_available else 0,
        threads=max(2, hardware.cpu_count - 1),
    )


@router.get("/hardware")
def hardware_profile():
    return ApiResponse.success(data=detect_hardware().to_dict())


@router.get("/catalog")
def catalog(db: Session = Depends(get_db)):
    ensure_catalog_rows()
    store = local_model_store(db)
    manifest = model_catalog()
    catalog_keys = {item["model_key"] for item in manifest}
    rows = [
        row for row in store.catalog_models()
        if row.source != "catalog" or row.model_key in catalog_keys
    ]
    runtime = store.runtime_installation("llama_cpp")
    usage_enabled = not local_runtime_disabled("local_llama_cpp")
    runtime_state = get_runtime_manager().status()
    observed_runtime_status = runtime.status if runtime else "not_installed"
    if runtime_state["running"]:
        observed_runtime_status = "running"
    return ApiResponse.success(data={
        "usage_enabled": usage_enabled,
        "usage_disabled_reason": None if usage_enabled else local_runtime_disabled_message(),
        "items": [_model_payload(row) for row in rows],
        "manifest": manifest,
        "runtime": {
            # Live health is authoritative. Persisted startup state can lag if
            # the initiating browser request is cancelled after launch.
            "status": observed_runtime_status,
            "version": runtime.version if runtime else None,
            "backend": runtime.backend if runtime else None,
            "executable_path": runtime.executable_path if runtime else None,
            **runtime_state,
        },
        "model_root": str(model_root()),
    })


@router.put("/root")
def update_model_root(payload: ModelRootUpdateRequest, db: Session = Depends(get_db)):
    target = Path(payload.path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    current = model_root()
    if target != current:
        for model in local_model_store(db).installed_models():
            if not _managed_model_file(model, current):
                continue
            source = Path(model.file_path)
            if not source.exists():
                continue
            destination_dir = target / model.model_key
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / source.name
            shutil.move(str(source), str(destination))
            model.file_path = str(destination)
        settings_path = _launcher_settings_path()
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
        except Exception:
            settings = {}
        settings["model_root"] = str(target)
        settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        set_compatible_env("SIMING_MODEL_ROOT", str(target))
        commit_session(db)
    return ApiResponse.success(data={"model_root": str(target)}, message="模型目录已更新")


@router.post("/root/pick")
def pick_model_root():
    selected = _pick_local_path(directory=True)
    return ApiResponse.success(data={"path": str(selected) if selected else None, "cancelled": selected is None})


@router.post("/custom/pick")
def pick_custom_model_file():
    selected = _pick_local_path(directory=False)
    if selected and selected.suffix.lower() != ".gguf":
        raise ValidationError("请选择 .gguf 模型文件")
    return ApiResponse.success(data={"path": str(selected) if selected else None, "cancelled": selected is None})


@router.post("/runtime/pick")
def pick_runtime_executable():
    selected = _pick_local_path(directory=False, executable=True)
    if selected and selected.name.lower() != "llama-server.exe":
        raise ValidationError("请选择 llama-server.exe")
    return ApiResponse.success(data={"path": str(selected) if selected else None, "cancelled": selected is None})


@router.post("/runtime/register-file")
def register_runtime_executable(payload: RuntimeExecutableRequest, db: Session = Depends(get_db)):
    register_runtime_file(db, payload.file_path)
    return ApiResponse.success(message="已登记本机 llama.cpp，原文件不会复制或移动")


@router.post("/runtime/install")
def install_runtime():
    task_id = create_runtime_download()
    return ApiResponse.success(data={"task_id": task_id, "already_installed": not bool(task_id)})


@router.post("/install")
def install_model(payload: ModelInstallRequest):
    runtime_task_id = create_runtime_download()
    model_task_id = create_model_download(payload.model_key)
    return ApiResponse.success(data={
        "runtime_task_id": runtime_task_id,
        "model_task_id": model_task_id,
        "already_installed": not bool(model_task_id),
    })


@router.post("/custom/download")
def download_custom_model(payload: CustomModelDownloadRequest):
    task_id = create_custom_model_download(**payload.model_dump())
    return ApiResponse.success(data={"model_task_id": task_id, "already_installed": not bool(task_id)})


@router.post("/custom/import")
def import_existing_custom_model(payload: CustomModelImportRequest):
    import_custom_model(**payload.model_dump())
    return ApiResponse.success(message="自有 GGUF 模型已登记")


@router.post("/catalog/{model_key}/register-file")
def register_existing_catalog_model(model_key: str, payload: CatalogModelFileRequest):
    register_catalog_model_file(model_key, payload.file_path)
    return ApiResponse.success(message="已登记本机模型文件；原文件不会复制或移动")


@router.get("/downloads")
def downloads(db: Session = Depends(get_db)):
    tasks = local_model_store(db).download_tasks(limit=100)
    return ApiResponse.success(data={"items": [_task_payload(task) for task in tasks]})


@router.get("/downloads/{task_id}/events")
async def download_events(task_id: str):
    async def stream():
        last_payload = None
        while True:
            from ..database.session import SessionLocal

            with SessionLocal() as db:
                task = local_model_store(db).download_task(task_id)
                if not task:
                    yield f"data: {json.dumps({'status': 'missing'}, ensure_ascii=False)}\n\n"
                    return
                payload = _task_payload(task)
            encoded = json.dumps(payload, ensure_ascii=False)
            if encoded != last_payload:
                yield f"data: {encoded}\n\n"
                last_payload = encoded
            if payload["status"] in {"completed", "failed", "cancelled"}:
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/downloads/{task_id}/resume")
def resume_model_download(task_id: str):
    resume_download(task_id)
    return ApiResponse.success(message="已从保存的下载进度继续")


@router.post("/downloads/{task_id}/cancel")
def cancel_model_download(task_id: str):
    cancel_download(task_id)
    return ApiResponse.success(message="下载已取消，已下载的临时文件保留")


@router.put("/runtime/usage")
def set_runtime_usage(payload: RuntimeUsageRequest):
    settings = load_launcher_settings()
    settings["local_runtime_enabled"] = payload.enabled
    save_launcher_settings(settings)
    if not payload.enabled:
        get_runtime_manager().stop()
    return ApiResponse.success(data={"usage_enabled": payload.enabled, "runtime": get_runtime_manager().status()})


@router.get("/runtime/settings/{model_key}")
def get_runtime_settings(model_key: str, db: Session = Depends(get_db)):
    profile, saved = _launch_settings_for(model_key, db)
    return ApiResponse.success(data={"profile": profile.model_dump(), "saved": saved})


@router.put("/runtime/settings/{model_key}")
def put_runtime_settings(model_key: str, payload: RuntimeLaunchSettings, db: Session = Depends(get_db)):
    _launch_settings_for(model_key, db)
    save_launch_settings(model_key, payload)
    return ApiResponse.success(data={"profile": payload.model_dump(), "saved": True})


@router.delete("/runtime/settings/{model_key}")
def restore_runtime_settings(model_key: str, db: Session = Depends(get_db)):
    _launch_settings_for(model_key, db)
    reset_launch_settings(model_key)
    profile, saved = _launch_settings_for(model_key, db)
    return ApiResponse.success(data={"profile": profile.model_dump(), "saved": saved})


@router.post("/runtime/start")
async def start_runtime(payload: RuntimeStartRequest, db: Session = Depends(get_db)):
    _ensure_local_runtime_usage_enabled()
    base_url = await asyncio.to_thread(
        get_runtime_manager().ensure_running,
        payload.model_key,
        context_length=payload.context_length,
        task_type=payload.task_type,
        project_id=payload.project_id,
    )
    config_crud = model_config_crud(db)
    config = config_crud.get_provider("local_llama_cpp")
    if not config:
        config = config_crud.create(
            provider="local_llama_cpp",
            api_key_encrypted=encrypt("__local_runtime__"),
            default_model=payload.model_key,
            provider_type="local_runtime",
            max_output_tokens=16384,
        )
        db.add(config)
    config.default_model = payload.model_key
    mark_model_ready(config, source="local_runtime_started", message="本地模型已成功加载")
    became_global = config_crud.make_global_if_no_ready_default(config)
    commit_session(db)
    return ApiResponse.success(data={
        **get_runtime_manager().status(),
        "base_url": base_url,
        "became_global_default": became_global,
    })


@router.post("/runtime/stop")
def stop_runtime():
    get_runtime_manager().stop()
    return ApiResponse.success(data=get_runtime_manager().status())


@router.delete("/{model_key}")
def delete_model(model_key: str, db: Session = Depends(get_db)):
    store = local_model_store(db)
    model = store.model(model_key)
    if not model:
        return ApiResponse.success()
    if get_runtime_manager().status().get("model_key") == model_key:
        get_runtime_manager().stop()
    # A manually registered file belongs to the user. Removing it from the
    # model center must not erase an arbitrary file outside Siming's model
    # directory.
    if not _managed_model_file(model):
        store.delete(model)
        commit_session(db)
        return ApiResponse.success(message="已移除模型登记，原文件未改动")
    if model.file_path:
        path = Path(model.file_path)
        if path.exists():
            path.unlink()
    model.file_path = None
    model.file_size = None
    model.status = "available"
    model.installed_at = None
    commit_session(db)
    return ApiResponse.success(message="模型已删除")


@router.post("/benchmark")
async def benchmark(payload: BenchmarkRequest):
    _ensure_local_runtime_usage_enabled()
    started = time.perf_counter()
    result = await LLMGateway.chat_completion(
        messages=[{"role": "user", "content": payload.prompt}],
        model=f"local_llama_cpp:{payload.model_key}",
        temperature=0.2,
        max_tokens=payload.max_tokens,
        extra_body={"moshu_task_type": "assistant"},
        retry=0,
        timeout=180,
    )
    elapsed = max(0.001, time.perf_counter() - started)
    reply = str(result.get("content") or "")
    reasoning = str(result.get("reasoning_content") or "")
    measured_text = reply or reasoning
    completion_tokens = int((result.get("usage") or {}).get("completion_tokens") or 0)
    tokens_estimated = False
    if not completion_tokens and measured_text.strip():
        completion_tokens = max(1, len(measured_text.strip()))
        tokens_estimated = True
    return ApiResponse.success(data={
        "reply": reply,
        "reasoning_preview": reasoning[:500] if reasoning and not reply else "",
        "reasoning_only": bool(reasoning and not reply),
        "elapsed_seconds": round(elapsed, 2),
        "completion_tokens": completion_tokens,
        "tokens_estimated": tokens_estimated,
        "tokens_per_second": round(completion_tokens / elapsed, 2) if completion_tokens else None,
    })


@router.post("/qualify")
async def qualify(payload: QualificationRequest, db: Session = Depends(get_db)):
    """Verify that a local model can execute Siming's critical task contracts."""

    _ensure_local_runtime_usage_enabled()
    model = local_model_store(db).model(payload.model_key)
    if not model or model.status != "installed":
        raise ValidationError("本地模型尚未安装，无法执行任务验证")
    capacity = max(4096, int(model.context_length or 4096))
    context_length = int(
        payload.context_length
        or min(capacity, detect_hardware().recommended_context)
    )
    if context_length > capacity:
        raise ValidationError(f"验证上下文 {context_length} 超过模型容量 {capacity}")
    result = await qualify_local_model(payload.model_key, context_length)
    return ApiResponse.success(data=result)


@router.get("/adapters")
def list_adapters(project_id: str | None = None, db: Session = Depends(get_db)):
    items = local_model_store(db).adapters(project_id)
    return ApiResponse.success(data={"items": [{
        "id": item.id,
        "project_id": item.project_id,
        "base_model_key": item.base_model_key,
        "name": item.name,
        "scope": item.scope,
        "file_path": item.file_path,
        "weight": item.weight,
        "enabled": item.enabled,
        "is_default_for_writing": item.is_default_for_writing,
        "metrics": item.metrics_json or {},
    } for item in items]})


@router.patch("/adapters/{adapter_id}")
def update_adapter(adapter_id: str, payload: AdapterUpdateRequest, db: Session = Depends(get_db)):
    store = local_model_store(db)
    item = store.adapter(adapter_id)
    if not item:
        raise ValueError("适配器不存在")
    model = store.model(item.base_model_key)
    if payload.enabled and item.base_model_sha256 and model and model.sha256 != item.base_model_sha256:
        raise ValueError("适配器与当前基座模型哈希不兼容")
    for field in ("enabled", "weight", "is_default_for_writing"):
        value = getattr(payload, field)
        if value is not None:
            setattr(item, field, value)
    commit_session(db)
    get_runtime_manager().stop()
    return ApiResponse.success(message="适配器设置已更新")


@router.post("/adapters/compare")
async def compare_adapters(payload: AdapterCompareRequest, db: Session = Depends(get_db)):
    _ensure_local_runtime_usage_enabled()
    candidates: list[tuple[str, list[str]]] = [("基座模型", [])]
    adapters = local_model_store(db).selected_adapters(payload.adapter_ids, payload.model_key)
    candidates.extend((adapter.name, [adapter.id]) for adapter in adapters)
    results: list[dict] = []
    for name, adapter_ids in candidates:
        result = await LLMGateway.chat_completion(
            messages=[
                {"role": "system", "content": "你是中文小说写作模型。只输出正文，不解释。"},
                {"role": "user", "content": payload.prompt},
            ],
            model=f"local_llama_cpp:{payload.model_key}",
            temperature=0.8,
            max_tokens=payload.max_tokens,
            timeout=300,
            retry=0,
            extra_body={
                "moshu_task_type": "writing",
                "moshu_project_id": payload.project_id,
                "moshu_adapter_ids": adapter_ids,
            },
        )
        results.append({"source": name, "content": result.get("content") or ""})
    random.SystemRandom().shuffle(results)
    comparison_id = str(uuid.uuid4())
    labels = []
    reveal: dict[str, str] = {}
    for index, result in enumerate(results):
        label = chr(ord("A") + index)
        labels.append({"label": label, "content": result["content"]})
        reveal[label] = result["source"]
    _ADAPTER_COMPARISONS[comparison_id] = reveal
    return ApiResponse.success(data={"comparison_id": comparison_id, "variants": labels})


@router.get("/adapters/compare/{comparison_id}/reveal")
def reveal_adapter_comparison(comparison_id: str):
    mapping = _ADAPTER_COMPARISONS.pop(comparison_id, None)
    if not mapping:
        raise ValueError("对比结果不存在或已揭晓")
    return ApiResponse.success(data={"mapping": mapping})


@router.post("/training/datasets")
def create_dataset(payload: DatasetCreateRequest, db: Session = Depends(get_db)):
    dataset = build_training_dataset(db, **payload.model_dump())
    commit_session(db)
    db.refresh(dataset)
    return ApiResponse.success(data={
        "id": dataset.id,
        "sample_count": dataset.sample_count,
        "train_count": dataset.train_count,
        "eval_count": dataset.eval_count,
        "stats": dataset.stats_json or {},
    })


@router.get("/training/datasets")
def list_datasets(project_id: str | None = None, db: Session = Depends(get_db)):
    rows = local_model_store(db).datasets(project_id)
    return ApiResponse.success(data={"items": [{
        "id": row.id,
        "project_id": row.project_id,
        "name": row.name,
        "sample_count": row.sample_count,
        "train_count": row.train_count,
        "eval_count": row.eval_count,
        "stats": row.stats_json or {},
        "rights_confirmed": row.rights_confirmed,
    } for row in rows]})


@router.post("/training/jobs")
def create_training(payload: TrainingJobCreateRequest):
    values = payload.model_dump()
    job_id = create_training_job(
        dataset_id=values.pop("dataset_id"),
        base_model_key=values.pop("base_model_key"),
        name=values.pop("name"),
        project_id=values.pop("project_id"),
        config=values,
    )
    return ApiResponse.success(data={"job_id": job_id})


@router.get("/training/jobs")
def list_training_jobs(project_id: str | None = None, db: Session = Depends(get_db)):
    rows = local_model_store(db).training_jobs(project_id)
    return ApiResponse.success(data={"items": [_training_payload(row) for row in rows]})


@router.post("/training/jobs/{job_id}/{action}")
def control_training(job_id: str, action: str):
    control_training_job(job_id, action)
    return ApiResponse.success(message=f"训练任务已{action}")


@router.get("/training/jobs/{job_id}/events")
async def training_events(job_id: str):
    async def stream():
        last = None
        while True:
            from ..database.session import SessionLocal

            with SessionLocal() as db:
                job = local_model_store(db).training_job(job_id)
                if not job:
                    yield f"data: {json.dumps({'status': 'missing'}, ensure_ascii=False)}\n\n"
                    return
                payload = _training_payload(job)
            encoded = json.dumps(payload, ensure_ascii=False)
            if encoded != last:
                yield f"data: {encoded}\n\n"
                last = encoded
            if payload["status"] in {"completed", "failed", "cancelled"}:
                return
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


def _training_payload(job: Any) -> dict:
    log_tail = ""
    if job.log_path and Path(job.log_path).exists():
        lines = Path(job.log_path).read_text(encoding="utf-8", errors="replace").splitlines()
        log_tail = "\n".join(lines[-80:])
    return {
        "id": job.id,
        "project_id": job.project_id,
        "dataset_id": job.dataset_id,
        "base_model_key": job.base_model_key,
        "name": job.name,
        "status": job.status,
        "progress": job.progress,
        "current_step": job.current_step,
        "total_steps": job.total_steps,
        "metrics": job.metrics_json or {},
        "output_path": job.output_path,
        "error_message": job.error_message,
        "log_tail": log_tail,
    }
