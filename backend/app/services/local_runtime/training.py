"""Persistent NVIDIA QLoRA training jobs."""
from __future__ import annotations

from app.architecture.uow import commit_session

import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from ...database.models import LocalModel, ModelAdapter, TrainingDataset, TrainingJob
from ...database.session import SessionLocal
from .hardware import detect_hardware
from .paths import training_root
from .trainer_env import ensure_llama_conversion_tools, ensure_training_environment


TRAINING_MODEL_IDS = {
    "qwen3-4b-q4": "Qwen/Qwen3-4B",
    "qwen3-8b-q4": "Qwen/Qwen3-8B",
    "qwen3-14b-q4": "Qwen/Qwen3-14B",
}

_WORKERS: dict[str, threading.Thread] = {}
_PROCESSES: dict[str, subprocess.Popen] = {}
_LOCK = threading.Lock()


TRAINER_SCRIPT = r'''
import json
import os
import sys
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainerCallback
from trl import SFTConfig, SFTTrainer

cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
control = Path(cfg["control_file"])
output = Path(cfg["output_dir"])
output.mkdir(parents=True, exist_ok=True)

def emit(event, **payload):
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)

class SimingCallback(TrainerCallback):
    def on_step_end(self, args, state, control_state, **kwargs):
        emit("progress", step=state.global_step, total=state.max_steps, progress=(state.global_step / max(1, state.max_steps)))
        command = control.read_text(encoding="utf-8").strip() if control.exists() else ""
        if command in {"pause", "cancel"}:
            control_state.should_save = True
            control_state.should_training_stop = True
            emit(command, step=state.global_step)
        return control_state

records = [json.loads(line) for line in Path(cfg["dataset_path"]).read_text(encoding="utf-8").splitlines() if line.strip()]
train = [r for r in records if r.get("split") != "eval"]
evaluation = [r for r in records if r.get("split") == "eval"]

def format_record(row):
    instruction = row.get("instruction", "")
    source = row.get("input", "")
    answer = row.get("output", "")
    return {"text": f"<|im_start|>system\n你是司命小说写作模型。<|im_end|>\n<|im_start|>user\n{instruction}\n{source}<|im_end|>\n<|im_start|>assistant\n{answer}<|im_end|>"}

train_ds = Dataset.from_list([format_record(r) for r in train])
eval_ds = Dataset.from_list([format_record(r) for r in evaluation])
quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
tokenizer = AutoTokenizer.from_pretrained(cfg["model_id"], trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(cfg["model_id"], quantization_config=quant, device_map="auto", trust_remote_code=True)
peft = LoraConfig(r=cfg["lora_rank"], lora_alpha=cfg["lora_rank"] * 2, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM", target_modules="all-linear")
args = SFTConfig(
    output_dir=str(output),
    num_train_epochs=cfg["epochs"],
    learning_rate=cfg["learning_rate"],
    per_device_train_batch_size=cfg["batch_size"],
    gradient_accumulation_steps=cfg["gradient_accumulation"],
    max_length=cfg["max_sequence_length"],
    logging_steps=1,
    save_steps=max(10, len(train) // 4),
    save_total_limit=3,
    eval_strategy="steps" if evaluation else "no",
    eval_steps=max(10, len(train) // 4),
    bf16=torch.cuda.is_bf16_supported(),
    fp16=not torch.cuda.is_bf16_supported(),
    report_to="none",
    dataset_text_field="text",
)
trainer = SFTTrainer(model=model, args=args, train_dataset=train_ds, eval_dataset=eval_ds if evaluation else None, peft_config=peft, processing_class=tokenizer, callbacks=[SimingCallback()])
emit("started")
resume = cfg.get("resume_from_checkpoint")
result = trainer.train(resume_from_checkpoint=resume or None)
command = control.read_text(encoding="utf-8").strip() if control.exists() else ""
if command in {"pause", "cancel"}:
    emit(command, checkpoint=trainer.state.best_model_checkpoint or "")
    sys.exit(75 if command == "pause" else 76)
trainer.save_model(str(output / "adapter"))
tokenizer.save_pretrained(str(output / "adapter"))
metrics = dict(result.metrics)
if evaluation:
    metrics.update({f"eval_{k}": v for k, v in trainer.evaluate().items()})
emit("completed", metrics=metrics, adapter_path=str(output / "adapter"))
'''


