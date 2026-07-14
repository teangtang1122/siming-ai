"""Tests for the zero-command-line OpenCode onboarding flow."""
from __future__ import annotations

import hashlib
import threading
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, call, patch

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import APIConfig, Base, OpenCodeActivationJob
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


def test_mirror_candidates_keep_official_first_and_require_https(monkeypatch):
    monkeypatch.setenv(
        "SIMING_OPENCODE_MIRROR_URLS",
        "https://mirror.example/{asset};http://unsafe.example/{asset};https://proxy.example/{url}",
    )
    urls = opencode_onboarding._mirror_urls(
        "https://github.com/anomalyco/opencode/releases/download/v1/opencode.zip",
        "opencode.zip",
    )
    assert urls == [
        "https://github.com/anomalyco/opencode/releases/download/v1/opencode.zip",
        "https://mirror.example/opencode.zip",
        "https://proxy.example/https://github.com/anomalyco/opencode/releases/download/v1/opencode.zip",
    ]


def test_resumable_download_reuses_a_complete_verified_partial_file():
    content = b"already downloaded and verified"
    expected = hashlib.sha256(content).hexdigest()
    progress = []
    with TemporaryDirectory() as temporary_dir:
        destination = Path(temporary_dir) / "opencode.zip.part"
        destination.write_bytes(content)
        with patch.object(opencode_onboarding, "urlopen") as open_url:
            opencode_onboarding._download_asset_resumable(
                "https://example.invalid/opencode.zip",
                destination,
                expected_sha256=expected,
                progress=lambda downloaded, total: progress.append((downloaded, total)),
            )

    open_url.assert_not_called()
    assert progress == [(len(content), len(content))]


def test_concurrent_activation_requests_share_one_persistent_job():
    class DeferredWorker:
        starts = 0

        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            type(self).starts += 1

    with TemporaryDirectory() as temporary_dir:
        database_path = Path(temporary_dir) / "activation.db"
        engine = create_engine(
            f"sqlite:///{database_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        barrier = threading.Barrier(3)
        results = []
        errors = []

        def activate():
            barrier.wait()
            try:
                results.append(opencode_onboarding.start_opencode_activation())
            except Exception as exc:  # pragma: no cover - assertion reports thread failures
                errors.append(exc)

        workers = [threading.Thread(target=activate) for _ in range(2)]
        with patch("app.database.session.SessionLocal", Session), patch.object(
            opencode_onboarding.threading, "Thread", DeferredWorker
        ):
            for worker in workers:
                worker.start()
            barrier.wait()
            for worker in workers:
                worker.join()

        with Session() as db:
            persisted_count = db.query(OpenCodeActivationJob).count()
        engine.dispose()

    assert errors == []
    assert len(results) == 2
    assert results[0]["id"] == results[1]["id"]
    assert persisted_count == 1
    assert DeferredWorker.starts == 1


def test_activation_falls_back_to_next_free_model_before_saving_config():
    job = {
        "id": "job-1",
        "command": r"C:\managed\opencode.exe",
        "preferred_model": None,
        "sha256": "a" * 64,
    }
    inspected = {
        "installed": True,
        "command": job["command"],
        "version": "1.17.20",
        "free_models": [
            {"id": "opencode/first-free", "recommended": True},
            {"id": "opencode/second-free", "recommended": False},
        ],
    }
    with patch.object(opencode_onboarding, "get_opencode_activation_job", return_value=job), patch.object(
        opencode_onboarding, "_update_activation"
    ) as update, patch.object(opencode_onboarding, "resolve_opencode_command", return_value=job["command"]), patch.object(
        opencode_onboarding, "inspect_opencode", return_value=inspected
    ), patch.object(
        opencode_onboarding,
        "_test_opencode_model",
        new=AsyncMock(side_effect=[RuntimeError("free usage quota exceeded"), None]),
    ) as test_model, patch.object(opencode_onboarding, "_save_activated_config") as save_config, patch(
        "app.services.external_agent.mcp_auto_config.auto_configure_mcp_for_provider"
    ):
        opencode_onboarding._activation_worker("job-1")

    assert test_model.await_args_list == [
        call(job["command"], "opencode/first-free"),
        call(job["command"], "opencode/second-free"),
    ]
    save_config.assert_called_once_with(job["command"], "opencode/second-free")
    assert any(item.kwargs.get("status") == "ready" for item in update.call_args_list)


def test_activation_pauses_for_official_auth_without_changing_config():
    job = {"id": "job-2", "command": r"C:\managed\opencode.exe", "preferred_model": None, "sha256": None}
    inspected = {
        "installed": True,
        "command": job["command"],
        "version": "1.17.20",
        "free_models": [{"id": "opencode/free", "recommended": True}],
    }
    with patch.object(opencode_onboarding, "get_opencode_activation_job", return_value=job), patch.object(
        opencode_onboarding, "_update_activation"
    ) as update, patch.object(opencode_onboarding, "resolve_opencode_command", return_value=job["command"]), patch.object(
        opencode_onboarding, "inspect_opencode", return_value=inspected
    ), patch.object(
        opencode_onboarding,
        "_test_opencode_model",
        new=AsyncMock(side_effect=RuntimeError("authentication required, please login")),
    ), patch.object(opencode_onboarding, "_save_activated_config") as save_config:
        opencode_onboarding._activation_worker("job-2")

    save_config.assert_not_called()
    auth_updates = [item.kwargs for item in update.call_args_list if item.kwargs.get("status") == "auth_required"]
    assert auth_updates and auth_updates[-1]["phase"] == "auth_required"
