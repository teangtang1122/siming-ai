"""Pydantic schemas for external Agent permission settings."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ExternalAgentSettingsRead(BaseModel):
    """Schema for reading external Agent settings."""
    id: str
    project_id: str
    enabled_packs: list[str]
    trusted_local_enabled: bool
    trusted_local_clients: list[str]
    require_confirmation_for_writes: bool
    require_confirmation_for_destructive: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ExternalAgentSettingsUpdate(BaseModel):
    """Schema for updating external Agent settings."""
    enabled_packs: Optional[list[str]] = Field(default=None, description="Enabled permission packs")
    trusted_local_enabled: Optional[bool] = Field(default=None, description="Enable trusted local mode")
    trusted_local_clients: Optional[list[str]] = Field(default=None, description="Trusted client names")
    require_confirmation_for_writes: Optional[bool] = Field(default=None, description="Require confirmation for writes")
    require_confirmation_for_destructive: Optional[bool] = Field(default=None, description="Require confirmation for destructive actions")


# Default settings for new projects
DEFAULT_ENABLED_PACKS = ["readonly_collaboration"]
DEFAULT_TRUSTED_LOCAL_ENABLED = False
DEFAULT_TRUSTED_LOCAL_CLIENTS: list[str] = []
DEFAULT_REQUIRE_CONFIRMATION_FOR_WRITES = True
DEFAULT_REQUIRE_CONFIRMATION_FOR_DESTRUCTIVE = True
