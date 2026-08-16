"""Launcher preference and verified application update endpoints."""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.exceptions import ValidationError
from ..core.response import ApiResponse
from ..services.application_settings import (
    app_home,
    launcher_settings_payload,
    load_launcher_settings,
    normalize_gateway_advertised_url,
    normalize_gateway_allowed_hosts,
    save_launcher_settings,
)
from ..installer_updater import (
    download_and_stage_update,
    get_update_status,
    schedule_staged_update_install,
)

router = APIRouter(tags=["config"])


class LauncherSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    launch_mode: Literal["desktop", "browser"] | None = None
    update_channel: Literal["stable", "preview"] | None = None
    gateway_enabled: bool | None = None
    gateway_advertised_url: str | None = Field(default=None, max_length=2048)
    gateway_allowed_hosts: str | None = Field(default=None, max_length=4096)

    @field_validator("gateway_advertised_url")
    @classmethod
    def validate_advertised_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return normalize_gateway_advertised_url(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("gateway_allowed_hosts")
    @classmethod
    def validate_allowed_hosts(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return normalize_gateway_allowed_hosts(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


def _exit_after_update_install() -> None:
    """Let the HTTP response flush before the replacement helper waits."""
    if "pytest" in sys.modules or not getattr(sys, "frozen", False):
        return
    time.sleep(1.0)
    os._exit(0)


@router.get("/config/launcher")
def get_launcher_settings():
    return ApiResponse.success(data=launcher_settings_payload())


@router.put("/config/launcher")
def update_launcher_settings(payload: LauncherSettingsUpdateRequest):
    settings = load_launcher_settings()
    if payload.launch_mode is not None:
        settings["launch_mode"] = payload.launch_mode
    if payload.update_channel is not None:
        settings["update_channel"] = payload.update_channel
    if payload.gateway_enabled is not None:
        settings["gateway_enabled"] = payload.gateway_enabled
    if payload.gateway_advertised_url is not None:
        settings["gateway_advertised_url"] = payload.gateway_advertised_url
    if payload.gateway_allowed_hosts is not None:
        settings["gateway_allowed_hosts"] = payload.gateway_allowed_hosts
    save_launcher_settings(settings)
    return ApiResponse.success(
        data=launcher_settings_payload(),
        message="应用设置已保存",
    )


@router.post("/config/update/check")
def check_for_application_update():
    channel = launcher_settings_payload()["update_channel"]
    return ApiResponse.success(data=get_update_status(app_home(), channel))


@router.post("/config/update/download")
def download_application_update():
    channel = launcher_settings_payload()["update_channel"]
    try:
        data = download_and_stage_update(app_home(), channel)
    except RuntimeError as exc:
        raise ValidationError(str(exc)) from exc
    return ApiResponse.success(
        data=data,
        message="更新已下载并完成 SHA256 与签名校验",
    )


@router.post("/config/update/install")
def install_application_update():
    try:
        data = schedule_staged_update_install(app_home())
    except RuntimeError as exc:
        raise ValidationError(str(exc)) from exc
    threading.Thread(target=_exit_after_update_install, daemon=True).start()
    return ApiResponse.success(
        data=data,
        message="已验证更新，司命即将重启安装",
    )


__all__ = [
    "LauncherSettingsUpdateRequest",
    "check_for_application_update",
    "download_application_update",
    "get_launcher_settings",
    "install_application_update",
    "router",
    "update_launcher_settings",
]
