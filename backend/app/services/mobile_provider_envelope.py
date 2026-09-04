"""Decrypt an Android-owned model configuration without persisting its key."""

from __future__ import annotations

import base64
import ipaddress
import json
import socket
import time
from typing import Literal
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.modules.gateway.infrastructure.mobile_provider_crypto import (
    gateway_encryption_private_key,
)
from app.modules.gateway.infrastructure.models import GatewayIdentity
from app.modules.model_runtime.domain.configuration import ModelProviderConfig
from app.schemas.ai_writer import MobileProviderEnvelope

_ENVELOPE_INFO = b"siming-mobile-provider-envelope-v1"
_MAX_CLOCK_SKEW_MS = 5 * 60 * 1000


class _MobileProviderCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base_url: str = Field(min_length=8, max_length=2048)
    api_key: str = Field(min_length=1, max_length=16_384)
    model: str = Field(min_length=1, max_length=300)
    protocol: str = Field(pattern="^(responses|chat_completions)$")
    context_window_tokens: int = Field(gt=0, le=10_000_000)
    max_output_tokens: int = Field(gt=0, le=2_000_000)
    safety_margin_tokens: int = Field(ge=0, le=2_000_000)
    capacity_assurance: Literal["exact", "conservative", "unverified"] = "conservative"
    issued_at: int

    @model_validator(mode="after")
    def validate_capacity(self) -> _MobileProviderCredentials:
        if self.max_output_tokens + self.safety_margin_tokens >= self.context_window_tokens:
            raise ValueError("手机模型容量必须为输入保留正数空间")
        return self


def _decode(value: str, *, expected: int | None = None, maximum: int = 64 * 1024) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise ValidationError("手机模型凭据密文格式无效") from exc
    if not raw or len(raw) > maximum or (expected is not None and len(raw) != expected):
        raise ValidationError("手机模型凭据密文格式无效")
    return raw


def _validated_base_url(value: str) -> str:
    parsed = urlsplit(value.rstrip("/"))
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValidationError("手机模型 API 地址必须是无账号和查询参数的 HTTPS 地址")
    hostname = parsed.hostname.casefold().rstrip(".")
    internal_suffixes = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
    if hostname == "localhost" or hostname.endswith(internal_suffixes):
        raise ValidationError("手机模型 API 地址不能指向 Gateway 本机或内部网络")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValidationError("手机模型 API 地址不能指向私网、环回或链路本地地址")
    if address is None:
        try:
            address_info = socket.getaddrinfo(
                hostname,
                parsed.port or 443,
                type=socket.SOCK_STREAM,
            )
            resolved = {ipaddress.ip_address(item[4][0]) for item in address_info}
        except (OSError, ValueError) as exc:
            raise ValidationError("手机模型 API 域名无法解析，请检查地址后重试") from exc
        if not resolved or any(not item.is_global for item in resolved):
            raise ValidationError("手机模型 API 域名不能解析到私网、环回或链路本地地址")
    return value.rstrip("/")


def decrypt_mobile_provider(
    db: Session,
    envelope: MobileProviderEnvelope,
    *,
    device_id: str,
    project_id: str,
) -> ModelProviderConfig:
    identity = db.query(GatewayIdentity).order_by(GatewayIdentity.created_at).first()
    if identity is None:
        raise ValidationError("Gateway 身份尚未初始化，请重新配对")

    try:
        ephemeral_public = X25519PublicKey.from_public_bytes(
            _decode(envelope.ephemeral_public_key, expected=32)
        )
        shared_secret = gateway_encryption_private_key(identity).exchange(ephemeral_public)
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=_ENVELOPE_INFO,
        ).derive(shared_secret)
        nonce = _decode(envelope.nonce, expected=12)
        ciphertext = _decode(envelope.ciphertext)
        associated_data = f"siming-mobile-provider-v1:{device_id}:{project_id}".encode()
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, associated_data)
        raw = json.loads(plaintext)
        credentials = _MobileProviderCredentials.model_validate(raw)
    except (
        InvalidTag,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        PydanticValidationError,
    ) as exc:
        raise ValidationError("手机模型凭据无法解密或已损坏，请重新发起请求") from exc

    if abs(int(time.time() * 1000) - credentials.issued_at) > _MAX_CLOCK_SKEW_MS:
        raise ValidationError("手机模型凭据请求已过期，请校准手机时间后重试")
    return ModelProviderConfig(
        provider="mobile_openai",
        default_model=credentials.model,
        api_key=credentials.api_key,
        base_url=_validated_base_url(credentials.base_url),
        api_protocol=credentials.protocol,
        provider_type="ephemeral_mobile",
        context_window_tokens=credentials.context_window_tokens,
        max_output_tokens=credentials.max_output_tokens,
        safety_margin_tokens=credentials.safety_margin_tokens,
        capacity_assurance=credentials.capacity_assurance,
    )


__all__ = ["decrypt_mobile_provider"]
