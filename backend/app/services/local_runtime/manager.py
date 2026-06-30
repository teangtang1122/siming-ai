"""Lifecycle manager for Siming's managed llama.cpp server."""
from __future__ import annotations

import atexit
import json
import os
import re
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from ...database.models import (
    LocalModel,
    LocalModelTaskSetting,
    LocalRuntimeInstallation,
    ModelAdapter,
)
from ...database.session import SessionLocal
from .hardware import detect_hardware
from .paths import moshu_home


LOCAL_SERVER_PARALLEL_SLOTS = 1


def _hidden_process_kwargs(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) -> dict:
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": stdout,
        "stderr": stderr,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    return kwargs


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LocalRuntimeManager:
    """Own one resident llama-server process and hot-swap models as needed."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._model_key: str | None = None
        self._context_length: int | None = None
        self._requested_context_length: int | None = None
        self._adapter_signature = ""
        self._port: int | None = None
        self._last_adjustment: str | None = None
        self._last_log_path: str | None = None
        atexit.register(self.stop)

    @property
    def base_url(self) -> str | None:
        return f"http://127.0.0.1:{self._port}/v1" if self._port else None

    def status(self) -> dict:
        running = bool(self._port and self._healthy())
        pid = None
        if running:
            pid = self._process.pid if self._process and self._process.poll() is None else self._pid_for_port(self._port)
        return {
            "running": running,
            "pid": pid,
            "port": self._port if running else None,
            "model_key": self._model_key if running else None,
            "context_length": self._context_length if running else None,
            "requested_context_length": self._requested_context_length if running else None,
            "base_url": self.base_url if running else None,
            "adjustment": self._last_adjustment,
            "log_path": self._last_log_path,
        }

    def ensure_running(
        self,
        model_key: str,
        *,
        context_length: int | None = None,
        task_type: str = "chat",
        project_id: str | None = None,
        adapter_ids: list[str] | None = None,
    ) -> str:
        with self._lock:
            model, runtime, adapters = self._load_assets(
                model_key,
                task_type,
                project_id,
                adapter_ids,
            )
            signature = json.dumps(
                [(adapter.file_path, adapter.weight) for adapter in adapters],
                ensure_ascii=False,
            )
            profile = detect_hardware()
            context = min(
                context_length or profile.recommended_context,
                model.context_length or profile.recommended_context,
            )
            if (
                self._model_key == model_key
                and self._requested_context_length == context
                and self._adapter_signature == signature
                and self._healthy()
            ):
                return self.base_url or ""

            self.stop()
            launch_profiles = (
                [
                    (99, context),
                    (40, max(4096, context // 2)),
                    (0, max(4096, context // 2)),
                ]
                if profile.nvidia_available
                else [(0, context), (0, max(4096, context // 2))]
            )
            last_error = "本地模型运行时启动失败"
            for attempt, (gpu_layers, attempt_context) in enumerate(launch_profiles):
                port = _free_port()
                command = self._build_command(
                    runtime.executable_path,
                    model.file_path,
                    model.model_key,
                    port,
                    attempt_context,
                    max(2, profile.cpu_count - 1),
                    gpu_layers,
                    adapters,
                )
                stdout_path, stderr_path = self._launch_log_paths(model.model_key, attempt)
                self._last_log_path = str(stderr_path)
                with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
                    stderr.write(
                        (
                            f"\n\n=== {datetime.utcnow().isoformat()}Z "
                            f"model={model.model_key} gpu_layers={gpu_layers} "
                            f"context={attempt_context} ===\n"
                            f"command={json.dumps(command, ensure_ascii=False)}\n"
                        ).encode("utf-8", errors="replace")
                    )
                    stderr.flush()
                    self._process = subprocess.Popen(
                        command,
                        **_hidden_process_kwargs(stdout=stdout, stderr=stderr),
                    )
                self._port = port
                self._model_key = model_key
                self._context_length = attempt_context
                self._requested_context_length = context
                self._adapter_signature = signature
                self._last_adjustment = (
                    None
                    if attempt == 0
                    else f"已自动降级为 GPU 层 {gpu_layers}、上下文 {attempt_context}"
                )
                self._record_start(runtime, model.id)

                started_at = time.monotonic()
                deadline = started_at + 120
                detached_grace_deadline = started_at + 10
                while time.monotonic() < deadline:
                    if self._healthy():
                        self._mark_runtime_running()
                        with SessionLocal() as db:
                            row = db.query(LocalModel).filter(LocalModel.id == model.id).first()
                            if row:
                                row.last_used_at = datetime.utcnow()
                                db.commit()
                        return self.base_url or ""
                    if self._process.poll() is not None:
                        last_error = (
                            "本地模型加载失败，可能是显存或系统内存不足"
                            if gpu_layers
                            else "本地模型使用 CPU 加载仍然失败"
                        )
                        if time.monotonic() < detached_grace_deadline:
                            time.sleep(0.5)
                            continue
                        detail = self._tail_log(stderr_path)
                        if detail:
                            last_error = f"{last_error}\n\nllama.cpp 日志尾部：\n{detail}"
                        break
                    time.sleep(0.5)
                else:
                    detail = self._tail_log(stderr_path)
                    last_error = "本地模型启动超时"
                    if detail:
                        last_error = f"{last_error}\n\nllama.cpp 日志尾部：\n{detail}"
                self.stop()
            self._mark_runtime_error(last_error)
            raise RuntimeError(last_error)

    def stop(self) -> None:
        with self._lock:
            process = self._process
            port = self._port
            self._process = None
            self._port = None
            self._model_key = None
            self._context_length = None
            self._requested_context_length = None
            self._adapter_signature = ""
            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    process.kill()
            elif port:
                self._kill_pid(self._pid_for_port(port))
            try:
                with SessionLocal() as db:
                    runtime = db.query(LocalRuntimeInstallation).filter(
                        LocalRuntimeInstallation.runtime_key == "llama_cpp"
                    ).first()
                    if runtime:
                        runtime.status = "stopped" if runtime.executable_path else "not_installed"
                        runtime.port = None
                        runtime.pid = None
                        runtime.active_model_id = None
                        db.commit()
            except Exception:
                pass

    @staticmethod
    def _build_command(
        executable_path: str,
        model_path: str,
        model_key: str,
        port: int,
        context_length: int,
        thread_count: int,
        gpu_layers: int,
        adapters: list[ModelAdapter],
    ) -> list[str]:
        command = [
            executable_path,
            "--model",
            model_path,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ctx-size",
            str(context_length),
            "--alias",
            model_key,
            "--threads",
            str(thread_count),
            "--parallel",
            str(LOCAL_SERVER_PARALLEL_SLOTS),
            "--jinja",
            "--no-webui",
            "--gpu-layers",
            str(gpu_layers),
        ]
        for adapter in adapters:
            command.extend(["--lora-scaled", adapter.file_path, str(adapter.weight or 1.0)])
        return command

    @staticmethod
    def _launch_log_paths(model_key: str, attempt: int) -> tuple[Path, Path]:
        safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", model_key)[:80] or "model"
        log_dir = moshu_home() / "logs" / "local-runtime"
        log_dir.mkdir(parents=True, exist_ok=True)
        stem = f"llama-server-{safe_key}-attempt-{attempt + 1}"
        return log_dir / f"{stem}.out.log", log_dir / f"{stem}.err.log"

    @staticmethod
    def _tail_log(path: Path, max_chars: int = 5000) -> str:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
        return text[-max_chars:].strip()

    def _healthy(self) -> bool:
        if not self._port:
            return False
        try:
            with httpx.Client(timeout=1.5, trust_env=False) as client:
                response = client.get(f"http://127.0.0.1:{self._port}/health")
            return response.status_code == 200
        except Exception:
            return False

    @staticmethod
    def _pid_for_port(port: int | None) -> int | None:
        if not port or os.name != "nt":
            return None
        kwargs: dict = {"stdin": subprocess.DEVNULL}
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                timeout=5,
                **kwargs,
            )
        except Exception:
            return None
        suffix = f":{port}"
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(suffix) and parts[3].upper() == "LISTENING":
                try:
                    return int(parts[-1])
                except ValueError:
                    return None
        return None

    @staticmethod
    def _kill_pid(pid: int | None) -> None:
        if not pid:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F", "/T"],
                    timeout=10,
                    **_hidden_process_kwargs(),
                )
            except Exception:
                pass

    @staticmethod
    def _load_assets(
        model_key: str,
        task_type: str,
        project_id: str | None,
        adapter_ids: list[str] | None,
    ):
        with SessionLocal() as db:
            model = db.query(LocalModel).filter(LocalModel.model_key == model_key).first()
            if (
                not model
                or model.status != "installed"
                or not model.file_path
                or not Path(model.file_path).exists()
            ):
                raise RuntimeError(f"本地模型 {model_key} 尚未安装，请先在模型中心下载")
            runtime = db.query(LocalRuntimeInstallation).filter(
                LocalRuntimeInstallation.runtime_key == "llama_cpp"
            ).first()
            if not runtime or runtime.status == "not_installed" or not runtime.executable_path:
                raise RuntimeError("llama.cpp 运行时尚未安装，请先在模型中心安装")
            if not Path(runtime.executable_path).exists():
                raise RuntimeError("llama.cpp 运行时文件丢失，请重新安装")

            query = db.query(ModelAdapter).filter(ModelAdapter.base_model_key == model_key)
            if adapter_ids is None:
                query = query.filter(ModelAdapter.enabled == True)  # noqa: E712
            if project_id:
                query = query.filter(
                    (ModelAdapter.project_id == project_id)
                    | (ModelAdapter.project_id.is_(None))
                )
            else:
                query = query.filter(ModelAdapter.project_id.is_(None))
            adapters = query.all()
            task_setting = db.query(LocalModelTaskSetting).filter(
                LocalModelTaskSetting.task_type == task_type
            ).first()
            selected_ids = (
                set(adapter_ids)
                if adapter_ids is not None
                else set(task_setting.adapter_ids or [])
                if task_setting
                else set()
            )
            if adapter_ids is not None or selected_ids:
                adapters = [item for item in adapters if item.id in selected_ids]
            elif task_type == "writing":
                adapters = [item for item in adapters if item.is_default_for_writing]
            else:
                adapters = []
            adapters = [item for item in adapters if Path(item.file_path).exists()]
            return model, runtime, adapters

    def _record_start(self, runtime: LocalRuntimeInstallation, model_id: str) -> None:
        runtime.status = "starting"
        runtime.port = self._port
        runtime.pid = self._process.pid if self._process else None
        runtime.active_model_id = model_id
        runtime.last_error = None
        with SessionLocal() as db:
            db.merge(runtime)
            db.commit()

    @staticmethod
    def _mark_runtime_error(message: str) -> None:
        with SessionLocal() as db:
            runtime = db.query(LocalRuntimeInstallation).filter(
                LocalRuntimeInstallation.runtime_key == "llama_cpp"
            ).first()
            if runtime:
                runtime.status = "error"
                runtime.last_error = message
                runtime.port = None
                runtime.pid = None
                db.commit()

    def _mark_runtime_running(self) -> None:
        with SessionLocal() as db:
            runtime = db.query(LocalRuntimeInstallation).filter(
                LocalRuntimeInstallation.runtime_key == "llama_cpp"
            ).first()
            if runtime:
                runtime.status = "running"
                runtime.last_health_at = datetime.utcnow()
                runtime.port = self._port
                runtime.pid = self._process.pid if self._process else None
                db.commit()


_MANAGER = LocalRuntimeManager()


def get_runtime_manager() -> LocalRuntimeManager:
    return _MANAGER
