"""Tests for the zero-command-line OpenCode onboarding flow."""
from __future__ import annotations

import hashlib
import io
import tarfile
import threading
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ValidationError
from app.database.models import APIConfig, Base, OpenCodeActivationJob
from app.routers.getting_started import (
    OpenCodeActivateRequest,
    OpenCodeConfigureRequest,
    activate_opencode,
    configure_opencode,
    get_getting_started_status,
)
from app.schemas.config import ConnectionTestRequest
from app.services import opencode_onboarding, opencode_release_catalog


def test_free_model_detection_covers_current_opencode_labels():
    assert opencode_onboarding.is_free_opencode_model("opencode/deepseek-v4-flash-free")
    assert opencode_onboarding.is_free_opencode_model("opencode/laguna-s-2.1-free")
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


def test_extract_opencode_supports_verified_official_npm_package():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        archive = root / "opencode.tgz"
        destination = root / "managed" / "opencode.exe"
        content = b"official-npm-binary"
        with tarfile.open(archive, "w:gz") as output:
            member = tarfile.TarInfo("package/bin/opencode.exe")
            member.size = len(content)
            output.addfile(member, io.BytesIO(content))
            unrelated = tarfile.TarInfo("../unrelated.exe")
            unrelated.size = 4
            output.addfile(unrelated, io.BytesIO(b"nope"))

        opencode_onboarding._extract_opencode(archive, destination, archive_format="tgz")

        assert destination.read_bytes() == content
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
    ), patch("app.services.external_agent.mcp_auto_config.auto_configure_mcp_for_provider") as auto_configure:
        result = configure_opencode(
            OpenCodeConfigureRequest(model="opencode/deepseek-v4-flash-free"),
            db,
        )
        saved = db.query(APIConfig).filter(APIConfig.provider == "opencode_cli").one()

    assert result.data["model"] == "opencode/deepseek-v4-flash-free"
    assert saved.provider_type == "local_cli"
    assert saved.is_global_default is False
    assert saved.readiness_status == "unverified"
    assert saved.cli_command == inspected["command"]
    assert "mcp_auto_setup" not in result.data
    auto_configure.assert_not_called()


def test_summary_status_does_not_launch_cli_probes():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db, patch("app.routers.getting_started.inspect_opencode") as inspect_probe:
        result = get_getting_started_status(summary=True, db=db)

    inspect_probe.assert_not_called()
    assert result.data["needs_setup"] is True
    assert result.data["has_usable_models"] is False
    assert result.data["recommended_action"] == "activate_opencode"
    assert result.data["free_models"] == []


def test_usable_model_is_a_stable_quick_start_completion_without_cli_probe():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(APIConfig(
            provider="deepseek",
            api_key_encrypted="test",
            default_model="deepseek-v4-flash",
            readiness_status="ready",
            is_global_default=False,
        ))
        db.add(OpenCodeActivationJob(
            status="running",
            phase="testing",
            message="stale activation",
        ))
        db.commit()
        with patch("app.routers.getting_started.inspect_opencode") as inspect_probe:
            result = get_getting_started_status(summary=False, db=db)

    inspect_probe.assert_not_called()
    assert result.data["needs_setup"] is False
    assert result.data["has_usable_models"] is True
    assert result.data["available_model"] == {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
    }
    assert result.data["activation_job"] is None


def test_quick_start_activation_is_rejected_when_a_model_is_already_usable():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(APIConfig(
            provider="deepseek",
            api_key_encrypted="test",
            default_model="deepseek-v4-flash",
            readiness_status="ready",
        ))
        db.commit()
        with pytest.raises(ValidationError, match="已有通过验证的可用模型"):
            activate_opencode(OpenCodeActivateRequest(), db)


def test_startup_does_not_resume_activation_when_a_model_is_already_usable():
    class UnexpectedWorker:
        def __init__(self, *args, **kwargs):
            raise AssertionError("a completed onboarding state must not start a worker")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(APIConfig(
            provider="deepseek",
            api_key_encrypted="test",
            default_model="deepseek-v4-flash",
            readiness_status="ready",
        ))
        job = OpenCodeActivationJob(
            status="running",
            phase="testing",
            message="stale activation",
        )
        db.add(job)
        db.commit()
        job_id = job.id

    with patch("app.database.session.SessionLocal", Session), patch.object(
        opencode_onboarding.threading, "Thread", UnexpectedWorker
    ):
        resumed = opencode_onboarding.resume_incomplete_opencode_activations()

    with Session() as db:
        saved = db.query(OpenCodeActivationJob).filter(
            OpenCodeActivationJob.id == job_id
        ).one()
        assert saved.status == "ready"
        assert saved.phase == "ready"
        assert saved.percent == 100
        assert saved.completed_at is not None
        assert "停止重复检测" in saved.message
    assert resumed == 0


