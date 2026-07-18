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

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.architecture.uow import commit_session

from ..modules.model_runtime.application.execution import model_executor as LLMGateway
from ..ai.local_runtime_policy import local_runtime_disabled, local_runtime_disabled_message
from ..core.exceptions import ValidationError
from ..core.legacy_env import get_compatible_env, set_compatible_env
from ..core.response import ApiResponse
from ..database.models import (
    LocalModel,
    LocalModelTaskSetting,
    LocalRuntimeInstallation,
    ModelAdapter,
    ModelDownloadTask,
    TrainingDataset,
    TrainingJob,
)
from ..database.session import get_db
from ..schemas.local_model import (
    AdapterCompareRequest,
    AdapterUpdateRequest,
    BenchmarkRequest,
    DatasetCreateRequest,
    ModelInstallRequest,
    ModelRootUpdateRequest,
    RuntimeStartRequest,
    TrainingJobCreateRequest,
)
from ..services.local_runtime import get_runtime_manager
from ..services.local_runtime.datasets import build_training_dataset
from ..services.local_runtime.hardware import detect_hardware
from ..services.local_runtime.manifest import model_catalog
from ..services.local_runtime.model_jobs import (
    create_model_download,
    create_runtime_download,
    ensure_catalog_rows,
)
from ..services.local_runtime.paths import model_root
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


def _model_payload(model: LocalModel) -> dict:
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


def _task_payload(task: ModelDownloadTask) -> dict:
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


@router.get("/hardware")
def hardware_profile():
    return ApiResponse.success(data=detect_hardware().to_dict())


@router.get("/catalog")
def catalog(db: Session = Depends(get_db)):
    ensure_catalog_rows()
    rows = db.query(LocalModel).order_by(LocalModel.recommended_vram_gb.asc()).all()
    runtime = db.query(LocalRuntimeInstallation).filter(
        LocalRuntimeInstallation.runtime_key == "llama_cpp"
    ).first()
    settings = db.query(LocalModelTaskSetting).all()
    usage_enabled = not local_runtime_disabled("local_llama_cpp")
    return ApiResponse.success(data={
        "usage_enabled": usage_enabled,
        "usage_disabled_reason": None if usage_enabled else local_runtime_disabled_message(),
        "items": [_model_payload(row) for row in rows],
        "manifest": model_catalog(),
        "runtime": {
            "status": runtime.status if runtime else "not_installed",
            "version": runtime.version if runtime else None,
            "backend": runtime.backend if runtime else None,
            "executable_path": runtime.executable_path if runtime else None,
            **get_runtime_manager().status(),
        },
        "model_root": str(model_root()),
        "task_settings": {
            item.task_type: {
                "model_key": item.model_key,
                "adapter_ids": item.adapter_ids or [],
                "context_length": item.context_length,
                "allow_api_fallback": item.allow_api_fallback,
            }
            for item in settings
        },
    })


