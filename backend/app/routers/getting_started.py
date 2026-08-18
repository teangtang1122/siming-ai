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
from ..services.external_agent.mcp_auto_config import (
    configure_cli_integration,
    scan_cli_integrations,
)
from ..services.external_agent.mcp_preflight import preflight_cli_integration
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
    model: str = Field(..., min_length=2, max_length=512)
    command: str | None = Field(None, max_length=500)


class OpenCodeActivateRequest(BaseModel):
    preferred_model: str | None = Field(None, max_length=512)


class OpenCodeCredentialRequest(BaseModel):
    credential: SecretStr = Field(..., min_length=1, max_length=4096)


class FreeModelOption(BaseModel):
    id: str
    display_name: str
    recommended: bool = False
    test_status: str = "untested"
    failure_kind: str | None = None


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
    download_source: str | None = None
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
    available_model: dict[str, str | None] | None = None
    activation_job: OpenCodeActivationStatus | None = None
    opencode_mcp_configured: bool = False
    official_links: dict[str, str] = Field(default_factory=dict)

    model_config = {"protected_namespaces": ()}


def _getting_started_summary(db: Session) -> dict:
    state = get_getting_started_configuration().state(db)
    opencode_mcp_configured = False
    if state.opencode_command:
        try:
            scan = scan_cli_integrations()
            opencode_mcp_configured = any(
                item.get("provider") == "opencode_cli" and bool(item.get("configured"))
                for item in scan.get("clients") or []
            )
        except Exception:
            opencode_mcp_configured = False
    has_usable_model = bool(
        state.has_usable_models and state.usable_provider and state.usable_model
    )
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
        "needs_setup": not has_usable_model,
        "recommended_action": "start_writing" if has_usable_model else "verify_detected" if state.has_detected_models else "activate_opencode",
        "global_model": {
            "provider": state.global_provider,
            "model": state.global_model,
        } if state.global_provider else None,
        "available_model": {
            "provider": state.usable_provider,
            "model": state.usable_model,
        } if has_usable_model else None,
        # An old interrupted activation must not restart UI polling after any
        # verified model is already available. Users can still revalidate a
        # model explicitly from model settings.
        "activation_job": None if has_usable_model else get_latest_opencode_activation_job(db),
        "opencode_mcp_configured": opencode_mcp_configured,
        "official_links": {
            "releases": OPENCODE_RELEASES_URL,
            "install_docs": OPENCODE_INSTALL_DOCS_URL,
            "model_docs": OPENCODE_MODELS_DOCS_URL,
        },
    }


def _getting_started_status(db: Session, *, refresh: bool = False) -> dict:
    state = get_getting_started_configuration().state(db)
    summary = _getting_started_summary(db)
    # The persisted readiness result is the stable completion state. Opening
    # Quick Start must not repeatedly launch CLI discovery once it is reached;
    # refresh=True remains the explicit user-requested recheck path.
    if state.has_usable_models and not refresh:
        return summary
    inspected = inspect_opencode(
        state.opencode_command,
        refresh=refresh,
    )
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
def activate_opencode(
    payload: OpenCodeActivateRequest | None = None,
    db: Session = Depends(get_db),
):
    if get_getting_started_configuration().state(db).has_usable_models:
        raise ValidationError(
            "系统中已有通过验证的可用模型，无需在快速开始中重复检测；"
            "如需重新验证，请前往“模型与训练”。"
        )
    try:
        job = start_opencode_activation(
            preferred_model=payload.preferred_model if payload else None,
        )
    except RuntimeError as exc:
        raise ValidationError(str(exc)) from exc
    return ApiResponse.success(data=job, message="免费写作能力正在准备")



@router.post("/config/getting-started/opencode/mcp/configure")
def configure_getting_started_opencode_mcp(db: Session = Depends(get_db)):
    """Explicitly configure and verify Siming MCP after OpenCode is usable."""

    state = get_getting_started_configuration().state(db)
    command = resolve_opencode_command(state.opencode_command)
    if not command:
        raise ValidationError("还没有可运行的 OpenCode，请先完成快速开始")
    configured = configure_cli_integration(
        "opencode_cli",
        cli_command=command,
        permission_pack="auto",
    )
    preflight = preflight_cli_integration(
        "opencode_cli",
        cli_command=command,
        permission_pack="cataloging_worker",
    )
    return ApiResponse.success(
        data={
            **configured,
            "ready": bool(preflight.get("ready")),
            "preflight": preflight,
        },
        message=(
            "OpenCode 与 Siming MCP 已配置并验证"
            if preflight.get("ready")
            else preflight.get("detail") or configured.get("detail") or "MCP 配置未完成"
        ),
    )


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
    return ApiResponse.success(
        data={
            "provider": config.provider,
            "model": config.model,
            "command": config.command,
            "cli_args": config.cli_args,
            "status": _getting_started_status(db),
        },
        message="OpenCode 模型已保存；如需连接 MCP，请在系统设置中单独授权",
    )