def test_onboarding_connection_test_can_request_a_shorter_timeout():
    payload = ConnectionTestRequest(
        provider="opencode_cli",
        model="opencode/deepseek-v4-flash-free",
        timeout_seconds=60,
    )
    assert payload.timeout_seconds == 60
    with pytest.raises(PydanticValidationError):
        ConnectionTestRequest(provider="opencode_cli", timeout_seconds=10)


def test_certificate_chain_failure_has_a_distinct_actionable_classification():
    message = (
        "<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "unable to get local issuer certificate (_ssl.c:1010)>"
    )

    assert opencode_onboarding._activation_failure_kind(message) == "certificate_verification"


def test_bare_http_rate_limit_is_classified_by_activation_context():
    message = "HTTP Error 403"

    assert opencode_onboarding._activation_failure_kind(message, context="model") == "quota_or_rate_limit"
    assert opencode_onboarding._activation_failure_kind(message, context="download") == "download_rate_limit"


def test_http_429_is_not_reported_as_a_generic_network_failure():
    message = "request failed with status code 429"

    assert opencode_onboarding._activation_failure_kind(message, context="model") == "quota_or_rate_limit"


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


def test_managed_release_catalog_selects_verified_windows_binary():
    version, standard = opencode_release_catalog.managed_windows_release(
        machine="AMD64",
        avx2_supported=True,
    )
    _, baseline = opencode_release_catalog.managed_windows_release(
        machine="AMD64",
        avx2_supported=False,
    )
    _, arm64 = opencode_release_catalog.managed_windows_release(machine="ARM64")

    assert version == "v1.18.4"
    assert standard["name"] == "opencode-windows-x64.zip"
    assert baseline["name"] == "opencode-windows-x64-baseline.zip"
    assert arm64["name"] == "opencode-windows-arm64.zip"
    for asset in (standard, baseline, arm64):
        assert f"/releases/download/{version}/{asset['name']}" in asset["browser_download_url"]
        assert asset["digest"].startswith("sha256:")
        assert len(asset["digest"].removeprefix("sha256:")) == 64
        assert [source["label"] for source in asset["download_sources"]] == [
            "GitHub 官方源",
            "npm 官方源",
            "国内加速源",
        ]
        assert all(source["url"].startswith("https://") for source in asset["download_sources"])
        assert asset["download_sources"][1]["digest"].startswith("sha512:")
        assert asset["download_sources"][1]["digest"] == asset["download_sources"][2]["digest"]


def test_download_sources_include_configured_verified_mirror(monkeypatch):
    monkeypatch.setenv("SIMING_OPENCODE_MIRROR_URLS", "https://mirror.example/{asset}")
    _version, asset = opencode_release_catalog.managed_windows_release(
        machine="AMD64",
        avx2_supported=True,
    )

    sources = opencode_onboarding._download_sources(asset)

    assert [source.label for source in sources] == [
        "GitHub 官方源",
        "npm 官方源",
        "国内加速源",
        "自定义加速源 1",
    ]
    custom = sources[-1]
    assert custom.archive_format == "zip"
    assert custom.expected_digest == asset["digest"].removeprefix("sha256:")


def test_download_source_ranking_prefers_measured_throughput():
    sources = [
        opencode_onboarding._DownloadSource(
            label=label,
            url=f"https://{label}.example/opencode.zip",
            archive_format="zip",
            digest_algorithm="sha256",
            expected_digest="a" * 64,
            expected_size=100,
        )
        for label in ("slow", "fast", "offline")
    ]

    def probe(source):
        rates = {"slow": 100, "fast": 500, "offline": 0}
        return opencode_onboarding._DownloadProbe(
            source=source,
            available=source.label != "offline",
            bytes_per_second=rates[source.label],
            latency_seconds=0.1,
        )

    with patch.object(opencode_onboarding, "_probe_download_source", side_effect=probe):
        ranked = opencode_onboarding._rank_download_sources(sources)

    assert [item.source.label for item in ranked] == ["fast", "slow", "offline"]