@router.put("/root")
def update_model_root(payload: ModelRootUpdateRequest, db: Session = Depends(get_db)):
    target = Path(payload.path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    current = model_root()
    if target != current:
        for model in db.query(LocalModel).filter(LocalModel.status == "installed").all():
            if not model.file_path:
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


@router.get("/downloads")
def downloads(db: Session = Depends(get_db)):
    tasks = db.query(ModelDownloadTask).order_by(ModelDownloadTask.created_at.desc()).limit(100).all()
    return ApiResponse.success(data={"items": [_task_payload(task) for task in tasks]})


@router.get("/downloads/{task_id}/events")
async def download_events(task_id: str):
    async def stream():
        last_payload = None
        while True:
            from ..database.session import SessionLocal

            with SessionLocal() as db:
                task = db.query(ModelDownloadTask).filter(ModelDownloadTask.id == task_id).first()
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


@router.post("/runtime/start")
async def start_runtime(payload: RuntimeStartRequest):
    _ensure_local_runtime_usage_enabled()
    base_url = await asyncio.to_thread(
        get_runtime_manager().ensure_running,
        payload.model_key,
        context_length=payload.context_length,
        task_type=payload.task_type,
        project_id=payload.project_id,
    )
    return ApiResponse.success(data={**get_runtime_manager().status(), "base_url": base_url})


@router.post("/runtime/stop")
def stop_runtime():
    get_runtime_manager().stop()
    return ApiResponse.success(data=get_runtime_manager().status())


@router.delete("/{model_key}")
def delete_model(model_key: str, db: Session = Depends(get_db)):
    model = db.query(LocalModel).filter(LocalModel.model_key == model_key).first()
    if not model:
        return ApiResponse.success()
    if get_runtime_manager().status().get("model_key") == model_key:
        get_runtime_manager().stop()
    if model.file_path:
        path = Path(model.file_path)
        if path.exists():
            shutil.rmtree(path.parent, ignore_errors=True)
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
        extra_body={"moshu_task_type": "chat"},
        retry=0,
        timeout=180,
    )
    elapsed = max(0.001, time.perf_counter() - started)
    reply = result.get("content") or ""
    completion_tokens = int((result.get("usage") or {}).get("completion_tokens") or 0)
    tokens_estimated = False
    if not completion_tokens and reply.strip():
        completion_tokens = max(1, len(reply.strip()))
        tokens_estimated = True
    return ApiResponse.success(data={
        "reply": reply,
        "elapsed_seconds": round(elapsed, 2),
        "completion_tokens": completion_tokens,
        "tokens_estimated": tokens_estimated,
        "tokens_per_second": round(completion_tokens / elapsed, 2) if completion_tokens else None,
    })


@router.put("/task-settings/{task_type}")
def update_task_setting(task_type: str, payload: dict, db: Session = Depends(get_db)):
    model_key = str(payload.get("model_key") or "").strip()
    row = db.query(LocalModelTaskSetting).filter(LocalModelTaskSetting.task_type == task_type).first()
    if not model_key:
        if row:
            db.delete(row)
            commit_session(db)
        return ApiResponse.success(message="任务模型设置已清除，将跟随全局默认模型")
    _ensure_local_runtime_usage_enabled()
    if not row:
        row = LocalModelTaskSetting(task_type=task_type, model_key=model_key)
        db.add(row)
    row.model_key = model_key
    row.adapter_ids = payload.get("adapter_ids") or []
    row.context_length = payload.get("context_length")
    row.allow_api_fallback = bool(payload.get("allow_api_fallback", False))
    commit_session(db)
    return ApiResponse.success(message="任务模型设置已保存")


@router.delete("/task-settings/{task_type}")
def clear_task_setting(task_type: str, db: Session = Depends(get_db)):
    row = db.query(LocalModelTaskSetting).filter(LocalModelTaskSetting.task_type == task_type).first()
    if row:
        db.delete(row)
        commit_session(db)
    return ApiResponse.success(message="任务模型设置已清除，将跟随全局默认模型")


@router.get("/adapters")
def list_adapters(project_id: str | None = None, db: Session = Depends(get_db)):
    query = db.query(ModelAdapter)
    if project_id:
        query = query.filter((ModelAdapter.project_id == project_id) | (ModelAdapter.project_id.is_(None)))
    items = query.order_by(ModelAdapter.created_at.desc()).all()
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
    item = db.query(ModelAdapter).filter(ModelAdapter.id == adapter_id).first()
    if not item:
        raise ValueError("适配器不存在")
    model = db.query(LocalModel).filter(LocalModel.model_key == item.base_model_key).first()
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
    adapters = (
        db.query(ModelAdapter)
        .filter(
            ModelAdapter.id.in_(payload.adapter_ids),
            ModelAdapter.base_model_key == payload.model_key,
        )
        .all()
        if payload.adapter_ids
        else []
    )
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
    query = db.query(TrainingDataset)
    if project_id:
        query = query.filter(TrainingDataset.project_id == project_id)
    rows = query.order_by(TrainingDataset.created_at.desc()).all()
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
    query = db.query(TrainingJob)
    if project_id:
        query = query.filter(TrainingJob.project_id == project_id)
    rows = query.order_by(TrainingJob.created_at.desc()).all()
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
                job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
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


def _training_payload(job: TrainingJob) -> dict:
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
