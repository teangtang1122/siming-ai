"""Beginner-friendly setup endpoints for a free OpenCode first run."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.orm import Session

from ..ai.local_cli_adapter import DEFAULT_CLI_ARGS
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.model_runtime.application.getting_started import (
    get_getting_started_configuration,
)
from ..services.external_agent.mcp_auto_config import auto_configure_mcp_for_provider
from ..services.opencode_onboarding import (
    OPENCODE_INSTALL_DOCS_URL,
    OPENCODE_MODELS_DOCS_URL,
    OPENCODE_RELEASES_URL,
    get_latest_opencode_activation_job,
    get_opencode_activation_job,
    get_opencode_install_job,
    inspect_opencode,
    is_free_opencode_model,
    managed_opencode_command,
    open_opencode_authentication,
    resolve_opencode_command,
    retry_opencode_activation,
    start_opencode_activation,
    start_opencode_install,
    submit_opencode_auth_credential,
)

router = APIRouter(tags=["getting-started"])


class OpenCodeConfigureRequest(BaseModel):
    model: str = Field(..., min_length=2, max_length=200)
    command: str | None = Field(None, max_length=500)


class OpenCodeActivateRequest(BaseModel):
    preferred_model: str | None = Field(None, max_length=200)


class OpenCodeCredentialRequest(BaseModel):
    credential: SecretStr = Field(..., min_length=1, max_length=4096)


class FreeModelOption(BaseModel):
    id: str
    display_name: str
    recommended: bool = False


class OpenCodeActivationStatus(BaseModel):
    id: str
    operation_id: str | None = None
    status: str
    phase: str
    percent: int = 0
    message: str = ""
    error: str | None = None
    failure_kind: str | None = None
    next_action: str | None = None
    auth_mode: str | None = None
    auth_status: str | None = None
    auth_prompt: str | None = None
    auth_url: str | None = None
    command: str | None = None
    version: str | None = None
    selected_model: str | None = None
    preferred_model: str | None = None
    free_models: list[FreeModelOption] = Field(default_factory=list)
    download_url: str | None = None
    sha256: str | None = None
    bytes_downloaded: int = 0
    bytes_total: int = 0
    estimated_seconds_remaining: int | None = None
    attempt_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class GettingStartedStatus(BaseModel):
    """Stable first-run projection shared by the GUI and generated OpenAPI types."""

    installed: bool
    command: str | None = None
    version: str | None = None
    managed_by_siming: bool = False
    models: list[dict[str, Any]] = Field(default_factory=list)
    model_source: str = "none"
    free_models: list[FreeModelOption] = Field(default_factory=list)
    recommended_model: str | None = None
    platform_supported: bool = True
    install_location: str
    configured: bool = False
    configured_model: str | None = None
    is_global_default: bool = False
    has_any_model: bool = False
    has_detected_models: bool = False
    has_usable_models: bool = False
    needs_setup: bool = True
    recommended_action: str
    global_model: dict[str, str | None] | None = None
    activation_job: OpenCodeActivationStatus | None = None
    official_links: dict[str, str] = Field(default_factory=dict)

    model_config = {"protected_namespaces": ()}


def _getting_started_summary(db: Session) -> dict:
    state = get_getting_started_configuration().state(db)
    return {
        "installed": bool(state.opencode_command),
        "command": state.opencode_command,
        "version": None,
        "managed_by_siming": False,
        "models": [],
        "model_source": "none",
        "free_models": [],
        "recommended_model": None,
        "platform_supported": True,
        "install_location": str(managed_opencode_command()),
        "configured": state.configured,
        "configured_model": state.configured_model,
        "is_global_default": state.opencode_is_global,
        "has_any_model": state.has_any_model,
        "has_detected_models": state.has_detected_models,
        "has_usable_models": state.has_usable_models,
        "needs_setup": state.global_provider is None,
        "recommended_action": "start_writing" if state.global_provider else "verify_detected" if state.has_detected_models else "activate_opencode",
        "global_model": {
            "provider": state.global_provider,
            "model": state.global_model,
        } if state.global_provider else None,
        "activation_job": get_latest_opencode_activation_job(db),
        "official_links": {
            "releases": OPENCODE_RELEASES_URL,
            "install_docs": OPENCODE_INSTALL_DOCS_URL,
            "model_docs": OPENCODE_MODELS_DOCS_URL,
        },
    }


def _getting_started_status(db: Session, *, refresh: bool = False) -> dict:
    state = get_getting_started_configuration().state(db)
    inspected = inspect_opencode(
        state.opencode_command,
        refresh=refresh,
    )
    summary = _getting_started_summary(db)
    return {
        **summary,
        **inspected,
        "configured": state.configured,
        "configured_model": state.configured_model,
        "is_global_default": state.opencode_is_global,
    }


@router.get(
    "/config/getting-started",
    response_model=ApiResponse[GettingStartedStatus],
)
def get_getting_started_status(
    summary: bool = False,
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    return ApiResponse.success(
        data=_getting_started_summary(db) if summary else _getting_started_status(db, refresh=refresh),
        message="首次使用环境检查完成",
    )


@router.post("/config/getting-started/opencode/install")
def install_opencode():
    try:
        job = start_opencode_install()
    except RuntimeError as exc:
        raise ValidationError(str(exc)) from exc
    return ApiResponse.success(data=job, message="OpenCode 安装任务已开始")


@router.post("/config/getting-started/opencode/activate")
def activate_opencode(payload: OpenCodeActivateRequest | None = None):
    try:
        job = start_opencode_activation(
            preferred_model=payload.preferred_model if payload else None,
        )
    except RuntimeError as exc:
        raise ValidationError(str(exc)) from exc
    return ApiResponse.success(data=job, message="免费写作能力正在准备")


@router.get("/config/getting-started/opencode/jobs/{job_id}")
def get_activation_status(job_id: str):
    job = get_opencode_activation_job(job_id)
    if not job:
        raise NotFoundError("没有找到这次免费体验任务")
    return ApiResponse.success(data=job, message="免费体验状态已更新")


@router.post("/config/getting-started/opencode/jobs/{job_id}/retry")
def retry_activation(job_id: str):
    try:
        job = retry_opencode_activation(job_id)
    except RuntimeError as exc:
        raise NotFoundError(str(exc)) from exc
    return ApiResponse.success(data=job, message="正在重新尝试")


@router.post("/config/getting-started/opencode/jobs/{job_id}/authenticate")
def authenticate_activation(job_id: str):
    try:
        result = open_opencode_authentication(job_id)
    except RuntimeError as exc:
        if "不存在" in str(exc):
            raise NotFoundError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc
    return ApiResponse.success(data=result, message="OpenCode 官方登录已经启动")


@router.post("/config/getting-started/opencode/jobs/{job_id}/credential")
def submit_activation_credential(job_id: str, payload: OpenCodeCredentialRequest):
    try:
        result = submit_opencode_auth_credential(job_id, payload.credential.get_secret_value())
    except RuntimeError as exc:
        raise ValidationError(str(exc)) from exc
    return ApiResponse.success(data=result, message="一次性凭据已提交")


@router.get("/config/getting-started/opencode/install/{job_id}")
def get_install_status(job_id: str):
    job = get_opencode_install_job(job_id)
    if not job:
        raise NotFoundError("没有找到这次 OpenCode 安装任务")
    return ApiResponse.success(data=job, message="OpenCode 安装状态已更新")


@router.post("/config/getting-started/opencode/configure")
def configure_opencode(payload: OpenCodeConfigureRequest, db: Session = Depends(get_db)):
    command = resolve_opencode_command(payload.command)
    if not command:
        raise ValidationError("还没有检测到 OpenCode，请先点击一键安装或重新检测")
    inspected = inspect_opencode(command, timeout=15, refresh=True)
    available = {str(item.get("id") or "") for item in inspected["models"]}
    model = payload.model.strip()
    if not is_free_opencode_model(model):
        raise ValidationError("快速开始只能选择 OpenCode 当前标记的免费模型")
    if inspected["model_source"] == "cli" and model not in available:
        raise ValidationError("这个免费模型当前没有出现在 OpenCode 的模型列表中，请重新检测")

    config = get_getting_started_configuration().configure_opencode(
        db,
        command=command,
        model=model,
        cli_args=json.dumps(DEFAULT_CLI_ARGS["opencode_cli"], ensure_ascii=False),
    )
    mcp_setup = auto_configure_mcp_for_provider("opencode_cli", cli_command=command)
    return ApiResponse.success(
        data={
            "provider": config.provider,
            "model": config.model,
            "command": config.command,
            "cli_args": config.cli_args,
            "mcp_auto_setup": mcp_setup,
            "status": _getting_started_status(db),
        },
        message="OpenCode 已交给司命管理，下一步测试免费模型",
    )