def test_slow_download_automatically_switches_to_next_verified_source():
    content = b"verified archive"
    digest = hashlib.sha256(content).hexdigest()
    asset = {
        "name": "opencode.zip",
        "download_sources": [
            {
                "label": "慢速源",
                "url": "https://slow.example/opencode.zip",
                "archive_format": "zip",
                "size": len(content),
                "digest": f"sha256:{digest}",
            },
            {
                "label": "快速源",
                "url": "https://fast.example/opencode.zip",
                "archive_format": "zip",
                "size": len(content),
                "digest": f"sha256:{digest}",
            },
        ],
    }
    sources = opencode_onboarding._download_sources(asset)
    probes = [
        opencode_onboarding._DownloadProbe(
            source=sources[0], available=True, bytes_per_second=200
        ),
        opencode_onboarding._DownloadProbe(
            source=sources[1], available=True, bytes_per_second=100
        ),
    ]
    events = []

    def download(url, destination, *, progress, **_kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        if "slow.example" in url:
            destination.write_bytes(b"x")
            progress(1, len(content))
            return
        destination.write_bytes(content)
        progress(len(content), len(content))

    with TemporaryDirectory() as temporary, patch.object(
        opencode_onboarding,
        "_rank_download_sources",
        return_value=probes,
    ), patch.object(
        opencode_onboarding,
        "_download_asset_resumable",
        side_effect=download,
    ), patch.object(
        opencode_onboarding,
        "DOWNLOAD_SLOW_WINDOW_SECONDS",
        0,
    ), patch.object(
        opencode_onboarding,
        "DOWNLOAD_MIN_SWITCH_RATE",
        100,
    ):
        archive, source, _parts = opencode_onboarding._download_release_archive(
            Path(temporary),
            asset,
            on_event=lambda event, details: events.append((event, details)),
        )

        assert source.label == "快速源"
        assert archive.read_bytes() == content
    assert any(event == "switching" for event, _details in events)


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


def test_resumable_download_continues_partial_file_across_sources():
    content = b"verified bytes from either source"
    existing = 9
    expected = hashlib.sha256(content).hexdigest()
    progress = []

    class RangeResponse:
        status = 206
        headers = {
            "Content-Range": f"bytes {existing}-{len(content) - 1}/{len(content)}",
            "Content-Length": str(len(content) - existing),
        }

        def __init__(self):
            self.remaining = content[existing:]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read1(self, _size):
            value, self.remaining = self.remaining, b""
            return value

    def open_url(request, timeout):
        assert request.get_header("Range") == f"bytes={existing}-"
        assert timeout == 60
        return RangeResponse()

    with TemporaryDirectory() as temporary_dir:
        destination = Path(temporary_dir) / "shared-artifact.part"
        destination.write_bytes(content[:existing])
        with patch.object(opencode_onboarding, "urlopen", side_effect=open_url):
            opencode_onboarding._download_asset_resumable(
                "https://next-source.example/opencode.zip",
                destination,
                expected_digest=expected,
                expected_size=len(content),
                progress=lambda downloaded, total: progress.append((downloaded, total)),
            )

        assert destination.read_bytes() == content
    assert progress[-1] == (len(content), len(content))


def test_download_403_is_not_misreported_as_free_model_quota():
    state = {
        "id": "job-download-limit",
        "command": None,
        "preferred_model": None,
        "sha256": None,
        "phase": "checking",
    }
    updates = []

    def get_job(_job_id):
        return dict(state)

    def update_job(_job_id, **changes):
        state.update(changes)
        updates.append(dict(state))
        return dict(state)

    version, asset = opencode_release_catalog.managed_windows_release(
        machine="AMD64",
        avx2_supported=True,
    )
    with TemporaryDirectory() as temporary_dir, patch.object(
        opencode_onboarding,
        "get_opencode_activation_job",
        side_effect=get_job,
    ), patch.object(
        opencode_onboarding,
        "_update_activation",
        side_effect=update_job,
    ), patch.object(
        opencode_onboarding,
        "resolve_opencode_command",
        return_value=None,
    ), patch.object(
        opencode_onboarding,
        "inspect_opencode",
        return_value={"installed": False},
    ), patch.object(
        opencode_onboarding,
        "managed_opencode_root",
        return_value=Path(temporary_dir),
    ), patch.object(
        opencode_onboarding,
        "_latest_release_asset",
        return_value=(version, asset),
    ), patch.object(
        opencode_onboarding,
        "_download_asset_resumable",
        side_effect=RuntimeError("HTTP Error 403: rate limit exceeded"),
    ), patch.object(
        opencode_onboarding,
        "_rank_download_sources",
        side_effect=lambda sources: [
            opencode_onboarding._DownloadProbe(source=source, available=True)
            for source in sources
        ],
    ), patch.object(opencode_onboarding, "_save_activation_readiness_failure"):
        opencode_onboarding._activation_worker(state["id"])

    assert any(item.get("phase") == "checking_release" for item in updates)
    assert any(item.get("phase") == "downloading" for item in updates)
    assert state["failure_kind"] == "download_rate_limit"
    assert state["message"] == "免费写作能力暂时没有准备完成"
    assert "下载进度已保留" in state["next_action"]


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
            opencode_onboarding, "os", SimpleNamespace(name="nt")
        ), patch.object(
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
    ) as auto_configure:
        opencode_onboarding._activation_worker("job-1")

    assert test_model.await_args_list == [
        call(job["command"], "opencode/first-free"),
        call(job["command"], "opencode/second-free"),
    ]
    save_config.assert_called_once_with(job["command"], "opencode/second-free")
    auto_configure.assert_not_called()
    assert any(item.kwargs.get("status") == "ready" for item in update.call_args_list)


