"""Local runtime, catalog, routing, and dataset regression tests."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.capabilities import ToolCapabilityUnavailableError, sanitize_tool_request
from app.ai.gateway import LLMGateway
from app.ai.local_runtime_adapter import LocalRuntimeAdapter
from app.database.models import (
    APIConfig,
    Base,
    Chapter,
    LocalModel,
    ModelDownloadTask,
    ModelTaskSetting,
    OperationRun,
    Project,
)
from app.modules.model_runtime.infrastructure.config_crud import SqlAlchemyModelConfigCrud
from app.routers.local_models import catalog as local_model_catalog, delete_model
from app.schemas.local_model import RuntimeStartRequest
from app.services.local_runtime.datasets import build_training_dataset
from app.services.local_runtime.hardware import detect_hardware
from app.services.local_runtime.manager import LocalRuntimeManager
from app.services.local_runtime.launch_settings import default_launch_settings
from app.services.local_runtime.launch_settings import load_launch_settings, save_launch_settings
from app.modules.model_runtime.domain.policy import local_runtime_disabled
from app.schemas.local_model import RuntimeLaunchSettings
from app.services.local_runtime.manifest import model_catalog
from app.services.local_runtime.model_jobs import cancel_download, import_custom_model, register_catalog_model_file, resume_download
from app.services.local_runtime.training import TRAINING_MODEL_IDS, create_training_job


def test_hardware_profile_has_safe_recommendation():
    profile = detect_hardware()
    assert profile.recommended_model in {None, "qwen3.8-27b-q3", "qwen3.8-27b-q4"}
    assert profile.recommended_context in {8192, 16384, 32768}
    assert profile.cpu_count >= 1


def test_hardware_profiles_recommend_memory_safe_starting_contexts():
    cases = [
        ((None, 0.0), 8.0, ("unsupported", None, 8192)),
        ((None, 0.0), 64.0, ("unsupported", None, 8192)),
        (("RTX", 12.0), 31.6, ("unsupported", None, 8192)),
        (("RTX", 16.0), 31.6, ("standard", "qwen3.8-27b-q3", 32768)),
        (("RTX", 24.0), 32.0, ("quality", "qwen3.8-27b-q4", 32768)),
    ]
    for gpu, ram, expected in cases:
        with patch("app.services.local_runtime.hardware._nvidia_gpu", return_value=gpu), patch(
            "app.services.local_runtime.hardware._ram_gb", return_value=ram,
        ):
            profile = detect_hardware()
        assert (profile.profile, profile.recommended_model, profile.recommended_context) == expected
        assert profile.training_supported == (profile.vram_gb >= 24)


def test_embedded_catalog_contains_current_qwen_tiers():
    items = model_catalog()
    assert [item["model_key"] for item in items] == [
        "qwen3.6-27b-q4",
        "qwen3.8-27b-q3",
        "qwen3.8-27b-q4",
    ]
    assert all(item["parameter_size"] == "27B" for item in items)
    assert all(item["context_length"] == 262144 and item["sources"] for item in items)
    latest = items[-1]
    assert latest["family"] == "qwen3.8"
    assert latest["file_name"] == "Qwen3.8-27B-UD-Q4_K_XL.gguf"
    assert "/unsloth/Qwen3.8-27B-GGUF/" in latest["sources"][0]
    compact = items[1]
    assert compact["recommended_vram_gb"] == 16
    assert compact["sha256"] == "8c2a45ff85e7674ca185ec8eb6cdeab0e617ed9d8018caed0b64380eb2a67a5e"


def test_signed_manifest_cannot_reintroduce_small_models():
    remote = {"models": [
        {"model_key": "small", "parameter_size": "9B"},
        {"model_key": "old-large", "family": "qwen3.5", "parameter_size": "27B"},
        {"model_key": "qwen3.5-27b-q4", "parameter_size": "27B"},
        {"model_key": "large", "parameter_size": "27B"},
    ]}
    with patch("app.services.local_runtime.manifest._load_verified_remote_manifest", return_value=remote):
        assert [item["model_key"] for item in model_catalog()] == ["large"]


def test_local_model_page_hides_retired_catalog_entries_without_deleting_custom_models():
    def row(key: str, source: str) -> LocalModel:
        return LocalModel(model_key=key, display_name=key, source=source, status="available")

    store = SimpleNamespace(
        catalog_models=lambda: [
            row("qwen3.5-9b-q4", "catalog"),
            row("qwen3.6-27b-q4", "catalog"),
            row("user-model", "custom"),
        ],
        runtime_installation=lambda _key: None,
    )
    manager = SimpleNamespace(status=lambda: {"running": False})
    with patch("app.routers.local_models.ensure_catalog_rows"), patch(
        "app.routers.local_models.local_model_store", return_value=store
    ), patch("app.routers.local_models.get_runtime_manager", return_value=manager):
        response = local_model_catalog(db=object())

    assert [item["model_key"] for item in response.data["items"]] == [
        "qwen3.6-27b-q4", "user-model",
    ]


def test_only_27b_training_bases_are_supported():
    assert TRAINING_MODEL_IDS == {
        "qwen3.6-27b-q4": "Qwen/Qwen3.6-27B",
        "qwen3.8-27b-q4": "Qwen/Qwen3.8-27B",
    }
    with (
        patch("app.services.local_runtime.training.detect_hardware", return_value=SimpleNamespace(training_supported=True)),
        pytest.raises(ValueError, match="当前基座不支持"),
    ):
        create_training_job(dataset_id="dataset", base_model_key="qwen3.5-9b-q4", name="retired", project_id=None, config={})


def test_local_runtime_tool_request_is_not_stripped():
    tools = [{
        "type": "function",
        "function": {
            "name": "get_project_info",
            "parameters": {"type": "object", "properties": {}},
        },
    }]

    safe_tools, safe_tool_choice, notes = sanitize_tool_request(
        "local_llama_cpp",
        tools,
        "auto",
    )

    assert safe_tools is tools
    assert safe_tool_choice == "auto"
    assert notes == []


def test_non_tool_provider_rejects_required_tools_instead_of_stripping_them():
    tools = [{
        "type": "function",
        "function": {
            "name": "get_project_info",
            "parameters": {"type": "object", "properties": {}},
        },
    }]

    with pytest.raises(
        ToolCapabilityUnavailableError,
        match=r"^tool_capability_unavailable:",
    ) as exc_info:
        sanitize_tool_request("codex_cli", tools, "required")

    assert exc_info.value.reason_code == "tool_capability_unavailable"


def test_task_setting_routes_to_local_runtime_by_default():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(APIConfig(
            provider="local_llama_cpp",
            provider_type="local_runtime",
            api_key_encrypted="",
            default_model="qwen3.6-27b-q4",
            readiness_status="ready",
            readiness_json='{"source":"test_verification"}',
        ))
        db.add(ModelTaskSetting(
            task_type="writing",
            provider="local_llama_cpp",
            model_name="qwen3.6-27b-q4",
        ))
        db.commit()

    with patch("app.modules.model_runtime.infrastructure.configuration.SessionLocal", Session):
        selected = LLMGateway._model_for_task(None, {"moshu_task_type": "writing"})
    assert selected == "local_llama_cpp:qwen3.6-27b-q4"
    assert LLMGateway._model_for_task("deepseek:custom", {"moshu_task_type": "writing"}) == "deepseek:custom"


def test_task_setting_routes_to_local_runtime_without_environment_opt_in():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(APIConfig(
            provider="local_llama_cpp",
            provider_type="local_runtime",
            api_key_encrypted="",
            default_model="qwen3.6-27b-q4",
            readiness_status="ready",
            readiness_json='{"source":"test_verification"}',
        ))
        db.add(ModelTaskSetting(
            task_type="writing",
            provider="local_llama_cpp",
            model_name="qwen3.6-27b-q4",
        ))
        db.commit()

    with patch(
        "app.modules.model_runtime.infrastructure.configuration.SessionLocal", Session,
    ):
        selected = LLMGateway._model_for_task(None, {"moshu_task_type": "writing"})
    assert selected == "local_llama_cpp:qwen3.6-27b-q4"


def test_runtime_start_request_accepts_high_context_values():
    request = RuntimeStartRequest(model_key="qwen3.6-27b-q4", context_length=1_000_000)
    assert request.context_length == 1_000_000


def test_task_default_model_wins_over_global_and_explicit_override_wins_over_task():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(APIConfig(
            provider="claude_cli",
            provider_type="local_cli",
            api_key_encrypted="",
            default_model="claude-code",
            is_global_default=True,
            readiness_status="ready",
            readiness_json='{"source":"test_verification"}',
        ))
        db.add(APIConfig(
            provider="local_llama_cpp",
            provider_type="local_runtime",
            api_key_encrypted="",
            default_model="qwen3.6-27b-q4",
            readiness_status="ready",
            readiness_json='{"source":"test_verification"}',
        ))
        db.add(ModelTaskSetting(
            task_type="cataloging",
            provider="local_llama_cpp",
            model_name="qwen3.6-27b-q4",
            context_length=262144,
        ))
        db.commit()

    with patch(
        "app.modules.model_runtime.infrastructure.configuration.SessionLocal", Session,
    ):
        selected = LLMGateway.select_model_for_task(task_type="cataloging")
        explicit_body = {"moshu_task_type": "cataloging"}
        explicit = LLMGateway.select_model_for_task(
            task_type="cataloging",
            model_override="local_llama_cpp:qwen3.6-27b-q4",
            extra_body=explicit_body,
        )

    assert selected.model == "local_llama_cpp:qwen3.6-27b-q4"
    assert selected.source == "task_setting"
    assert selected.provider == "local_llama_cpp"
    assert explicit.model == "local_llama_cpp:qwen3.6-27b-q4"
    assert explicit.source == "explicit"
    assert explicit_body["moshu_context_length"] == 262144


def test_first_verified_local_model_becomes_default_without_overriding_user_choice():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        local = APIConfig(
            provider="local_llama_cpp",
            api_key_encrypted="encrypted",
            default_model="local-model",
            readiness_status="ready",
            is_global_default=False,
        )
        db.add(local)
        db.flush()
        crud = SqlAlchemyModelConfigCrud(db)

        assert crud.make_global_if_no_ready_default(local)
        assert local.is_global_default

        existing = APIConfig(
            provider="openai",
            api_key_encrypted="encrypted",
            default_model="remote-model",
            readiness_status="ready",
            is_global_default=True,
        )
        db.add(existing)
        local.is_global_default = False
        db.flush()

        assert not crud.make_global_if_no_ready_default(local)
        assert existing.is_global_default
        assert not local.is_global_default


def test_local_runtime_server_uses_single_parallel_slot():
    command = LocalRuntimeManager._build_command(
        "llama-server.exe",
        "model.gguf",
        "qwen3.6-27b-q4",
        8765,
        32768,
        8,
        99,
        [SimpleNamespace(file_path="adapter.gguf", weight=0.75)],
    )
    parallel_index = command.index("--parallel")
    assert command[parallel_index + 1] == "1"
    assert "--lora-scaled" in command


def test_qwen38_q3_launch_uses_measured_memory_and_mtp_settings():
    command = LocalRuntimeManager._build_command(
        "llama-server.exe", "model.gguf", "qwen3.8-27b-q3", 8765, 32768, 8, 99, [],
    )
    for option, value in (
        ("--fit", "off"), ("--flash-attn", "on"),
        ("--cache-type-k", "q4_0"), ("--cache-type-v", "q4_0"),
        ("--spec-type", "draft-mtp"), ("--spec-draft-n-max", "2"),
        ("--cache-ram", "2048"),
    ):
        assert command[command.index(option) + 1] == value


def test_local_runtime_command_preserves_requested_context_length():
    command = LocalRuntimeManager._build_command(
        "llama-server.exe", "model.gguf", "custom", 8765, 1_000_000, 8, 99, [],
    )
    context_index = command.index("--ctx-size")
    assert command[context_index + 1] == "1000000"


def test_local_runtime_command_is_loopback_browser_restricted_and_authenticated():
    command = LocalRuntimeManager._build_command(
        "llama-server.exe",
        "model.gguf",
        "custom",
        8765,
        16384,
        8,
        99,
        [],
        api_key="ephemeral-secret",
    )

    assert command[command.index("--cors-origins") + 1] == "localhost"
    assert command[command.index("--api-key") + 1] == "ephemeral-secret"
    redacted = LocalRuntimeManager._redacted_command(command)
    assert "ephemeral-secret" not in redacted
    assert redacted[redacted.index("--api-key") + 1] == "<redacted>"


def test_saved_launch_profile_drives_command_and_can_disable_automatic_start():
    with TemporaryDirectory() as temp_dir, patch.dict("os.environ", {"SIMING_HOME": temp_dir}):
        settings = RuntimeLaunchSettings(
            context_length=32768, gpu_layers=70, threads=8,
            flash_attention="on", fit="off", kv_cache_type="q4_0",
            mtp_draft_tokens=2, cache_ram_mb=2048, reasoning_effort="medium",
            temperature=1.0, top_p=0.95, top_k=20, min_p=0,
            repeat_penalty=1.0,
        )
        save_launch_settings("qwen3.8-27b-q3", settings)
        loaded, saved = load_launch_settings(
            "qwen3.8-27b-q3", context_length=16384, gpu_layers=99, threads=4,
        )
        assert saved and loaded == settings
        command = LocalRuntimeManager._build_command(
            "llama-server.exe", "model.gguf", "qwen3.8-27b-q3",
            8765, loaded.context_length, loaded.threads or 4, loaded.gpu_layers, [],
            launch_settings=loaded,
        )
        assert command[command.index("--ctx-size") + 1] == "32768"
        assert command[command.index("--gpu-layers") + 1] == "70"
        assert command[command.index("--cache-type-k") + 1] == "q4_0"
        assert command[command.index("--spec-draft-n-max") + 1] == "2"

        from app.services.application_settings import load_launcher_settings, save_launcher_settings
        launcher = load_launcher_settings()
        launcher["local_runtime_enabled"] = False
        save_launcher_settings(launcher)
        assert local_runtime_disabled()
        manager = LocalRuntimeManager()
        with patch.object(manager, "_load_assets") as load_assets, pytest.raises(RuntimeError, match="已在模型中心关闭"):
            manager.ensure_running("qwen3.8-27b-q3")
        load_assets.assert_not_called()


def test_local_runtime_adapter_uses_the_ephemeral_process_key():
    manager = SimpleNamespace(
        api_key="runtime-key",
        ensure_running=lambda *args, **kwargs: "http://127.0.0.1:8765/v1",
    )
    adapter = LocalRuntimeAdapter(api_key="placeholder")

    with patch("app.ai.local_runtime_adapter.get_runtime_manager", return_value=manager):
        base_url, payload = adapter._runtime_context("custom", None)

    assert base_url == "http://127.0.0.1:8765/v1"
    assert payload == {"chat_template_kwargs": {"enable_thinking": False}}
    assert adapter.api_key == "runtime-key"


def test_qwen38_q3_enables_reasoning_for_tool_rounds_only():
    manager = SimpleNamespace(
        api_key="runtime-key",
        ensure_running=lambda *args, **kwargs: "http://127.0.0.1:8765/v1",
    )
    adapter = LocalRuntimeAdapter(api_key="placeholder")
    with patch("app.ai.local_runtime_adapter.get_runtime_manager", return_value=manager):
        _, tool_payload = adapter._runtime_context("qwen3.8-27b-q3", None, tools_enabled=True)
        _, text_payload = adapter._runtime_context("qwen3.8-27b-q3", None)
    assert tool_payload["chat_template_kwargs"] == {"enable_thinking": True}
    assert text_payload["chat_template_kwargs"] == {"enable_thinking": False}


def test_healthy_runtime_reconciles_durable_status_before_reuse():
    manager = LocalRuntimeManager()
    manager._model_key = "custom"
    manager._requested_context_length = 16384
    manager._context_length = 16384
    launch = default_launch_settings("custom", context_length=16384, gpu_layers=99, threads=7)
    manager._adapter_signature = json.dumps({"adapters": [], "model_file": "model.gguf", "settings": launch.model_dump()}, ensure_ascii=False, sort_keys=True)
    manager._port = 8765
    manager._api_key = "runtime-key"
    model = SimpleNamespace(context_length=262144, file_path="model.gguf")
    profile = SimpleNamespace(recommended_context=16384, nvidia_available=True, cpu_count=8)

    with patch.object(
        manager,
        "_load_assets",
        return_value=(model, SimpleNamespace(), []),
    ), patch(
        "app.services.local_runtime.manager.detect_hardware",
        return_value=profile,
    ), patch.object(
        manager,
        "_healthy",
        return_value=True,
    ), patch.object(
        manager,
        "_mark_runtime_running",
    ) as reconcile:
        result = manager.ensure_running("custom")

    assert result == "http://127.0.0.1:8765/v1"
    reconcile.assert_called_once_with()


def test_local_runtime_defaults_to_safe_context_but_preserves_model_capacity():
    assert LocalRuntimeManager._default_context_length(262144, 16384) == 16384
    assert LocalRuntimeManager._default_context_length(8192, 16384) == 8192
    assert LocalRuntimeManager._default_context_length(None, 16384) == 16384


def test_local_runtime_falls_back_to_cpu_without_shrinking_context():
    assert LocalRuntimeManager._launch_profiles(True, 16384) == [
        (99, 16384),
        (0, 16384),
    ]
    assert LocalRuntimeManager._launch_profiles(False, 16384) == [(0, 16384)]


def test_curated_external_gguf_can_be_registered_and_unregistered_without_deletion():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "Qwen3.8-27B-UD-Q3_K_XL.gguf"
        path.write_bytes(b"GGUFtest-model")
        with Session() as db:
            db.add(LocalModel(
                model_key="qwen3.8-27b-q3", display_name="Qwen3.8 27B",
                source="catalog", status="available", context_length=262144,
            ))
            db.commit()
        with patch("app.services.local_runtime.model_jobs.SessionLocal", Session), patch(
            "app.services.local_runtime.model_jobs.model_spec",
            return_value={"file_name": path.name},
        ), patch("app.services.local_runtime.model_jobs.ensure_catalog_rows"):
            register_catalog_model_file("qwen3.8-27b-q3", str(path))
        with Session() as db:
            row = db.query(LocalModel).filter_by(model_key="qwen3.8-27b-q3").one()
            assert row.status == "installed" and Path(row.file_path).samefile(path)
            delete_model("qwen3.8-27b-q3", db=db)
        assert path.read_bytes() == b"GGUFtest-model"


def test_failed_download_can_be_dismissed_and_cannot_restart_implicitly():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        operation = OperationRun(
            source_kind="download", source_id="old-qwen35",
            title="旧模型下载", status="failed", phase="failed",
        )
        db.add(operation)
        db.flush()
        db.add(ModelDownloadTask(
            id="old-qwen35", kind="model", target_key="qwen3.5-9b-q4",
            destination_path="old.gguf", status="failed", error_message="network error",
            operation_id=operation.id,
        ))
        db.commit()
    with patch("app.services.local_runtime.model_jobs.SessionLocal", Session):
        with pytest.raises(ValueError, match="退出目录"):
            resume_download("old-qwen35")
        cancel_download("old-qwen35")
        with pytest.raises(ValueError, match="只有失败"):
            resume_download("old-qwen35")
    with Session() as db:
        task = db.query(ModelDownloadTask).filter_by(id="old-qwen35").one()
        assert task.status == "cancelled" and task.error_message is None
        operation = db.query(OperationRun).filter_by(source_id="old-qwen35").one()
        assert operation.status == "cancelled" and operation.can_retry is False


def test_imported_custom_gguf_is_registered_without_copying():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with TemporaryDirectory() as temp_dir:
        model_path = Path(temp_dir) / "qwen36.gguf"
        model_path.write_bytes(b"GGUF")
        with patch("app.services.local_runtime.model_jobs.SessionLocal", Session):
            import_custom_model(
                model_key="qwen36-27b-q4",
                display_name="Qwen 3.6 27B Q4",
                file_path=str(model_path),
                context_length=262144,
            )
        with Session() as db:
            model = db.query(LocalModel).filter(LocalModel.model_key == "qwen36-27b-q4").one()
            assert model.file_path == str(model_path.resolve())
            assert model.context_length == 262144
            assert model.source == "custom"
            assert model.status == "installed"


def test_training_dataset_deduplicates_and_splits():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    content = "第一段。" * 120 + "\n“你终于来了，我已经在这里等了整整三天。”\n“先别说话，门外的东西还没有走远。”" * 20
    with TemporaryDirectory() as temp_dir, Session() as db:
        project = Project(id="p1", title="测试作品", folder_path=temp_dir)
        db.add(project)
        db.add_all([
            Chapter(id="c1", project_id="p1", title="第一章", content=content, word_count=len(content)),
            Chapter(id="c2", project_id="p1", title="第二章", content=content + "第二章变化。", word_count=len(content)),
        ])
        db.commit()
        with patch("app.services.local_runtime.datasets.training_root", return_value=Path(temp_dir)):
            dataset = build_training_dataset(
                db,
                name="测试训练集",
                project_id="p1",
                chapter_ids=[],
                include_outline_pairs=True,
                include_revision_pairs=False,
                include_character_dialogue=True,
                eval_ratio=0.2,
                rights_confirmed=True,
            )
            db.commit()
            lines = [
                json.loads(line)
                for line in Path(dataset.file_path).read_text(encoding="utf-8").splitlines()
            ]
    assert dataset.sample_count == len(lines)
    assert dataset.train_count + dataset.eval_count == dataset.sample_count
    assert {item["split"] for item in lines} == {"train", "eval"}
