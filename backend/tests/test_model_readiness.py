"""Tests for the detected-versus-usable model contract."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.crypto import encrypt
from app.core.exceptions import LLMError
from app.core.response import ApiResponse
from app.database.models import APIConfig, Base
from app.routers import config as config_router
from app.services.model_readiness import (
    mark_model_ready,
    mark_model_verification_failure,
    repair_runtime_readiness_demotions,
    readiness_payload,
    sanitize_readiness_message,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_only_ready_configs_are_usable_and_messages_are_safe():
    config = APIConfig(
        provider="openai",
        api_key_encrypted=encrypt("secret"),
        default_model="gpt-test",
        readiness_status="detected",
        readiness_json='{"source":"auto_detect"}',
    )
    assert readiness_payload(config)["is_usable"] is False

    mark_model_ready(config, source="test")
    assert readiness_payload(config)["is_usable"] is True
    assert config.last_tested_at is not None

    assert "sk-secret-value" not in sanitize_readiness_message("API key=sk-secret-value")
    assert "[redacted]" in sanitize_readiness_message("API key=sk-secret-value")


def test_failure_class_updates_readiness_and_revokes_global_default():
    config = APIConfig(
        provider="opencode_cli",
        api_key_encrypted=encrypt("__local_cli__"),
        default_model="opencode/free",
        is_global_default=True,
        readiness_status="ready",
    )

    changed = mark_model_verification_failure(
        config,
        LLMError("Free usage exceeded, retrying later"),
        source="test",
    )

    assert changed is True
    assert config.readiness_status == "quota_limited"
    assert config.is_global_default is False
    assert readiness_payload(config)["failure_class"] == "quota_or_rate_limit"


def test_startup_repairs_only_historical_runtime_demotions():
    db = _session()
    runtime_demoted = APIConfig(
        provider="opencode_cli",
        api_key_encrypted=encrypt("__local_cli__"),
        default_model="opencode/big-pickle",
        provider_type="local_cli",
        readiness_status="unavailable",
        readiness_json=(
            '{"source":"gateway","failure_class":"timeout",'
            '"message":"本机 CLI 请求超时（91秒）",'
            '"last_success_at":"2026-08-22T04:00:00"}'
        ),
        is_global_default=False,
    )
    explicitly_rejected = APIConfig(
        provider="openai",
        api_key_encrypted=encrypt("secret"),
        default_model="gpt-test",
        readiness_status="unavailable",
        readiness_json=(
            '{"source":"manual_verify","failure_class":"network",'
            '"message":"连接测试失败"}'
        ),
        is_global_default=False,
    )
    db.add_all([runtime_demoted, explicitly_rejected])
    db.commit()

    assert repair_runtime_readiness_demotions(db) == 1
    db.commit()
    db.refresh(runtime_demoted)
    db.refresh(explicitly_rejected)

    assert runtime_demoted.readiness_status == "ready"
    assert runtime_demoted.is_global_default is True
    assert "runtime_failure_repair" in (runtime_demoted.readiness_json or "")
    assert explicitly_rejected.readiness_status == "unavailable"
    assert explicitly_rejected.is_global_default is False
    assert repair_runtime_readiness_demotions(db) == 0


def test_saved_config_verification_marks_ready_and_assigns_first_global():
    db = _session()
    config = APIConfig(
        provider="claude_cli",
        api_key_encrypted=encrypt("__local_cli__"),
        default_model="claude-code",
        provider_type="local_cli",
        cli_command="claude",
        cli_args='["-p","{prompt}"]',
        readiness_status="detected",
        readiness_json='{"source":"auto_detect"}',
    )
    db.add(config)
    db.commit()

    with patch.object(
        config_router,
        "test_connection",
        new=AsyncMock(return_value=ApiResponse.success(data={"reply": "连接成功"})),
    ):
        response = asyncio.run(config_router.verify_saved_model_config("claude_cli", db))

    db.refresh(config)
    assert config.readiness_status == "ready"
    assert config.is_global_default is True
    assert response.data["became_global_default"] is True


def test_saved_custom_api_verification_persists_detected_responses_protocol():
    db = _session()
    config = APIConfig(
        provider="yls",
        api_key_encrypted=encrypt("sk-test"),
        default_model="gpt-test",
        provider_type="api",
        base_url_override="https://proxy.example/codex/",
        api_protocol="auto",
        readiness_status="unverified",
    )
    db.add(config)
    db.commit()

    with patch.object(
        config_router,
        "test_connection",
        new=AsyncMock(return_value=ApiResponse.success(data={
            "reply": "OK",
            "api_protocol": "responses",
            "base_url": "https://proxy.example/codex",
        })),
    ):
        response = asyncio.run(config_router.verify_saved_model_config("yls", db))

    db.refresh(config)
    assert config.api_protocol == "responses"
    assert config.base_url_override == "https://proxy.example/codex"
    assert config.readiness_status == "ready"
    assert response.data["test"]["api_protocol"] == "responses"


def test_opencode_verification_retains_capacity_for_first_real_creation_turn():
    db = _session()
    config = APIConfig(
        provider="opencode_cli",
        api_key_encrypted=encrypt("__local_cli__"),
        default_model="opencode/big-pickle",
        provider_type="local_cli",
        cli_command="opencode",
        readiness_status="unverified",
    )
    db.add(config)
    db.commit()
    verification = AsyncMock()
    verification.list_models.return_value = [{
        "id": "opencode/big-pickle", "display_name": "Big Pickle",
        "context_window_tokens": 200000, "max_output_tokens": 32000,
        "safety_margin_tokens": 512, "capacity_source": "opencode_cli_metadata",
    }]
    with patch.object(
        config_router, "test_connection",
        new=AsyncMock(return_value=ApiResponse.success(data={"reply": "连接成功"})),
    ), patch.object(config_router, "get_model_verification", return_value=verification):
        response = asyncio.run(config_router.verify_saved_model_config("opencode_cli", db))

    db.refresh(config)
    assert config.readiness_status == "ready"
    assert response.data["config"]["context_profile_known"] is True
    assert response.data["config"]["context_window_tokens"] == 200000
    assert response.data["config"]["context_profile_source"] == "opencode_cli_metadata"
    assert config.available_models_json[0]["max_output_tokens"] == 32000
    request = verification.list_models.await_args.args[0]
    assert request.cli_command == config.cli_command


def test_free_opencode_activation_persists_the_same_discovered_capacity():
    from app.services import opencode_activation

    db = _session()
    models = [{
        "id": "opencode/big-pickle", "context_window_tokens": 200000,
        "max_output_tokens": 32000, "capacity_source": "opencode_cli_metadata",
    }]
    with patch(
        "app.database.session.SessionLocal", sessionmaker(bind=db.get_bind()),
    ), patch.object(opencode_activation, "local_cli_model_options", return_value=models):
        opencode_activation.save_activated_config("opencode", "opencode/big-pickle")

    config = db.query(APIConfig).filter_by(provider="opencode_cli").one()
    data = config_router._config_payload(config, db=db)
    assert data["is_usable"] is True
    assert data["context_profile_known"] is True
    assert data["context_window_tokens"] == 200000
    assert data["context_profile_source"] == "opencode_cli_metadata"


def test_saved_config_verification_persists_quota_failure():
    db = _session()
    config = APIConfig(
        provider="opencode_cli",
        api_key_encrypted=encrypt("__local_cli__"),
        default_model="opencode/free",
        provider_type="local_cli",
        cli_command="opencode",
        readiness_status="unverified",
    )
    db.add(config)
    db.commit()

    with patch.object(
        config_router,
        "test_connection",
        new=AsyncMock(side_effect=LLMError("Free usage exceeded, retrying in 9h")),
    ), pytest.raises(LLMError):
        asyncio.run(config_router.verify_saved_model_config("opencode_cli", db))

    db.refresh(config)
    assert config.readiness_status == "quota_limited"
    assert config.is_global_default is False


def test_saved_config_verification_never_leaves_a_connection_failure_testing():
    db = _session()
    config = APIConfig(
        provider="openai",
        api_key_encrypted=encrypt("sk-test"),
        default_model="gpt-test",
        readiness_status="unverified",
    )
    db.add(config)
    db.commit()

    with patch.object(
        config_router,
        "test_connection",
        new=AsyncMock(side_effect=LLMError("Cannot connect to OpenAI")),
    ), pytest.raises(LLMError):
        asyncio.run(config_router.verify_saved_model_config("openai", db))

    db.refresh(config)
    assert config.readiness_status == "unavailable"
    assert readiness_payload(config)["failure_class"] == "network"