def test_activation_reports_all_tested_models_when_the_free_pool_is_rate_limited():
    job = {
        "id": "job-quota",
        "command": r"C:\managed\opencode.exe",
        "preferred_model": None,
        "sha256": None,
    }
    inspected = {
        "installed": True,
        "command": job["command"],
        "version": "1.17.20",
        "free_models": [
            {"id": "opencode/first-free", "display_name": "First Free", "recommended": True},
            {"id": "opencode/second-free", "display_name": "Second Free", "recommended": False},
        ],
    }
    with patch.object(opencode_onboarding, "get_opencode_activation_job", return_value=job), patch.object(
        opencode_onboarding, "_update_activation"
    ) as update, patch.object(opencode_onboarding, "resolve_opencode_command", return_value=job["command"]), patch.object(
        opencode_onboarding, "inspect_opencode", return_value=inspected
    ), patch.object(
        opencode_onboarding,
        "_test_opencode_model",
        new=AsyncMock(side_effect=RuntimeError("HTTP Error 403: rate limit exceeded")),
    ), patch.object(opencode_onboarding, "_save_activated_config") as save_config, patch.object(
        opencode_onboarding, "_save_activation_readiness_failure"
    ):
        opencode_onboarding._activation_worker("job-quota")

    save_config.assert_not_called()
    failed_updates = [item.kwargs for item in update.call_args_list if item.kwargs.get("status") == "failed"]
    assert failed_updates
    final = failed_updates[-1]
    assert final["failure_kind"] == "quota_or_rate_limit"
    assert "不是网络故障" in final["next_action"]
    assert [item["test_status"] for item in final["free_models_json"]] == ["rate_limited", "rate_limited"]


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


def test_managed_auth_opens_captured_url_and_retries_after_auth_list_verification():
    class FakeAuthProcess:
        def __init__(self):
            self.alive = True

        def isalive(self):
            return self.alive

        def read(self):
            self.alive = False
            return "Continue in your browser: https://opencode.ai/auth/device"

        def wait(self):
            return 0

        def write(self, _value):
            return None

    process = FakeAuthProcess()
    with patch.object(opencode_onboarding, "_update_activation") as update, patch.object(
        opencode_onboarding, "_auth_list_has_credentials", return_value=True
    ), patch.object(opencode_onboarding, "retry_opencode_activation") as retry, patch(
        "webbrowser.open", return_value=True
    ) as open_browser:
        opencode_onboarding._authentication_worker("job-auth", r"C:\managed\opencode.exe", process)

    open_browser.assert_called_once_with("https://opencode.ai/auth/device")
    retry.assert_called_once_with("job-auth")
    assert any(item.kwargs.get("auth_status") == "completed" for item in update.call_args_list)


def test_one_time_auth_credential_is_written_without_returning_or_logging_it():
    process = MagicMock()
    process.isalive.return_value = True
    opencode_onboarding._auth_sessions["job-secret"] = opencode_onboarding._ManagedAuthSession(process=process)
    try:
        with patch.object(
            opencode_onboarding,
            "_update_activation",
            return_value={"id": "job-secret", "auth_status": "submitted"},
        ):
            result = opencode_onboarding.submit_opencode_auth_credential("job-secret", "secret-token-value")
    finally:
        opencode_onboarding._auth_sessions.pop("job-secret", None)

    process.write.assert_called_once_with("secret-token-value\r")
    assert "secret-token-value" not in str(result)

def test_quick_start_can_explicitly_configure_and_preflight_opencode_mcp():
    from app.routers.getting_started import configure_getting_started_opencode_mcp

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(APIConfig(
            provider="opencode_cli",
            provider_type="local_cli",
            api_key_encrypted="test",
            default_model="opencode/big-pickle",
            cli_command=r"C:\\managed\\opencode.exe",
            readiness_status="ready",
        ))
        db.commit()
        with patch(
            "app.routers.getting_started.resolve_opencode_command",
            return_value=r"C:\\managed\\opencode.exe",
        ), patch(
            "app.routers.getting_started.configure_cli_integration",
            return_value={"status": "configured", "configured": True, "detail": "configured"},
        ) as configure, patch(
            "app.routers.getting_started.preflight_cli_integration",
            return_value={"ready": True, "detail": "ready", "missing_tools": []},
        ) as preflight:
            result = configure_getting_started_opencode_mcp(db)

    assert result.data["ready"] is True
    configure.assert_called_once()
    preflight.assert_called_once()
