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
    ModelTaskSetting,
    Project,
)
from app.modules.model_runtime.infrastructure.config_crud import SqlAlchemyModelConfigCrud
from app.schemas.local_model import RuntimeStartRequest
from app.services.local_runtime.datasets import build_training_dataset
from app.services.local_runtime.hardware import detect_hardware
from app.services.local_runtime.manager import LocalRuntimeManager
from app.services.local_runtime.manifest import model_catalog
from app.services.local_runtime.model_jobs import import_custom_model


def test_hardware_profile_has_safe_recommendation():
    profile = detect_hardware()
    assert profile.recommended_model in {"qwen3.5-4b-q4", "qwen3.5-9b-q4", "qwen3.8-27b-q4"}
    assert profile.recommended_context in {8192, 16384, 32768}
    assert profile.cpu_count >= 1


def test_hardware_profiles_recommend_memory_safe_starting_contexts():
    cases = [
        ((None, 0.0), 8.0, ("light", 8192)),
        (("RTX", 16.0), 32.0, ("standard", 16384)),
        (("RTX", 24.0), 32.0, ("quality", 32768)),
    ]
    for gpu, ram, expected in cases:
        with patch("app.services.local_runtime.hardware._nvidia_gpu", return_value=gpu), patch(
            "app.services.local_runtime.hardware._ram_gb", return_value=ram,
        ):
            profile = detect_hardware()
        assert (profile.profile, profile.recommended_context) == expected


def test_embedded_catalog_contains_current_qwen_tiers():
    items = model_catalog()
    assert [item["model_key"] for item in items] == [
        "qwen3.5-4b-q4",
        "qwen3.5-9b-q4",
        "qwen3.8-27b-q4",
    ]
    assert all(item["context_length"] == 262144 and item["sources"] for item in items)
    latest = items[-1]
    assert latest["family"] == "qwen3.8"
    assert latest["file_name"] == "Qwen3.8-27B-UD-Q4_K_XL.gguf"
    assert "/unsloth/Qwen3.8-27B-GGUF/" in latest["sources"][0]


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
            default_model="qwen3.5-9b-q4",
            readiness_status="ready",
            readiness_json='{"source":"test_verification"}',
        ))
        db.add(ModelTaskSetting(
            task_type="writing",
            provider="local_llama_cpp",
            model_name="qwen3.5-9b-q4",
        ))
        db.commit()

    with patch("app.modules.model_runtime.infrastructure.configuration.SessionLocal", Session):
        selected = LLMGateway._model_for_task(None, {"moshu_task_type": "writing"})
    assert selected == "local_llama_cpp:qwen3.5-9b-q4"
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
            default_model="qwen3.5-9b-q4",
            readiness_status="ready",
            readiness_json='{"source":"test_verification"}',
        ))
        db.add(ModelTaskSetting(
            task_type="writing",
            provider="local_llama_cpp",
            model_name="qwen3.5-9b-q4",
        ))
        db.commit()

    with patch(
        "app.modules.model_runtime.infrastructure.configuration.SessionLocal", Session,
    ):
        selected = LLMGateway._model_for_task(None, {"moshu_task_type": "writing"})
    assert selected == "local_llama_cpp:qwen3.5-9b-q4"


def test_runtime_start_request_accepts_high_context_values():
    request = RuntimeStartRequest(model_key="qwen3.5-9b-q4", context_length=1_000_000)
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
            default_model="qwen3.5-27b-q4",
            readiness_status="ready",
            readiness_json='{"source":"test_verification"}',
        ))
        db.add(ModelTaskSetting(
            task_type="cataloging",
            provider="local_llama_cpp",
            model_name="qwen3.5-27b-q4",
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
            model_override="local_llama_cpp:qwen3.5-27b-q4",
            extra_body=explicit_body,
        )

    assert selected.model == "local_llama_cpp:qwen3.5-27b-q4"
    assert selected.source == "task_setting"
    assert selected.provider == "local_llama_cpp"
    assert explicit.model == "local_llama_cpp:qwen3.5-27b-q4"
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
        "qwen3.5-9b-q4",
        8765,
        32768,
        8,
        99,
        [SimpleNamespace(file_path="adapter.gguf", weight=0.75)],
    )
    parallel_index = command.index("--parallel")
    assert command[parallel_index + 1] == "1"
    assert "--lora-scaled" in command


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


def test_healthy_runtime_reconciles_durable_status_before_reuse():
    manager = LocalRuntimeManager()
    manager._model_key = "custom"
    manager._requested_context_length = 16384
    manager._context_length = 16384
    manager._adapter_signature = "[]"
    manager._port = 8765
    manager._api_key = "runtime-key"
    model = SimpleNamespace(context_length=262144)
    profile = SimpleNamespace(recommended_context=16384, nvidia_available=True)

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
