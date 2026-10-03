"""Register an existing runtime file through the application persistence boundary."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from ...architecture.uow import commit_session
from ...core.exceptions import ValidationError
from ...database.models import LocalRuntimeInstallation
from .hardware import detect_hardware
from .manager import get_runtime_manager
from .model_jobs import cancel_active_downloads


def register_runtime_file(db: Session, file_path: str) -> None:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file() or path.name.lower() != "llama-server.exe":
        raise ValidationError("请选择存在的 llama-server.exe")
    cancel_active_downloads("runtime", "llama_cpp")
    runtime = db.query(LocalRuntimeInstallation).filter(
        LocalRuntimeInstallation.runtime_key == "llama_cpp"
    ).first()
    if not runtime:
        runtime = LocalRuntimeInstallation(runtime_key="llama_cpp")
        db.add(runtime)
    if get_runtime_manager().status()["running"]:
        get_runtime_manager().stop()
    runtime.executable_path = str(path)
    runtime.install_path = str(path.parent)
    runtime.backend = "cuda" if detect_hardware().nvidia_available else "cpu"
    runtime.status = "stopped"
    runtime.version = "本机文件"
    runtime.last_error = None
    commit_session(db)
