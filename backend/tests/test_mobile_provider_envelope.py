"""Request-only Android model credentials use authenticated E2E encryption."""

from __future__ import annotations

import base64
import json
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.core.crypto as crypto
from app.bootstrap.app_factory import create_app
from app.core.config import get_settings
from app.core.exceptions import NotFoundError, ValidationError
from app.database.session import Base, get_db
from app.modules.gateway.infrastructure.mobile_provider_crypto import gateway_encryption_public_key
from app.modules.gateway.infrastructure.service import GatewayService
from app.modules.gateway.interfaces.contracts import DeviceCapabilities, PairingCompleteRequest
from app.modules.model_runtime.application.request_capacity import active_request_capacity
from app.modules.model_runtime.application.request_override import (
    active_request_provider,
    use_request_provider,
)
from app.modules.model_runtime.application.runtime import ModelRuntime, get_model_runtime
from app.modules.model_runtime.domain.configuration import ModelProviderConfig
from app.modules.model_runtime.infrastructure.gateway import LLMGateway
from app.modules.operations.infrastructure import runtime as operation_runtime_module
from app.modules.story.infrastructure.entities import Project
from app.routers.novel_creation import (
    NovelCreationStageRunRequest,
    _resolve_mobile_creation_provider,
)
from app.schemas.ai_writer import MobileProviderEnvelope
from app.services.mobile_provider_envelope import decrypt_mobile_provider
from app.services.workspace import assistant_stream_runtime as assistant_stream_runtime_module

INFO = b"siming-mobile-provider-envelope-v1"


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _seal(
    public_key: str,
    *,
    device_id: str,
    project_id: str,
    issued_at: int,
    include_capacity: bool = True,
    context_window_tokens: int = 128_000,
    max_output_tokens: int = 8_192,
    safety_margin_tokens: int = 4_096,
    capacity_assurance: str = "conservative",
) -> MobileProviderEnvelope:
    ephemeral = X25519PrivateKey.generate()
    peer = X25519PublicKey.from_public_bytes(_decode(public_key))
    shared = ephemeral.exchange(peer)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=INFO).derive(shared)
    nonce = b"0123456789ab"
    credentials = {
        "base_url": "https://8.8.8.8/v1",
        "api_key": "phone-secret-key",
        "model": "gpt-compatible-model",
        "protocol": "chat_completions",
        "issued_at": issued_at,
    }
    if include_capacity:
        credentials.update(
            {
                "context_window_tokens": context_window_tokens,
                "max_output_tokens": max_output_tokens,
                "safety_margin_tokens": safety_margin_tokens,
                "capacity_assurance": capacity_assurance,
            }
        )
    plaintext = json.dumps(credentials).encode()
    aad = f"siming-mobile-provider-v1:{device_id}:{project_id}".encode()
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    ephemeral_public = ephemeral.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return MobileProviderEnvelope(
        ephemeral_public_key=_encode(ephemeral_public),
        nonce=_encode(nonce),
        ciphertext=_encode(ciphertext),
    )


