"""Pydantic schemas for model provider config and global model settings."""
from __future__ import annotations

import re
from typing import Literal, Optional

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
    "dsh_cli",
    "custom_cli",
}
LOCAL_RUNTIME_PROVIDER_IDS = {"local_llama_cpp"}
APIProtocol = Literal["auto", "chat_completions", "responses"]
MODEL_IDENTIFIER_MAX_LENGTH = 512
TASK_MODEL_TYPES = (
    "assistant",
    "planning",
    "cataloging",
    "writing",
    "evaluation",
    "deconstruct",
)
TaskModelType = Literal[
    "assistant",
    "planning",
    "cataloging",
    "writing",
    "evaluation",
    "deconstruct",
]


def validate_provider_id(provider: str) -> str:
    provider = provider.strip()
    if not PROVIDER_ID_PATTERN.fullmatch(provider):
        raise ValueError("Provider id may only contain letters, numbers, underscores, and hyphens")
    return provider


class ProviderModelOption(BaseModel):
    """One model identity returned by a configured provider."""

    id: str = Field(..., min_length=1, max_length=MODEL_IDENTIFIER_MAX_LENGTH)
    display_name: Optional[str] = Field(None, max_length=MODEL_IDENTIFIER_MAX_LENGTH)
    context_window_tokens: Optional[int] = Field(None, ge=2048, le=10_000_000)
    max_output_tokens: Optional[int] = Field(None, ge=1, le=1_000_000)
    safety_margin_tokens: Optional[int] = Field(None, ge=0, le=100_000)
    capacity_source: Optional[str] = Field(None, max_length=100)

    @field_validator("id", "display_name")
    @classmethod
    def _strip_model_value(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if isinstance(value, str) else value


class APIConfigCreate(BaseModel):
    """Schema for creating/updating an API or local CLI model config."""

    provider: str = Field(..., min_length=1, max_length=50, description="Provider id")
    api_key: Optional[str] = Field(None, description="API key; not required for local CLI providers")
    default_model: str = Field(..., min_length=1, max_length=MODEL_IDENTIFIER_MAX_LENGTH, description="Default model name")
    base_url_override: Optional[str] = Field(None, max_length=500, description="Custom API endpoint")
    api_protocol: APIProtocol = Field("auto", description="OpenAI-compatible wire protocol")
    provider_type: Optional[str] = Field(None, max_length=20, description="api, local_cli, or local_runtime")
    cli_command: Optional[str] = Field(None, max_length=500, description="Local CLI command")
    cli_args: Optional[str] = Field(
        None,
        max_length=2000,
        description="Local CLI args as JSON array or shell-like text; may include {prompt} and {model}",
    )
    max_output_tokens: Optional[int] = Field(None, ge=1, le=1000000, description="Max output tokens")
    context_window_tokens: Optional[int] = Field(
        None,
        ge=2048,
        le=10_000_000,
        description="Author-confirmed context window for the default model",
    )
    context_safety_margin_tokens: Optional[int] = Field(
        512,
        ge=0,
        le=100_000,
        description="Reserved context safety margin",
    )
    deconstruct_input_char_limit: Optional[int] = Field(None, ge=1, le=1000000, description="Deconstruct merge input char limit")
    deconstruct_item_char_limit: Optional[int] = Field(None, ge=1, le=1000000, description="Deconstruct item char limit")
    available_models: list[ProviderModelOption] = Field(
        default_factory=list,
        max_length=5000,
        description="Models discovered from this exact provider configuration",
    )

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, provider: str) -> str:
        return validate_provider_id(provider)

    @model_validator(mode="after")
    def _validate_context_capacity(self):
        if self.context_window_tokens is None:
            return self
        margin = int(self.context_safety_margin_tokens or 0)
        if self.max_output_tokens is not None and (
            int(self.max_output_tokens) + margin >= int(self.context_window_tokens)
        ):
            raise ValueError(
                "max output tokens plus safety margin must be smaller than context window"
            )
        return self


class GlobalModelSetting(BaseModel):
    """Schema for global default model setting."""

    provider: str = Field(..., description="Global default provider")
    model: str = Field(..., min_length=1, max_length=MODEL_IDENTIFIER_MAX_LENGTH, description="Global default model name")

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, provider: str) -> str:
        return validate_provider_id(provider)


class TaskModelSettingUpdate(BaseModel):
    """Select one configured provider model as the default for a task family."""

    provider: str = Field(..., min_length=1, max_length=50)
    model: str = Field(..., min_length=1, max_length=MODEL_IDENTIFIER_MAX_LENGTH)
    context_length: Optional[int] = Field(None, ge=1, le=1000000)

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, provider: str) -> str:
        return validate_provider_id(provider)

    @field_validator("model")
    @classmethod
    def _strip_model(cls, model: str) -> str:
        return model.strip()


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

class ConnectionTestRequest(BaseModel):
    """Schema for testing provider connection."""

    provider: str = Field(..., min_length=1, max_length=50, description="Provider id")
    api_key: Optional[str] = Field(None, description="API key")
    base_url_override: Optional[str] = Field(None, max_length=500, description="Custom API endpoint")
    api_protocol: APIProtocol = Field("auto", description="OpenAI-compatible wire protocol")
    cli_command: Optional[str] = Field(None, max_length=500, description="Local CLI command")
    cli_args: Optional[str] = Field(None, max_length=2000, description="Local CLI args")
    model: Optional[str] = Field(None, max_length=MODEL_IDENTIFIER_MAX_LENGTH, description="Model used by the real generation smoke test")
    timeout_seconds: Optional[int] = Field(
        None,
        ge=15,
        le=180,
        description="Optional model connection smoke-test timeout",
    )

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, provider: str) -> str:
        return validate_provider_id(provider)
