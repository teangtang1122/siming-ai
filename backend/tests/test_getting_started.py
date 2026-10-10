"""Quick Start only projects verified configurations; all setup uses model settings."""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import APIConfig, Base
from app.database.session import get_db
from app.modules.model_runtime.infrastructure.getting_started import SqlAlchemyGettingStartedConfiguration
from app.routers.getting_started import get_getting_started_status, router


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        "app.routers.getting_started.get_getting_started_configuration",
        lambda: SqlAlchemyGettingStartedConfiguration(),
    )
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def add_config(db, provider="opencode_cli", model="vendor/selected-model", status="unverified", **values):
    config = APIConfig(provider=provider, default_model=model, api_key_encrypted="test-only",
                       readiness_status=status, **values)
    db.add(config)
    db.commit()
    return config


def test_empty_status_is_provider_neutral_and_does_not_launch_discovery(db):
    with patch("app.ai.local_cli_adapter.discover_local_cli_models") as discover:
        result = get_getting_started_status(db).data
    discover.assert_not_called()
    assert result == {
        "has_any_model": False, "has_usable_models": False, "needs_setup": True,
        "global_model": None, "available_model": None,
    }
    assert db.query(APIConfig).count() == 0


@pytest.mark.parametrize("status", ["detected", "unverified", "auth_required", "quota_limited", "unavailable"])
def test_saved_or_discovered_model_is_not_treated_as_ready(db, status):
    add_config(db, status=status)
    result = get_getting_started_status(db).data
    assert result["has_any_model"] is True
    assert result["has_usable_models"] is False
    assert result["needs_setup"] is True


@pytest.mark.parametrize("provider", ["deepseek", "opencode_cli", "local_llama_cpp"])
def test_verified_models_share_one_readiness_contract(db, provider):
    config = add_config(db, provider=provider, status="ready", is_global_default=True)
    with patch("app.modules.model_runtime.infrastructure.getting_started.local_runtime_disabled", return_value=False):
        result = get_getting_started_status(db).data
    assert result["needs_setup"] is False
    assert result["global_model"] == {"provider": provider, "model": config.default_model}
    assert result["available_model"] == result["global_model"]
    assert config.default_model == "vendor/selected-model"


def test_failed_default_does_not_hide_another_verified_model_or_change_default(db):
    failed = add_config(db, status="unavailable", is_global_default=True)
    ready = add_config(db, provider="deepseek", status="ready")
    result = get_getting_started_status(db).data
    assert result["global_model"] is None
    assert result["available_model"] == {"provider": ready.provider, "model": ready.default_model}
    assert result["needs_setup"] is False
    assert failed.is_global_default is True and ready.is_global_default is False


def test_disabled_local_runtime_does_not_unlock_creation(db):
    add_config(db, provider="local_llama_cpp", status="ready", is_global_default=True)
    with patch("app.modules.model_runtime.infrastructure.getting_started.local_runtime_disabled", return_value=True):
        result = get_getting_started_status(db).data
    assert result["needs_setup"] is True
    assert result["global_model"] is None


def test_retired_free_activation_endpoints_are_no_longer_exposed(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    for path in ["activate", "install", "configure", "jobs/old-job/retry", "mcp/configure"]:
        assert client.post(f"/config/getting-started/opencode/{path}", json={}).status_code == 404


def test_new_database_does_not_create_free_activation_jobs(db):
    assert "opencode_activation_jobs" not in Base.metadata.tables
