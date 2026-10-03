"""Schemas for the local model center and LoRA training beta."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .config import TaskModelType


class LocalModelBase(BaseModel):
    model_config = ConfigDict(protected_namespaces=())


class ModelInstallRequest(LocalModelBase):
    model_key: str = Field(..., min_length=1, max_length=512)


class ModelRootUpdateRequest(LocalModelBase):
    path: str = Field(..., min_length=1, max_length=1000)


class RuntimeStartRequest(LocalModelBase):
    model_key: str = Field(..., min_length=1, max_length=512)
    # llama.cpp and the selected model decide the practical limit. Do not
    # impose a product ceiling: high-memory machines legitimately use much
    # larger context windows.
    context_length: Optional[int] = Field(None, ge=1)
    task_type: TaskModelType = "assistant"
    project_id: Optional[str] = Field(None, max_length=36)


class RuntimeUsageRequest(LocalModelBase):
    enabled: bool


class RuntimeLaunchSettings(LocalModelBase):
    """Validated llama-server options exposed by the desktop model center."""

    context_length: int = Field(..., ge=1)
    gpu_layers: int = Field(99, ge=0, le=999)
    threads: Optional[int] = Field(None, ge=1, le=256)
    flash_attention: Literal["auto", "on", "off"] = "auto"
    fit: Literal["auto", "on", "off"] = "auto"
    kv_cache_type: Literal["f16", "q8_0", "q4_0"] = "f16"
    mtp_draft_tokens: int = Field(0, ge=0, le=4)
    cache_ram_mb: int = Field(0, ge=0, le=65536)
    reasoning_effort: Optional[Literal["low", "medium", "high"]] = None
    temperature: Optional[float] = Field(None, ge=0, le=2)
    top_p: Optional[float] = Field(None, ge=0, le=1)
    top_k: Optional[int] = Field(None, ge=0, le=200)
    min_p: Optional[float] = Field(None, ge=0, le=1)
    repeat_penalty: Optional[float] = Field(None, ge=0.5, le=2)


class CatalogModelFileRequest(LocalModelBase):
    file_path: str = Field(..., min_length=1, max_length=4000)


class RuntimeExecutableRequest(LocalModelBase):
    file_path: str = Field(..., min_length=1, max_length=4000)


class BenchmarkRequest(LocalModelBase):
    model_key: str = Field(..., min_length=1, max_length=512)
    prompt: str = Field("请用中文简短介绍你自己。", min_length=1, max_length=2000)
    max_tokens: int = Field(128, ge=8, le=2048)


class QualificationRequest(LocalModelBase):
    model_key: str = Field(..., min_length=1, max_length=512)
    context_length: Optional[int] = Field(None, ge=4096)


class CustomModelDownloadRequest(LocalModelBase):
    model_key: str = Field(..., min_length=1, max_length=512, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    display_name: str = Field(..., min_length=1, max_length=200)
    source_url: str = Field(..., min_length=8, max_length=4000, pattern=r"^https?://")
    context_length: int = Field(..., ge=1)


class CustomModelImportRequest(LocalModelBase):
    model_key: str = Field(..., min_length=1, max_length=512, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    display_name: str = Field(..., min_length=1, max_length=200)
    file_path: str = Field(..., min_length=1, max_length=4000)
    context_length: int = Field(..., ge=1)


class AdapterUpdateRequest(LocalModelBase):
    enabled: Optional[bool] = None
    weight: Optional[float] = Field(None, ge=-4, le=4)
    is_default_for_writing: Optional[bool] = None


class AdapterCompareRequest(LocalModelBase):
    model_key: str = Field(..., min_length=1, max_length=512)
    prompt: str = Field(..., min_length=1, max_length=8000)
    project_id: Optional[str] = Field(None, max_length=36)
    adapter_ids: list[str] = Field(default_factory=list, max_length=2)
    max_tokens: int = Field(800, ge=100, le=4000)


class DatasetCreateRequest(LocalModelBase):
    name: str = Field(..., min_length=1, max_length=200)
    project_id: Optional[str] = Field(None, max_length=36)
    chapter_ids: list[str] = Field(default_factory=list)
    include_outline_pairs: bool = True
    include_revision_pairs: bool = True
    include_character_dialogue: bool = True
    eval_ratio: float = Field(0.1, ge=0.05, le=0.3)
    rights_confirmed: bool = False


class TrainingJobCreateRequest(LocalModelBase):
    name: str = Field(..., min_length=1, max_length=200)
    dataset_id: str = Field(..., min_length=1, max_length=36)
    base_model_key: str = Field(..., min_length=1, max_length=512)
    project_id: Optional[str] = Field(None, max_length=36)
    epochs: float = Field(1.0, gt=0, le=10)
    learning_rate: float = Field(0.0002, gt=0, le=0.01)
    lora_rank: int = Field(16, ge=4, le=128)
    batch_size: int = Field(1, ge=1, le=16)
    gradient_accumulation: int = Field(8, ge=1, le=128)
    max_sequence_length: int = Field(4096, ge=512, le=16384)
