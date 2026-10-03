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
from ..installer_updater import (
    download_and_stage_update,
    get_update_status,
    schedule_staged_update_install,
)
from ..services.application_settings import (
    DESKTOP_PET_MAX_OPACITY,
    DESKTOP_PET_MAX_SCALE,
    DESKTOP_PET_MIN_OPACITY,
    DESKTOP_PET_MIN_SCALE,
    app_home,
    launcher_settings_payload,
    normalize_gateway_advertised_url,
    normalize_gateway_allowed_hosts,
    update_launcher_preferences,
)

router = APIRouter(tags=["config"])


class LauncherSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    launch_mode: Literal["desktop", "browser"] | None = None
    update_channel: Literal["stable", "preview"] | None = None
    gateway_enabled: bool | None = None
    gateway_advertised_url: str | None = Field(default=None, max_length=2048)
    gateway_allowed_hosts: str | None = Field(default=None, max_length=4096)
    desktop_pet_enabled: bool | None = None
    desktop_pet_scale: float | None = Field(
        default=None,
        ge=DESKTOP_PET_MIN_SCALE,
        le=DESKTOP_PET_MAX_SCALE,
    )
    desktop_pet_opacity: float | None = Field(
        default=None,
        ge=DESKTOP_PET_MIN_OPACITY,
        le=DESKTOP_PET_MAX_OPACITY,
    )
    desktop_pet_muted: bool | None = None
    desktop_pet_on_top: bool | None = None

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


class ApplicationUpdateDownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["auto", "github", "gitee"] = "auto"


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
    updates = payload.model_dump(exclude_none=True)
    update_launcher_preferences(updates)
    return ApiResponse.success(
        data=launcher_settings_payload(),
        message="应用设置已保存",
    )


@router.post("/config/update/check")
def check_for_application_update():
    channel = launcher_settings_payload()["update_channel"]
    return ApiResponse.success(data=get_update_status(app_home(), channel))


@router.post("/config/update/download")
def download_application_update(
    payload: ApplicationUpdateDownloadRequest | None = None,
):
    channel = launcher_settings_payload()["update_channel"]
    try:
        data = download_and_stage_update(
            app_home(),
            channel,
            payload.source if payload else "auto",
        )
    except RuntimeError as exc:
        raise ValidationError(str(exc)) from exc
    return ApiResponse.success(
        data=data,
        message="更新已下载并完成 SHA256 校验",
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
    "ApplicationUpdateDownloadRequest",
    "LauncherSettingsUpdateRequest",
    "check_for_application_update",
    "download_application_update",
    "get_launcher_settings",
    "install_application_update",
    "router",
    "update_launcher_settings",
]
