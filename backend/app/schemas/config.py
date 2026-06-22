"""Pydantic schemas for model provider config and global model settings."""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
LOCAL_CLI_PROVIDER_IDS = {
    "claude_cli",
    "codex_cli",
    "opencode_cli",
    "mimocode_cli",
    "cursor_cli",
    "kilocode_cli",
    "qwen_code_cli",
    "hermes_cli",
    "openclaw_cli",
    "custom_cli",
}


def validate_provider_id(provider: str) -> str:
    provider = provider.strip()
    if not PROVIDER_ID_PATTERN.fullmatch(provider):
        raise ValueError("Provider id may only contain letters, numbers, underscores, and hyphens")
    return provider


class APIConfigCreate(BaseModel):
    """Schema for creating/updating an API or local CLI model config."""

    provider: str = Field(..., min_length=1, max_length=50, description="Provider id")
    api_key: Optional[str] = Field(None, description="API key; not required for local CLI providers")
    default_model: str = Field(..., min_length=1, max_length=100, description="Default model name")
    base_url_override: Optional[str] = Field(None, max_length=500, description="Custom API endpoint")
    provider_type: Optional[str] = Field(None, max_length=20, description="api or local_cli")
    cli_command: Optional[str] = Field(None, max_length=500, description="Local CLI command")
    cli_args: Optional[str] = Field(
        None,
        max_length=2000,
        description="Local CLI args as JSON array or shell-like text; may include {prompt} and {model}",
    )
    max_output_tokens: Optional[int] = Field(None, ge=1, le=1000000, description="Max output tokens")
    deconstruct_input_char_limit: Optional[int] = Field(None, ge=1, le=1000000, description="Deconstruct merge input char limit")
    deconstruct_item_char_limit: Optional[int] = Field(None, ge=1, le=1000000, description="Deconstruct item char limit")

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, provider: str) -> str:
        return validate_provider_id(provider)

    @model_validator(mode="after")
    def _validate_api_key_for_api_provider(self):
        provider_type = self.provider_type or ("local_cli" if self.provider in LOCAL_CLI_PROVIDER_IDS else "api")
        if provider_type != "local_cli" and not (self.api_key or "").strip():
            raise ValueError("API Key is required for API providers")
        return self


class GlobalModelSetting(BaseModel):
    """Schema for global default model setting."""

    provider: str = Field(..., description="Global default provider")
    model: str = Field(..., description="Global default model name")

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, provider: str) -> str:
        return validate_provider_id(provider)


class ModelListRequest(BaseModel):
    """Schema for requesting available models from a provider."""

    provider: str = Field(..., min_length=1, max_length=50, description="Provider id")
    api_key: Optional[str] = Field(None, description="API key")
    base_url_override: Optional[str] = Field(None, max_length=500, description="Custom API endpoint")
    cli_command: Optional[str] = Field(None, max_length=500, description="Local CLI command")
    cli_args: Optional[str] = Field(None, max_length=2000, description="Local CLI args")

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, provider: str) -> str:
        return validate_provider_id(provider)

    @model_validator(mode="after")
    def _validate_api_key_for_api_provider(self):
        if self.provider not in LOCAL_CLI_PROVIDER_IDS and not (self.api_key or "").strip():
            raise ValueError("API Key is required for API providers")
        return self


class ConnectionTestRequest(BaseModel):
    """Schema for testing provider connection."""

    provider: str = Field(..., min_length=1, max_length=50, description="Provider id")
    api_key: Optional[str] = Field(None, description="API key")
    base_url_override: Optional[str] = Field(None, max_length=500, description="Custom API endpoint")
    cli_command: Optional[str] = Field(None, max_length=500, description="Local CLI command")
    cli_args: Optional[str] = Field(None, max_length=2000, description="Local CLI args")
    model: Optional[str] = Field(None, max_length=200, description="Model used by the local CLI smoke test")

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, provider: str) -> str:
        return validate_provider_id(provider)

    @model_validator(mode="after")
    def _validate_api_key_for_api_provider(self):
        if self.provider not in LOCAL_CLI_PROVIDER_IDS and not (self.api_key or "").strip():
            raise ValueError("API Key is required for API providers")
        return self
