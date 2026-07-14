"""Beginner-friendly setup endpoints for a free OpenCode first run."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ai.local_cli_adapter import DEFAULT_CLI_ARGS
from ..core.crypto import encrypt
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.models import APIConfig
from ..database.session import get_db
from ..services.external_agent.mcp_auto_config import auto_configure_mcp_for_provider
from ..services.opencode_onboarding import (
    OPENCODE_INSTALL_DOCS_URL,
    OPENCODE_MODELS_DOCS_URL,
    OPENCODE_RELEASES_URL,
    get_opencode_install_job,
    inspect_opencode,
    is_free_opencode_model,
    managed_opencode_command,
    resolve_opencode_command,
    start_opencode_install,
)


router = APIRouter(tags=["getting-started"])


class OpenCodeConfigureRequest(BaseModel):
    model: str = Field(..., min_length=2, max_length=200)
    command: str | None = Field(None, max_length=500)


def _getting_started_summary(db: Session) -> dict:
    opencode_config = db.query(APIConfig).filter(APIConfig.provider == "opencode_cli").first()
    global_config = db.query(APIConfig).filter(APIConfig.is_global_default == True).first()  # noqa: E712
    return {
        "installed": bool(opencode_config and opencode_config.cli_command),
        "command": opencode_config.cli_command if opencode_config else None,
        "version": None,
        "managed_by_siming": False,
        "models": [],
        "model_source": "none",
        "free_models": [],
        "recommended_model": None,
        "platform_supported": True,
        "install_location": str(managed_opencode_command()),
        "configured": bool(opencode_config),
        "configured_model": opencode_config.default_model if opencode_config else None,
        "is_global_default": bool(opencode_config and opencode_config.is_global_default),
        "has_any_model": bool(db.query(APIConfig.id).first()),
        "needs_setup": global_config is None,
        "global_model": {
            "provider": global_config.provider,
            "model": global_config.default_model,
        } if global_config else None,
        "official_links": {
            "releases": OPENCODE_RELEASES_URL,
            "install_docs": OPENCODE_INSTALL_DOCS_URL,
            "model_docs": OPENCODE_MODELS_DOCS_URL,
        },
    }


def _getting_started_status(db: Session, *, refresh: bool = False) -> dict:
    opencode_config = db.query(APIConfig).filter(APIConfig.provider == "opencode_cli").first()
    inspected = inspect_opencode(
        opencode_config.cli_command if opencode_config else None,
        refresh=refresh,
    )
    summary = _getting_started_summary(db)
    return {
        **summary,
        **inspected,
        "configured": bool(opencode_config),
        "configured_model": opencode_config.default_model if opencode_config else None,
        "is_global_default": bool(opencode_config and opencode_config.is_global_default),
    }


@router.get("/config/getting-started")
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

    config = db.query(APIConfig).filter(APIConfig.provider == "opencode_cli").first()
    if config:
        config.api_key_encrypted = encrypt("__local_cli__")
        config.default_model = model
        config.provider_type = "local_cli"
        config.cli_command = command
        config.cli_args = json.dumps(DEFAULT_CLI_ARGS["opencode_cli"], ensure_ascii=False)
    else:
        config = APIConfig(
            provider="opencode_cli",
            api_key_encrypted=encrypt("__local_cli__"),
            default_model=model,
            is_global_default=False,
            provider_type="local_cli",
            cli_command=command,
            cli_args=json.dumps(DEFAULT_CLI_ARGS["opencode_cli"], ensure_ascii=False),
        )
        db.add(config)
    db.commit()
    db.refresh(config)
    mcp_setup = auto_configure_mcp_for_provider("opencode_cli", cli_command=command)
    return ApiResponse.success(
        data={
            "provider": config.provider,
            "model": config.default_model,
            "command": config.cli_command,
            "cli_args": config.cli_args,
            "mcp_auto_setup": mcp_setup,
            "status": _getting_started_status(db),
        },
        message="OpenCode 已交给司命管理，下一步测试免费模型",
    )
