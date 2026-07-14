"""Tests for the zero-command-line OpenCode onboarding flow."""
from __future__ import annotations

import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import APIConfig, Base
from app.routers.getting_started import (
    OpenCodeConfigureRequest,
    configure_opencode,
    get_getting_started_status,
)
from app.schemas.config import ConnectionTestRequest
from app.services import opencode_onboarding


def test_free_model_detection_covers_current_opencode_labels():
    assert opencode_onboarding.is_free_opencode_model("opencode/deepseek-v4-flash-free")
    assert opencode_onboarding.is_free_opencode_model("opencode/big-pickle")
    assert not opencode_onboarding.is_free_opencode_model("opencode/minimax-m2.7")


def test_inspect_opencode_prefers_live_free_models():
    models = [
        {"id": "opencode/deepseek-v4-flash-free", "display_name": "DeepSeek V4 Flash Free"},
        {"id": "opencode/paid-model", "display_name": "Paid"},
    ]
    with patch.object(opencode_onboarding, "resolve_opencode_command", return_value=r"C:\tools\opencode.exe"), patch.object(
        opencode_onboarding, "_command_version", return_value="1.17.20"
    ), patch.object(opencode_onboarding, "discover_local_cli_models", return_value=models):
        status = opencode_onboarding.inspect_opencode()

    assert status["installed"] is True
    assert status["model_source"] == "cli"
    assert [item["id"] for item in status["free_models"]] == ["opencode/deepseek-v4-flash-free"]
    assert status["recommended_model"] == "opencode/deepseek-v4-flash-free"


def test_inspect_opencode_caches_cli_probes_until_refresh():
    models = [{"id": "opencode/deepseek-v4-flash-free", "display_name": "Free"}]
    opencode_onboarding.clear_opencode_inspection_cache()
    with patch.object(opencode_onboarding, "resolve_opencode_command", return_value=r"C:\tools\opencode.exe"), patch.object(
        opencode_onboarding, "_inspection_cache_key", return_value=(r"C:\tools\opencode.exe", 1)
    ), patch.object(opencode_onboarding, "_command_version", return_value="1.17.20") as version_probe, patch.object(
        opencode_onboarding, "discover_local_cli_models", return_value=models
    ) as model_probe:
        opencode_onboarding.inspect_opencode()
        opencode_onboarding.inspect_opencode()
        opencode_onboarding.inspect_opencode(refresh=True)

    assert version_probe.call_count == 2
    assert model_probe.call_count == 2


def test_extract_opencode_uses_only_expected_executable():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        archive = root / "opencode.zip"
        destination = root / "managed" / "opencode.exe"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("nested/opencode.exe", b"official-binary")
            output.writestr("../unrelated.exe", b"ignore-me")

        opencode_onboarding._extract_opencode(archive, destination)

        assert destination.read_bytes() == b"official-binary"
        assert not (root / "unrelated.exe").exists()


def test_configure_opencode_saves_cli_without_making_it_global_before_test():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    inspected = {
        "models": [{"id": "opencode/deepseek-v4-flash-free"}],
        "model_source": "cli",
        "installed": True,
        "command": r"C:\managed\opencode.exe",
        "free_models": [],
        "recommended_model": "opencode/deepseek-v4-flash-free",
    }
    with Session() as db, patch("app.routers.getting_started.resolve_opencode_command", return_value=inspected["command"]), patch(
        "app.routers.getting_started.inspect_opencode", return_value=inspected
    ), patch("app.routers.getting_started.auto_configure_mcp_for_provider", return_value={"status": "configured"}):
        result = configure_opencode(
            OpenCodeConfigureRequest(model="opencode/deepseek-v4-flash-free"),
            db,
        )
        saved = db.query(APIConfig).filter(APIConfig.provider == "opencode_cli").one()

    assert result.data["model"] == "opencode/deepseek-v4-flash-free"
    assert saved.provider_type == "local_cli"
    assert saved.is_global_default is False
    assert saved.cli_command == inspected["command"]


def test_summary_status_does_not_launch_cli_probes():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db, patch("app.routers.getting_started.inspect_opencode") as inspect_probe:
        result = get_getting_started_status(summary=True, db=db)

    inspect_probe.assert_not_called()
    assert result.data["needs_setup"] is True
    assert result.data["free_models"] == []


def test_onboarding_connection_test_can_request_a_shorter_timeout():
    payload = ConnectionTestRequest(
        provider="opencode_cli",
        model="opencode/deepseek-v4-flash-free",
        timeout_seconds=60,
    )
    assert payload.timeout_seconds == 60
    with pytest.raises(PydanticValidationError):
        ConnectionTestRequest(provider="opencode_cli", timeout_seconds=10)