def create_training_job(
    *,
    dataset_id: str,
    base_model_key: str,
    name: str,
    project_id: str | None,
    config: dict,
) -> str:
    profile = detect_hardware()
    if not profile.training_supported:
        raise ValueError("LoRA 训练 Beta 需要至少 8GB 显存的 NVIDIA 显卡")
    if base_model_key not in TRAINING_MODEL_IDS:
        raise ValueError("当前基座不支持内置 QLoRA 训练")
    if base_model_key == "qwen3-14b-q4" and profile.vram_gb < 24:
        raise ValueError("14B QLoRA 建议至少 24GB 显存；当前设备请使用 4B 或 8B")
    with SessionLocal() as db:
        dataset = db.query(TrainingDataset).filter(TrainingDataset.id == dataset_id).first()
        if not dataset or not dataset.rights_confirmed:
            raise ValueError("训练集不存在或尚未确认数据权利")
        job = TrainingJob(
            project_id=project_id,
            dataset_id=dataset_id,
            base_model_key=base_model_key,
            name=name,
            status="queued",
            config_json=config,
        )
        db.add(job)
        commit_session(db)
        job_id = job.id
    start_training_job(job_id)
    return job_id


def start_training_job(job_id: str) -> None:
    with _LOCK:
        worker = _WORKERS.get(job_id)
        if worker and worker.is_alive():
            return
        worker = threading.Thread(target=_run_job, args=(job_id,), daemon=True, name=f"siming-train-{job_id}")
        _WORKERS[job_id] = worker
        worker.start()


def control_training_job(job_id: str, action: str) -> None:
    if action not in {"pause", "cancel", "resume"}:
        raise ValueError("不支持的训练控制动作")
    job_dir = training_root() / "jobs" / job_id
    control = job_dir / "control.txt"
    if action == "resume":
        control.write_text("", encoding="utf-8")
        with SessionLocal() as db:
            job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
            if not job:
                raise ValueError("训练任务不存在")
            job.status = "queued"
            job.error_message = None
            commit_session(db)
        start_training_job(job_id)
        return
    control.parent.mkdir(parents=True, exist_ok=True)
    control.write_text(action, encoding="utf-8")
    if action == "cancel":
        _set_status(job_id, "cancelling")
        process = _PROCESSES.get(job_id)
        if process and process.poll() is None:
            process.terminate()


def resume_incomplete_training_jobs() -> None:
    with SessionLocal() as db:
        jobs = db.query(TrainingJob).filter(TrainingJob.status.in_(["queued", "running"])).all()
        ids = [job.id for job in jobs]
        for job in jobs:
            if job.pid:
                _terminate_process_tree(job.pid)
            job.status = "queued"
            job.pid = None
        commit_session(db)
    for job_id in ids:
        start_training_job(job_id)


def _log(job_id: str, message: str) -> None:
    path = training_root() / "jobs" / job_id / "training.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.utcnow().isoformat()} {message}\n")


