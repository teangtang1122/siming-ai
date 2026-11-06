"""Retiring an unused setting must not migrate actual encryption identities."""

from __future__ import annotations

import base64
import hashlib
import sys

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core import crypto
from app.core.config import Settings, get_settings
from app.modules.gateway.infrastructure.mobile_provider_crypto import gateway_encryption_public_key
from app.modules.gateway.infrastructure.models import GatewayIdentity
from app.modules.gateway.infrastructure.service import GatewayService


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in (
        "DATABASE_URL", "SECRET_KEY", "CORS_ORIGINS", "SIMING_RUNTIME_PROFILE",
        "RUNTIME_PROFILE", "SIMING_GATEWAY_BOOTSTRAP_KEY", "GATEWAY_BOOTSTRAP_KEY",
        "SIMING_KEY", "MOSHU_KEY", "NOVEL_AGENT_KEY",
        "SIMING_KEY_FILE", "MOSHU_KEY_FILE", "NOVEL_AGENT_KEY_FILE",
        "SIMING_HOME", "MOSHU_HOME", "NOVEL_AGENT_HOME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(crypto, "_fernet", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("legacy_value", ["change-me-in-production", "user-configured-old-value", ""])
def test_retired_secret_is_ignored_without_changing_settings_sources(
    tmp_path, monkeypatch, isolated_settings, frozen, legacy_value,
):
    (tmp_path / ".env").write_text(
        f"SECRET_KEY={legacy_value}\n"
        "DATABASE_URL=sqlite:///./dotenv.db\n"
        "CORS_ORIGINS=https://dotenv.example\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setenv("SIMING_GATEWAY_BOOTSTRAP_KEY", "separate-bootstrap-value")
    settings = get_settings()
    assert "secret_key" not in Settings.model_fields
    assert "secret_key" not in settings.model_dump()
    assert not hasattr(settings, "secret_key")
    assert settings.database_url == (
        "sqlite:///./novel_agent.db" if frozen else "sqlite:///./dotenv.db"
    )
    assert settings.cors_origins == (
        "http://localhost:5173" if frozen else "https://dotenv.example"
    )
    assert settings.gateway_bootstrap_key == "separate-bootstrap-value"

    monkeypatch.setenv("SECRET_KEY", legacy_value)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./process.db")
    get_settings.cache_clear()
    settings = get_settings()
    assert "secret_key" not in settings.model_dump()
    assert settings.database_url == "sqlite:///./process.db"
    assert settings.gateway_bootstrap_key == "separate-bootstrap-value"


@pytest.mark.parametrize("frozen", [False, True])
def test_existing_ciphertext_and_gateway_identity_survive_retirement(
    tmp_path, monkeypatch, isolated_settings, frozen,
):
    # Prepare an existing installation independently of application key creation.
    key = Fernet.generate_key()
    old_fernet = Fernet(key)
    key_file = tmp_path / ".crypto_key"
    key_file.write_bytes(key)
    ciphertext = old_fernet.encrypt(b"existing-provider-credential").decode()
    signing_key = Ed25519PrivateKey.generate()
    seed = signing_key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_key = base64.urlsafe_b64encode(signing_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )).decode()
    fingerprint = hashlib.sha256(base64.urlsafe_b64decode(public_key)).hexdigest()
    encrypted_seed = old_fernet.encrypt(base64.urlsafe_b64encode(seed)).decode()
    database_url = f"sqlite:///{tmp_path / 'existing.db'}"
    engine = create_engine(database_url)
    GatewayIdentity.__table__.create(engine)
    with Session(engine) as session:
        identity = GatewayIdentity(
            instance_id="existing-installation", display_name="Existing Gateway",
            public_key=public_key, private_key_encrypted=encrypted_seed,
            fingerprint=fingerprint,
        )
        session.add(identity)
        session.commit()
        identity_id = identity.id
    engine.dispose()

    (tmp_path / ".env").write_text("SECRET_KEY=change-me-in-production\n", encoding="utf-8")
    monkeypatch.setenv("SIMING_KEY_FILE", str(key_file))
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    original_files = set(tmp_path.iterdir())
    encryption_public_key = None
    for legacy_value in ("change-me-in-production", "different-unused-value"):
        monkeypatch.setenv("SECRET_KEY", legacy_value)
        monkeypatch.setattr(crypto, "_fernet", None)
        get_settings.cache_clear()
        assert "secret_key" not in get_settings().model_dump()
        assert crypto.decrypt(ciphertext) == "existing-provider-credential"
        engine = create_engine(database_url)
        try:
            with Session(engine) as session:
                service = GatewayService(session)
                identity = service.ensure_identity()
                assert identity.id == identity_id
                assert identity.instance_id == "existing-installation"
                assert identity.public_key == public_key
                assert identity.fingerprint == fingerprint
                assert identity.private_key_encrypted == encrypted_seed
                derived_public_key = gateway_encryption_public_key(identity)
                if encryption_public_key is None:
                    encryption_public_key = derived_public_key
                assert derived_public_key == encryption_public_key
                payload = {"upgrade": "preserved"}
                signature = base64.urlsafe_b64decode(service._sign_pairing_payload(identity, payload))
                signing_key.public_key().verify(signature, b'{"upgrade":"preserved"}')
        finally:
            engine.dispose()
        assert key_file.read_bytes() == key
        assert set(tmp_path.iterdir()) == original_files

