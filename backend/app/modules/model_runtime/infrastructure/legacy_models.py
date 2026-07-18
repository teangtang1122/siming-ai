"""SQLAlchemy persistence models owned by the model_runtime module."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)

from app.database.models_support import generate_uuid
from app.database.session import Base


class OpenCodeActivationJob(Base):
    __tablename__ = "opencode_activation_jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    operation_id = Column(
        String(36), ForeignKey("operation_runs.id", ondelete="SET NULL"), nullable=True
    )
    status = Column(String(30), nullable=False, default="pending")
    phase = Column(String(40), nullable=False, default="checking")
    percent = Column(Integer, nullable=False, default=0)
    message = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    failure_kind = Column(String(50), nullable=True)
    next_action = Column(Text, nullable=True)
    auth_mode = Column(String(30), nullable=True)
    auth_status = Column(String(30), nullable=True)
    auth_prompt = Column(Text, nullable=True)
    auth_url = Column(String(1000), nullable=True)
    command = Column(String(500), nullable=True)
    version = Column(String(100), nullable=True)
    selected_model = Column(String(200), nullable=True)
    preferred_model = Column(String(200), nullable=True)
    free_models_json = Column(JSON, nullable=True)
    download_url = Column(String(1000), nullable=True)
    sha256 = Column(String(64), nullable=True)
    bytes_downloaded = Column(Integer, nullable=False, default=0)
    bytes_total = Column(Integer, nullable=False, default=0)
    estimated_seconds_remaining = Column(Integer, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_opencode_activation_jobs_status", "status"),)


class LocalModel(Base):
    __tablename__ = "local_models"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    model_key = Column(String(120), nullable=False, unique=True)
    display_name = Column(String(200), nullable=False)
    family = Column(String(100), nullable=False, default="qwen3")
    parameter_size = Column(String(30), nullable=True)
    quantization = Column(String(50), nullable=True)
    context_length = Column(Integer, nullable=False, default=8192)
    file_path = Column(Text, nullable=True)
    file_size = Column(Integer, nullable=True)
    sha256 = Column(String(64), nullable=True)
    license_name = Column(String(100), nullable=True)
    source = Column(String(50), nullable=True)
    source_urls = Column(JSON, nullable=True)
    min_ram_gb = Column(Integer, nullable=True)
    recommended_vram_gb = Column(Integer, nullable=True)
    status = Column(String(30), nullable=False, default="available")
    installed_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (Index("ix_local_models_status", "status"),)


class LocalRuntimeInstallation(Base):
    __tablename__ = "local_runtime_installations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    runtime_key = Column(String(80), nullable=False, unique=True, default="llama_cpp")
    version = Column(String(80), nullable=True)
    backend = Column(String(30), nullable=False, default="cpu")
    executable_path = Column(Text, nullable=True)
    install_path = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="not_installed")
    port = Column(Integer, nullable=True)
    pid = Column(Integer, nullable=True)
    active_model_id = Column(
        String(36), ForeignKey("local_models.id", ondelete="SET NULL"), nullable=True
    )
    last_error = Column(Text, nullable=True)
    last_health_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ModelDownloadTask(Base):
    __tablename__ = "model_download_tasks"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    operation_id = Column(
        String(36), ForeignKey("operation_runs.id", ondelete="SET NULL"), nullable=True
    )
    kind = Column(String(30), nullable=False, default="model")
    target_key = Column(String(120), nullable=False)
    source_url = Column(Text, nullable=True)
    destination_path = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="queued")
    downloaded_bytes = Column(Integer, nullable=False, default=0)
    total_bytes = Column(Integer, nullable=True)
    sha256 = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_model_download_tasks_status", "status"),)


class ModelAdapter(Base):
    __tablename__ = "model_adapters"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    base_model_key = Column(String(120), nullable=False)
    name = Column(String(200), nullable=False)
    adapter_type = Column(String(30), nullable=False, default="lora")
    scope = Column(String(30), nullable=False, default="private")
    file_path = Column(Text, nullable=False)
    base_model_sha256 = Column(String(64), nullable=True)
    weight = Column(Float, nullable=False, default=1.0)
    enabled = Column(Boolean, nullable=False, default=True)
    is_default_for_writing = Column(Boolean, nullable=False, default=False)
    metrics_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_model_adapters_project", "project_id"),
        Index("ix_model_adapters_base_model", "base_model_key"),
    )


class TrainingDataset(Base):
    __tablename__ = "training_datasets"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(200), nullable=False)
    source_config_json = Column(JSON, nullable=True)
    file_path = Column(Text, nullable=False)
    sample_count = Column(Integer, nullable=False, default=0)
    train_count = Column(Integer, nullable=False, default=0)
    eval_count = Column(Integer, nullable=False, default=0)
    stats_json = Column(JSON, nullable=True)
    rights_confirmed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class TrainingJob(Base):
    __tablename__ = "training_jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    dataset_id = Column(
        String(36), ForeignKey("training_datasets.id", ondelete="SET NULL"), nullable=True
    )
    base_model_key = Column(String(120), nullable=False)
    name = Column(String(200), nullable=False)
    status = Column(String(30), nullable=False, default="queued")
    progress = Column(Float, nullable=False, default=0.0)
    current_step = Column(Integer, nullable=False, default=0)
    total_steps = Column(Integer, nullable=True)
    config_json = Column(JSON, nullable=True)
    metrics_json = Column(JSON, nullable=True)
    checkpoint_path = Column(Text, nullable=True)
    output_path = Column(Text, nullable=True)
    log_path = Column(Text, nullable=True)
    pid = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_training_jobs_status", "status"),
        Index("ix_training_jobs_project", "project_id"),
    )


__all__ = [
    "OpenCodeActivationJob",
    "LocalModel",
    "LocalRuntimeInstallation",
    "ModelDownloadTask",
    "ModelAdapter",
    "TrainingDataset",
    "TrainingJob",
]