def _run_job(job_id: str) -> None:
    try:
        with SessionLocal() as db:
            job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
            if not job:
                return
            dataset = db.query(TrainingDataset).filter(TrainingDataset.id == job.dataset_id).first()
            if not dataset:
                raise RuntimeError("训练集已被删除")
            config = dict(job.config_json or {})
            model_id = TRAINING_MODEL_IDS[job.base_model_key]
            dataset_path = dataset.file_path
            job.status = "preparing"
            commit_session(db)

        python = ensure_training_environment(lambda message: _log(job_id, message))
        job_dir = training_root() / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        script = job_dir / "train.py"
        script.write_text(TRAINER_SCRIPT, encoding="utf-8")
        control = job_dir / "control.txt"
        if not control.exists():
            control.write_text("", encoding="utf-8")
        output = job_dir / "output"
        payload = {
            **config,
            "model_id": model_id,
            "dataset_path": dataset_path,
            "control_file": str(control),
            "output_dir": str(output),
            "resume_from_checkpoint": _latest_checkpoint(output),
        }
        config_path = job_dir / "config.json"
        config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        with SessionLocal() as db:
            job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
            job.status = "running"
            job.log_path = str(job_dir / "training.log")
            commit_session(db)

        kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen([str(python), str(script), str(config_path)], **kwargs)
        _PROCESSES[job_id] = process
        with SessionLocal() as db:
            job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
            job.pid = process.pid
            commit_session(db)
        for line in process.stdout or []:
            text = line.rstrip()
            _log(job_id, text)
            _handle_event(job_id, text)
        return_code = process.wait()
        _PROCESSES.pop(job_id, None)
        requested = control.read_text(encoding="utf-8").strip() if control.exists() else ""
        if requested == "cancel":
            _set_status(job_id, "cancelled")
        elif return_code == 75:
            _set_status(job_id, "paused")
        elif return_code == 76:
            _set_status(job_id, "cancelled")
        elif return_code != 0:
            raise RuntimeError(f"训练进程退出，代码 {return_code}")
        else:
            _convert_and_register_adapter(job_id, python, model_id, output / "adapter")
    except Exception as exc:
        _set_status(job_id, "failed", str(exc))


def _handle_event(job_id: str, text: str) -> None:
    try:
        event = json.loads(text)
    except Exception:
        return
    kind = event.get("event")
    with SessionLocal() as db:
        job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
        if not job:
            return
        if kind == "progress":
            job.current_step = int(event.get("step") or 0)
            job.total_steps = int(event.get("total") or 0)
            job.progress = float(event.get("progress") or 0)
        elif kind == "completed":
            adapter_dir = str(event.get("adapter_path") or "")
            job.status = "converting"
            job.progress = 0.98
            job.output_path = adapter_dir
            job.metrics_json = event.get("metrics") or {}
        commit_session(db)


def _set_status(job_id: str, status: str, error: str | None = None) -> None:
    with SessionLocal() as db:
        job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
        if job:
            job.status = status
            job.error_message = error
            job.pid = None
            if status in {"failed", "cancelled"}:
                job.completed_at = datetime.utcnow()
            commit_session(db)


def _latest_checkpoint(output: Path) -> str | None:
    checkpoints = sorted(output.glob("checkpoint-*"), key=lambda path: int(path.name.split("-")[-1]))
    return str(checkpoints[-1]) if checkpoints else None


def _terminate_process_tree(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.kill(pid, 15)
    except Exception:
        pass


def _convert_and_register_adapter(job_id: str, python: Path, model_id: str, adapter_dir: Path) -> None:
    converter = ensure_llama_conversion_tools()
    output = adapter_dir.parent / "adapter-f16.gguf"
    _log(job_id, "正在转换为 llama.cpp GGUF LoRA")
    kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [
            str(python),
            str(converter),
            "--base-model-id",
            model_id,
            "--outfile",
            str(output),
            "--outtype",
            "f16",
            str(adapter_dir),
        ],
        **kwargs,
    )
    _log(job_id, result.stdout or "")
    if result.returncode or not output.exists():
        raise RuntimeError("LoRA 已训练完成，但转换为 GGUF 失败")
    with SessionLocal() as db:
        job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
        if not job:
            return
        model = db.query(LocalModel).filter(LocalModel.model_key == job.base_model_key).first()
        db.add(ModelAdapter(
            project_id=job.project_id,
            base_model_key=job.base_model_key,
            name=job.name,
            file_path=str(output),
            base_model_sha256=model.sha256 if model else None,
            scope="project" if job.project_id else "private",
            enabled=False,
            metrics_json=job.metrics_json or {},
        ))
        job.status = "completed"
        job.progress = 1.0
        job.output_path = str(output)
        job.completed_at = datetime.utcnow()
        job.pid = None
        commit_session(db)