def test_mobile_provider_envelope_decrypts_without_persisting_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMING_HOME", str(tmp_path / "runtime"))
    monkeypatch.setattr(crypto, "_fernet", None)
    engine = create_engine(f"sqlite:///{tmp_path / 'mobile-provider.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    try:
        with Session() as db:
            identity = GatewayService(db).ensure_identity()
            envelope = _seal(
                gateway_encryption_public_key(identity),
                device_id="android-device",
                project_id="project-1",
                issued_at=int(time.time() * 1000),
            )
            config = decrypt_mobile_provider(
                db,
                envelope,
                device_id="android-device",
                project_id="project-1",
            )

            assert config == ModelProviderConfig(
                provider="mobile_openai",
                default_model="gpt-compatible-model",
                api_key="phone-secret-key",
                base_url="https://8.8.8.8/v1",
                api_protocol="chat_completions",
                provider_type="ephemeral_mobile",
                context_window_tokens=128_000,
                max_output_tokens=8_192,
                safety_margin_tokens=4_096,
                capacity_assurance="conservative",
            )
            fallback = decrypt_mobile_provider(
                db,
                _seal(
                    gateway_encryption_public_key(identity),
                    device_id="android-device",
                    project_id="project-1",
                    issued_at=int(time.time() * 1000),
                    context_window_tokens=256_000,
                    capacity_assurance="unverified",
                ),
                device_id="android-device",
                project_id="project-1",
            )
            assert fallback.context_window_tokens == 256_000
            assert fallback.capacity_assurance == "unverified"
            assert "phone-secret-key" not in identity.private_key_encrypted
    finally:
        engine.dispose()


def test_mobile_provider_envelope_rejects_tamper_replay_and_wrong_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMING_HOME", str(tmp_path / "runtime"))
    monkeypatch.setattr(crypto, "_fernet", None)
    engine = create_engine(f"sqlite:///{tmp_path / 'mobile-provider-invalid.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    try:
        with Session() as db:
            identity = GatewayService(db).ensure_identity()
            public_key = gateway_encryption_public_key(identity)
            valid = _seal(
                public_key,
                device_id="android-device",
                project_id="project-1",
                issued_at=int(time.time() * 1000),
            )

            ciphertext = bytearray(_decode(valid.ciphertext))
            ciphertext[-1] ^= 1
            tampered = valid.model_copy(update={"ciphertext": _encode(bytes(ciphertext))})
            with pytest.raises(ValidationError, match="无法解密|损坏"):
                decrypt_mobile_provider(
                    db,
                    tampered,
                    device_id="android-device",
                    project_id="project-1",
                )

            with pytest.raises(ValidationError, match="无法解密|损坏"):
                decrypt_mobile_provider(
                    db,
                    valid,
                    device_id="another-device",
                    project_id="project-1",
                )

            expired = _seal(
                public_key,
                device_id="android-device",
                project_id="project-1",
                issued_at=int(time.time() * 1000) - 10 * 60 * 1000,
            )
            with pytest.raises(ValidationError, match="过期"):
                decrypt_mobile_provider(
                    db,
                    expired,
                    device_id="android-device",
                    project_id="project-1",
                )

            missing_capacity = _seal(
                public_key,
                device_id="android-device",
                project_id="project-1",
                issued_at=int(time.time() * 1000),
                include_capacity=False,
            )
            with pytest.raises(ValidationError, match="无法解密|损坏"):
                decrypt_mobile_provider(
                    db,
                    missing_capacity,
                    device_id="android-device",
                    project_id="project-1",
                )

            invalid_capacity = _seal(
                public_key,
                device_id="android-device",
                project_id="project-1",
                issued_at=int(time.time() * 1000),
                context_window_tokens=8_192,
                max_output_tokens=6_000,
                safety_margin_tokens=3_000,
            )
            with pytest.raises(ValidationError, match="无法解密|损坏"):
                decrypt_mobile_provider(
                    db,
                    invalid_capacity,
                    device_id="android-device",
                    project_id="project-1",
                )
    finally:
        engine.dispose()


def test_novel_creation_binds_mobile_key_to_the_creation_session(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMING_HOME", str(tmp_path / "runtime"))
    monkeypatch.setattr(crypto, "_fernet", None)
    engine = create_engine(f"sqlite:///{tmp_path / 'mobile-creation-provider.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    try:
        with Session() as db:
            identity = GatewayService(db).ensure_identity()
            envelope = _seal(
                gateway_encryption_public_key(identity),
                device_id="android-device",
                project_id="creation-session-1",
                issued_at=int(time.time() * 1000),
            )
            payload = NovelCreationStageRunRequest(
                stage="concepts",
                model_route="mobile",
                mobile_provider=envelope,
            )
            request = SimpleNamespace(
                state=SimpleNamespace(
                    gateway_device_id="android-device",
                    gateway_device_platform="android",
                )
            )

            provider = _resolve_mobile_creation_provider(
                db,
                payload,
                request,
                binding_id="creation-session-1",
            )

            assert provider.api_key == "phone-secret-key"
            assert payload.model == "mobile_openai:gpt-compatible-model"
            assert payload.mobile_provider is None
            assert "phone-secret-key" not in payload.model_dump_json()
    finally:
        engine.dispose()


class _EmptyConfigurations:
    def provider(self, provider: str):
        return None

    def global_default(self):
        return None

    def ready_providers(self):
        return []

    def task_setting(self, task_type: str):
        return None


def test_request_provider_override_is_context_scoped_and_non_persistent():
    runtime = ModelRuntime(_EmptyConfigurations())
    ephemeral = ModelProviderConfig(
        provider="mobile_openai",
        default_model="phone-model",
        api_key="phone-secret-key",
        base_url="https://8.8.8.8/v1",
        context_window_tokens=128_000,
        max_output_tokens=8_192,
        safety_margin_tokens=4_096,
    )

    with use_request_provider(ephemeral):
        assert runtime.provider_config("mobile_openai") is ephemeral
        assert active_request_capacity("mobile_openai", "phone-model") is not None
        assert active_request_capacity("mobile_openai", "another-model") is None

    with pytest.raises(NotFoundError):
        runtime.provider_config("mobile_openai")
    assert active_request_capacity("mobile_openai", "phone-model") is None


def test_request_provider_capacity_preserves_unverified_fallback_assurance():
    ephemeral = ModelProviderConfig(
        provider="mobile_openai",
        default_model="phone-model",
        api_key="phone-secret-key",
        base_url="https://8.8.8.8/v1",
        context_window_tokens=256_000,
        max_output_tokens=8_192,
        safety_margin_tokens=4_096,
        capacity_assurance="unverified",
    )

    with use_request_provider(ephemeral):
        capacity = active_request_capacity("mobile_openai", "phone-model")
        assert capacity is not None
        assert capacity.context_window_tokens == 256_000
        assert capacity.known is False


def test_gateway_mobile_key_uses_one_workspace_model_path_without_hidden_calls(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SIMING_RUNTIME_PROFILE", "gateway")
    monkeypatch.setenv("SIMING_HOME", str(tmp_path / "runtime"))
    monkeypatch.setattr(crypto, "_fernet", None)
    get_settings.cache_clear()
    engine = create_engine(
        f"sqlite:///{tmp_path / 'mobile-provider-route.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as db:
        service = GatewayService(db)
        started = service.start_pairing(
            gateway_url="http://192.168.1.20:8000",
            created_from="127.0.0.1",
        )
        pairing_request = PairingCompleteRequest(
            pairing_id=started.pairing_id,
            pairing_secret=started.pairing_secret,
            device_name="手机 Key 集成测试",
            platform="android",
            public_key="android-public-key",
            capabilities=DeviceCapabilities(),
        )
        service.complete_pairing(pairing_request)
        service.approve_pairing(started.pairing_id)
        completed = service.complete_pairing(pairing_request)
        assert completed.device_id is not None
        assert completed.tokens is not None
        project = Project(title="手机 Key 全流程")
        db.add(project)
        db.commit()
        service.enable_project(project.id)
        project_id = project.id
        device_id = completed.device_id
        access_token = completed.tokens.access_token
        envelope = _seal(
            started.gateway_encryption_public_key,
            device_id=device_id,
            project_id=project_id,
            issued_at=int(time.time() * 1000),
        )

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    import app.database.session as session_module

    monkeypatch.setattr(session_module, "SessionLocal", Session)
    monkeypatch.setattr(operation_runtime_module, "SessionLocal", Session)
    monkeypatch.setattr(assistant_stream_runtime_module, "SessionLocal", Session)
    observed: dict[str, ModelProviderConfig] = {}

    async def fake_stream_chat_completion_with_tools(**kwargs):
        observed["workspace"] = get_model_runtime().provider_config("mobile_openai")
        assert kwargs["model"] == "mobile_openai:gpt-compatible-model"
        yield {"type": "content_delta", "delta": "手机 Key 已按 PC 工作区流程执行。"}
        yield {
            "type": "done",
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        }

    async def fake_chat_completion(**kwargs):
        raise AssertionError("工作区回合结束后不得再隐藏调用模型抽取记忆")

    async def fake_stream_chat_completion(**kwargs):
        raise AssertionError("原生工具路径不得静默回退到普通文本流")
        if False:
            yield ""

    monkeypatch.setattr(
        LLMGateway,
        "stream_chat_completion_with_tools",
        fake_stream_chat_completion_with_tools,
    )
    monkeypatch.setattr(LLMGateway, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(LLMGateway, "chat_completion", fake_chat_completion)
    app = create_app(run_startup=False)
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app, client=("203.0.113.9", 41000)) as client:
            response = client.post(
                f"/api/v1/projects/{project_id}/ai/workspace-assistant/stream",
                headers={"authorization": f"Bearer {access_token}"},
                json={
                    "message": "检查手机 Key 是否贯穿完整 PC 工作区流程",
                    "model_route": "mobile",
                    "mobile_provider": envelope.model_dump(),
                },
            )
        assert response.status_code == 200
        assert "手机 Key 已按 PC 工作区流程执行" in response.text
        assert "phone-secret-key" not in response.text
        assert observed["workspace"].api_key == "phone-secret-key"
        assert set(observed) == {"workspace"}
        assert active_request_provider() is None
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        get_settings.cache_clear()
