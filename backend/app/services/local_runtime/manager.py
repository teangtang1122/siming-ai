"""Lifecycle manager for Siming's managed llama.cpp server."""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.architecture.uow import commit_session

from ...database.models import (
    LocalModel,
    LocalRuntimeInstallation,
    ModelAdapter,
    ModelTaskSetting,
)
from ...database.session import SessionLocal
from ...modules.model_runtime.domain.policy import (
    local_runtime_disabled,
    local_runtime_disabled_message,
)
from ...schemas.local_model import RuntimeLaunchSettings
from .hardware import detect_hardware
from .launch_settings import default_launch_settings, load_launch_settings
from .paths import siming_home

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
        self._api_key: str | None = None
        self._last_adjustment: str | None = None
        self._last_log_path: str | None = None

    @property
    def base_url(self) -> str | None:
        return f"http://127.0.0.1:{self._port}/v1" if self._port else None

    @property
    def api_key(self) -> str | None:
        """Return the per-process credential without exposing it in status APIs."""

        return self._api_key

    def request_token_counter(
        self,
        model_key: str,
        tools: Sequence[Mapping[str, Any]],
    ):
        """Bind the loaded model's tokenizer and native request template."""
        from .token_counter import make_request_token_counter

        return make_request_token_counter(self, model_key, tools)

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

    @staticmethod
    def _default_context_length(model_context: int | None, recommended_context: int) -> int:
        """Use a memory-safe starting context without reducing model capability."""

        capacity = int(model_context or 0)
        recommendation = max(1, int(recommended_context or 8192))
        return min(capacity, recommendation) if capacity > 0 else recommendation

    @staticmethod
    def _launch_profiles(nvidia_available: bool, context: int, gpu_layers: int = 99) -> list[tuple[int, int]]:
        """Try GPU first, then preserve the same context on CPU/RAM."""

        if nvidia_available and gpu_layers:
            return [(gpu_layers, context), (0, context)]
        return [(0, context)]

    def ensure_running(
        self,
        model_key: str,
        *,
        context_length: int | None = None,
        task_type: str = "assistant",
        project_id: str | None = None,
        adapter_ids: list[str] | None = None,
    ) -> str:
        with self._lock:
            if local_runtime_disabled():
                raise RuntimeError(local_runtime_disabled_message())
            model, runtime, adapters = self._load_assets(
                model_key,
                task_type,
                project_id,
                adapter_ids,
            )
            profile = detect_hardware()
            # The catalog value is a useful starting point, not a ceiling.
            # Users with large VRAM/unified memory may select a model's full
            # or RoPE-scaled context window.
            default_context = self._default_context_length(
                model.context_length,
                profile.recommended_context,
            )
            launch_settings, saved = load_launch_settings(
                model_key,
                context_length=default_context,
                gpu_layers=99 if profile.nvidia_available else 0,
                threads=max(2, profile.cpu_count - 1),
            )
            context = launch_settings.context_length if saved else context_length or launch_settings.context_length
            signature = json.dumps({
                "adapters": [(adapter.file_path, adapter.weight) for adapter in adapters],
                "model_file": model.file_path,
                "settings": launch_settings.model_dump(),
            }, ensure_ascii=False, sort_keys=True)
            if (
                self._model_key == model_key
                and self._requested_context_length == context
                and self._adapter_signature == signature
                and self._healthy()
            ):
                # A previous HTTP caller may have disconnected after launch.
                # Reconcile durable state before returning the healthy process.
                self._mark_runtime_running()
                return self.base_url or ""

            self.stop()
            # Never silently shrink a context selected by the user. A failed
            # launch reports llama.cpp's diagnostic so the user can choose a
            # lower value deliberately, instead of losing project evidence.
            launch_profiles = self._launch_profiles(profile.nvidia_available, context, launch_settings.gpu_layers)
            last_error = "本地模型运行时启动失败"
            for attempt, (gpu_layers, attempt_context) in enumerate(launch_profiles):
                port = _free_port()
                runtime_api_key = secrets.token_urlsafe(32)
                command = self._build_command(
                    runtime.executable_path,
                    model.file_path,
                    model.model_key,
                    port,
                    attempt_context,
                    launch_settings.threads or max(2, profile.cpu_count - 1),
                    gpu_layers,
                    adapters,
                    api_key=runtime_api_key,
                    launch_settings=launch_settings,
                )
                stdout_path, stderr_path = self._launch_log_paths(model.model_key, attempt)
                self._last_log_path = str(stderr_path)
                with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
                    stderr.write(
                        (
                            f"\n\n=== {datetime.utcnow().isoformat()}Z "
                            f"model={model.model_key} gpu_layers={gpu_layers} "
                            f"context={attempt_context} ===\n"
                            "command="
                            f"{json.dumps(self._redacted_command(command), ensure_ascii=False)}\n"
                        ).encode("utf-8", errors="replace")
                    )
                    stderr.flush()
                    self._process = subprocess.Popen(
                        command,
                        **_hidden_process_kwargs(stdout=stdout, stderr=stderr),
                    )
                self._port = port
                self._api_key = runtime_api_key
                self._model_key = model_key
                self._context_length = attempt_context
                self._requested_context_length = context
                self._adapter_signature = signature
                self._last_adjustment = (
                    None
                    if attempt == 0
                    else f"GPU 加载未成功，已改用 CPU/系统内存；上下文仍为 {attempt_context}"
                )
                self._record_start(runtime, model.id)

                started_at = time.monotonic()
                deadline = started_at + 120
                detached_grace_deadline = started_at + 10
                while time.monotonic() < deadline:
                    if local_runtime_disabled():
                        self.stop()
                        raise RuntimeError(local_runtime_disabled_message())
                    if self._healthy():
                        self._mark_runtime_running()
                        with SessionLocal() as db:
                            row = db.query(LocalModel).filter(LocalModel.id == model.id).first()
                            if row:
                                row.last_used_at = datetime.utcnow()
                                commit_session(db)
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
            self._api_key = None
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
                        commit_session(db)
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
        api_key: str | None = None,
        launch_settings: RuntimeLaunchSettings | None = None,
    ) -> list[str]:
        settings = launch_settings or default_launch_settings(
            model_key, context_length=context_length,
            gpu_layers=gpu_layers, threads=thread_count,
        )
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
            "--cors-origins",
            "localhost",
        ]
        if api_key:
            command.extend(["--api-key", api_key])
        if settings.fit != "auto":
            command.extend(["--fit", settings.fit])
        if settings.flash_attention != "auto":
            command.extend(["--flash-attn", settings.flash_attention])
        if settings.kv_cache_type != "f16":
            command.extend(["--cache-type-k", settings.kv_cache_type,
                            "--cache-type-v", settings.kv_cache_type])
        if settings.mtp_draft_tokens:
            command.extend(["--spec-type", "draft-mtp", "--spec-draft-n-max",
                            str(settings.mtp_draft_tokens)])
        if settings.cache_ram_mb:
            command.extend(["--cache-ram", str(settings.cache_ram_mb)])
        for option, value in (
            ("--reasoning-effort", settings.reasoning_effort),
            ("--temp", settings.temperature),
            ("--top-p", settings.top_p),
            ("--top-k", settings.top_k),
            ("--min-p", settings.min_p),
            ("--repeat-penalty", settings.repeat_penalty),
        ):
            if value is not None:
                command.extend([option, str(value)])
        for adapter in adapters:
            command.extend(["--lora-scaled", adapter.file_path, str(adapter.weight or 1.0)])
        return command

    @staticmethod
    def _redacted_command(command: list[str]) -> list[str]:
        """Keep diagnostics useful without writing the runtime credential."""

        redacted = list(command)
        try:
            key_index = redacted.index("--api-key") + 1
        except ValueError:
            return redacted
        if key_index < len(redacted):
            redacted[key_index] = "<redacted>"
        return redacted

    @staticmethod
    def _launch_log_paths(model_key: str, attempt: int) -> tuple[Path, Path]:
        safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", model_key)[:80] or "model"
        log_dir = siming_home() / "logs" / "local-runtime"
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
            task_setting = db.query(ModelTaskSetting).filter(
                ModelTaskSetting.task_type == task_type,
                ModelTaskSetting.provider == "local_llama_cpp",
                ModelTaskSetting.model_name == model_key,
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
            commit_session(db)

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
                commit_session(db)

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
                commit_session(db)


_MANAGER = LocalRuntimeManager()


def get_runtime_manager() -> LocalRuntimeManager:
    return _MANAGER
