from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.bootstrap.composition import configure_application_services
from app.core.exceptions import AppException, app_exception_handler
from app.database.models import Project
from app.database.session import Base, get_db
from app.modules.assistant.infrastructure.models import AssistantConversation
from app.routers import conversation_context as conversation_context_router
from app.routers.conversation_context import router
from app.services.conversation_context.errors import (
    ConversationContextError,
    ConversationContextErrorCode,
)
from app.services.persistence.assistant_workspace import SqlAlchemyAssistantWorkspace


def _resource_uuid(index: int) -> str:
    return f"00000000-0000-4000-8000-{index:012d}"


@pytest.fixture
def context_api() -> Iterator[tuple[TestClient, sessionmaker, str]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        db.add_all(
            [
                Project(id="project-1", title="Owned"),
                Project(id="project-2", title="Foreign"),
                AssistantConversation(
                    id="conversation-1",
                    project_id="project-1",
                    title="Long conversation",
                ),
                AssistantConversation(
                    id="conversation-short",
                    project_id="project-1",
                    title="Short conversation",
                ),
                AssistantConversation(
                    id="conversation-foreign",
                    project_id="project-2",
                    title="Foreign conversation",
                ),
            ]
        )
        db.commit()

    configure_application_services()
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(router, prefix="/api/v1")

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client, factory, "/api/v1/projects/project-1/ai/assistant/conversations/conversation-1"
    engine.dispose()


def _create_checkpoint(
    factory: sessionmaker,
    *,
    checkpoint_id: str,
    status: str,
    conversation_id: str = "conversation-1",
    project_id: str = "project-1",
    source_first_sequence: int = 1,
    source_last_sequence: int = 2,
    parent_checkpoint_id: str | None = None,
) -> None:
    with factory() as db:
        store = SqlAlchemyAssistantWorkspace(db)
        if not store.conversation_messages(conversation_id):
            store.create_message(
                conversation_id=conversation_id,
                role="user",
                content="不要修改主角姓名。",
                status="completed",
            )
            store.create_message(
                conversation_id=conversation_id,
                role="assistant",
                content="已记录。",
                status="completed",
            )
        checkpoint = store.create_context_checkpoint(
            "workspace",
            conversation_id,
            owner_id=project_id,
            id=checkpoint_id,
            idempotency_key=f"attempt:{checkpoint_id}",
            parent_checkpoint_id=parent_checkpoint_id,
            source_first_sequence=source_first_sequence,
            source_last_sequence=source_last_sequence,
            source_message_count=source_last_sequence - source_first_sequence + 1,
            source_hash=(checkpoint_id[-1].lower() if checkpoint_id[-1].lower() in "abcdef" else "a")
            * 64,
            transcript_revision=source_last_sequence,
            model_binding_json={
                "provider": "openai",
                "model_name": "gpt-test",
                "normalized_model": "openai:gpt-test",
                "display_name": "OpenAI test",
                "api_key": "must-not-leak",
                "config_fingerprint": "internal-only",
            },
        )
        assert checkpoint is not None
        if status != "pending":
            assert store.update_context_checkpoint_status(
                "workspace",
                conversation_id,
                checkpoint.id,
                "compressing",
                owner_id=project_id,
                expected_statuses=["pending"],
            ) is not None
        if status in {"ready", "failed", "cancelled", "superseded"}:
            target = status if status in {"ready", "failed", "cancelled"} else "superseded"
            assert store.update_context_checkpoint_status(
                "workspace",
                conversation_id,
                checkpoint.id,
                target,
                owner_id=project_id,
                expected_statuses=["compressing"],
                semantic_navigation_json={
                    "authority": "non_authoritative_navigation",
                    "current_objectives": ["继续写下一章"],
                    "hidden_reasoning": "must-not-leak",
                },
                author_quotes_json=[{
                    "message_id": "source-user",
                    "start_char": 0,
                    "end_char": 9,
                    "exact_quote": "不要修改主角姓名。",
                    "purpose": "active_constraint",
                    "arguments": {"secret": True},
                }],
                execution_ledger_json=[{
                    "run_id": "run-1",
                    "step_id": "step-1",
                    "tool": "create_outline_nodes",
                    "status": "ok",
                    "resource_refs": [
                        {"type": "outline", "id": _resource_uuid(1), "revision": 3}
                    ],
                    "arguments": {"title": "must-not-leak"},
                    "reasoning": "must-not-leak",
                    "detail": "must-not-leak",
                    "error_code": "api_key=sk-private arguments=must-not-leak",
                }],
                project_refs_json=[{
                    "type": "outline",
                    "id": _resource_uuid(1),
                    "reason": "使用前重新读取当前版本",
                    "revision": 3,
                    "arguments": {"secret": True},
                }],
                validation_json={"warnings": ["来源已重新校验"], "raw_output": "secret"},
                original_tokens=84_000,
                checkpoint_tokens=6_000,
                error_code=("conversation_checkpoint_failed" if status == "failed" else None),
                error_detail=("结构校验失败" if status == "failed" else None),
            ) is not None
        if status == "ready":
            state = store.context_state("workspace", conversation_id, owner_id=project_id)
            assert state is not None
            assert store.publish_context_checkpoint(
                "workspace",
                conversation_id,
                checkpoint.id,
                state.revision,
                owner_id=project_id,
                last_budget_json={
                    "trigger": "projected_next_step_over_capacity",
                    "capacity_assurance": "exact",
                    "recent_exact_turn_count": 4,
                    "original_history_tokens": 84_000,
                    "active_history_tokens": 19_000,
                },
            )
        db.commit()


def test_short_conversation_returns_ready_without_creating_a_state(context_api) -> None:
    client, factory, _base = context_api
    response = client.get(
        "/api/v1/projects/project-1/ai/assistant/conversations/conversation-short/context-state"
    )
    assert response.status_code == 200
    assert response.json()["data"] == {
        "status": "ready",
        "policy_version": 1,
        "active_checkpoint_id": None,
        "latest_checkpoint_id": None,
        "source_message_count": 0,
        "recent_exact_turn_count": 0,
        "original_history_tokens": 0,
        "active_history_tokens": 0,
        "trigger": "within_capacity",
        "capacity_assurance": "unverified",
        "provider": None,
        "model": None,
        "model_binding": None,
        "warnings": [],
        "error_code": None,
        "error_detail": None,
        "retryable": False,
        "updated_at": None,
    }
    with factory() as db:
        store = SqlAlchemyAssistantWorkspace(db)
        assert store.context_state(
            "workspace",
            "conversation-short",
            owner_id="project-1",
        ) is None


def test_response_model_drops_unexpected_internal_context_fields(
    context_api,
    monkeypatch,
) -> None:
    client, _factory, base = context_api
    original = conversation_context_router.public_context_state_payload

    def injected_payload(**kwargs):
        return {
            **original(**kwargs),
            "provider_secret": "sk-must-not-cross-public-boundary",
            "internal_diagnostic": {"arguments": {"delete": True}},
        }

    monkeypatch.setattr(
        conversation_context_router,
        "public_context_state_payload",
        injected_payload,
    )

    response = client.get(f"{base}/context-state")
    assert response.status_code == 200
    serialized = json.dumps(response.json(), ensure_ascii=False)
    assert "provider_secret" not in serialized
    assert "sk-must-not-cross-public-boundary" not in serialized
    assert "internal_diagnostic" not in serialized


def test_detail_and_list_are_owner_scoped_and_redacted(context_api) -> None:
    client, factory, base = context_api
    _create_checkpoint(factory, checkpoint_id="checkpoint-a", status="ready")

    state = client.get(f"{base}/context-state")
    assert state.status_code == 200
    assert state.json()["data"]["active_checkpoint_id"] == "checkpoint-a"
    assert state.json()["data"]["recent_exact_turn_count"] == 4

    detail = client.get(f"{base}/checkpoints/checkpoint-a")
    assert detail.status_code == 200
    payload = detail.json()["data"]
    assert payload["scope"] == "workspace"
    assert payload["status"] == "ready"
    assert payload["original_history_tokens"] == 84_000
    assert payload["model_binding"] == {
        "provider": "openai",
        "model": "openai:gpt-test",
        "display_name": "OpenAI test",
    }
    assert payload["execution_ledger"][0]["resource_refs"] == [
        {"type": "outline", "id": _resource_uuid(1), "revision": 3}
    ]
    assert payload["project_refs"] == [
        {
            "type": "outline",
            "id": _resource_uuid(1),
            "reason": "使用前重新读取当前版本",
        }
    ]
    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in ("api_key", "arguments", "hidden_reasoning", "reasoning", "raw_output"):
        assert forbidden not in serialized
    # Even a legacy model-created detail string is not an execution receipt.
    assert "must-not-leak" not in serialized

    listing = client.get(f"{base}/checkpoints")
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] == 1
    item = listing.json()["data"]["items"][0]
    assert item["id"] == "checkpoint-a"
    assert "author_quotes" not in item
    assert "semantic_navigation" not in item
    assert "execution_ledger" not in item

    assert client.get(
        "/api/v1/projects/project-2/ai/assistant/conversations/conversation-1/context-state"
    ).status_code == 404
    assert client.get(
        "/api/v1/projects/project-2/ai/assistant/conversations/conversation-1/checkpoints/checkpoint-a"
    ).status_code == 404


def test_legacy_raw_checkpoint_error_code_is_never_returned_by_rest_or_state(
    context_api,
) -> None:
    client, factory, base = context_api
    _create_checkpoint(factory, checkpoint_id="checkpoint-raw-error", status="failed")
    secret = 'api_key=sk-private {"tool":"delete_project","arguments":{}}'
    with factory() as db:
        checkpoint = SqlAlchemyAssistantWorkspace(db).context_checkpoint(
            "workspace",
            "conversation-1",
            "checkpoint-raw-error",
            owner_id="project-1",
        )
        assert checkpoint is not None
        checkpoint.error_code = secret
        checkpoint.error_detail = secret
        db.commit()

    for response in (
        client.get(f"{base}/context-state"),
        client.get(f"{base}/checkpoints/checkpoint-raw-error"),
        client.get(f"{base}/checkpoints"),
    ):
        assert response.status_code == 200
        serialized = json.dumps(response.json(), ensure_ascii=False)
        assert "sk-private" not in serialized
        assert "delete_project" not in serialized
        assert "arguments" not in serialized
        assert "conversation_checkpoint_failed" in serialized


def test_foreign_active_checkpoint_pointer_fails_closed_at_public_projection(
    context_api,
) -> None:
    client, factory, base = context_api
    owned_checkpoint_id = "checkpoint-owned-active"
    foreign_checkpoint_id = "checkpoint-foreign-active"
    _create_checkpoint(factory, checkpoint_id=owned_checkpoint_id, status="ready")
    _create_checkpoint(
        factory,
        checkpoint_id=foreign_checkpoint_id,
        status="ready",
        conversation_id="conversation-foreign",
        project_id="project-2",
    )

    with factory() as db:
        store = SqlAlchemyAssistantWorkspace(db)
        state = store.context_state(
            "workspace",
            "conversation-1",
            owner_id="project-1",
        )
        assert state is not None
        state.active_checkpoint_id = foreign_checkpoint_id
        db.commit()

    state_response = client.get(f"{base}/context-state")
    detail_response = client.get(f"{base}/checkpoints/{owned_checkpoint_id}")
    list_response = client.get(f"{base}/checkpoints")
    for response in (state_response, detail_response, list_response):
        assert response.status_code == 200
        assert foreign_checkpoint_id not in json.dumps(response.json(), ensure_ascii=False)

    payload = state_response.json()["data"]
    assert payload["status"] == "failed"
    assert payload["active_checkpoint_id"] is None
    assert payload["latest_checkpoint_id"] == owned_checkpoint_id
    assert payload["source_message_count"] == 0
    assert payload["capacity_assurance"] == "unverified"
    assert payload["error_code"] == ConversationContextErrorCode.SOURCE_CHANGED.value
    assert payload["retryable"] is True


def test_malformed_legacy_checkpoint_json_fails_closed_at_public_projection(
    context_api,
) -> None:
    client, factory, base = context_api
    _create_checkpoint(factory, checkpoint_id="checkpoint-malformed", status="ready")
    secret = "sk-malformed-legacy-secret"
    with factory() as db:
        store = SqlAlchemyAssistantWorkspace(db)
        checkpoint = store.context_checkpoint(
            "workspace",
            "conversation-1",
            "checkpoint-malformed",
            owner_id="project-1",
        )
        state = store.context_state(
            "workspace",
            "conversation-1",
            owner_id="project-1",
        )
        assert checkpoint is not None and state is not None
        checkpoint.model_binding_json = {
            "provider": {"api_key": secret},
            "normalized_model": {"arguments": secret},
            "display_name": [secret],
        }
        checkpoint.semantic_navigation_json = {
            "current_objectives": [{"hidden_reasoning": secret}],
        }
        checkpoint.author_quotes_json = [
            {
                "message_id": "source-user",
                "start_char": {"api_key": secret},
                "end_char": 9,
                "exact_quote": "do not leak",
                "quote_sha256": "a" * 64,
                "purpose": "constraint",
            }
        ]
        checkpoint.execution_ledger_json = [
            {
                "run_id": "run-1",
                "step_id": "step-1",
                "tool": "read_outline",
                "status": "ok",
                "resource_refs": [
                    {
                        "type": "outline",
                        "id": _resource_uuid(1),
                        "revision": 3,
                    },
                    {
                        "type": "outline",
                        "id": _resource_uuid(2),
                        "revision": secret,
                    },
                    {
                        "type": "outline",
                        "id": _resource_uuid(3),
                        "revision": "/tmp/private/key",
                    },
                    {
                        "type": "outline",
                        "id": _resource_uuid(4),
                        "revision": -1,
                    },
                    {
                        "type": "outline",
                        "id": _resource_uuid(5),
                        "revision": True,
                    },
                    {
                        "type": "outline",
                        "id": _resource_uuid(6),
                        "revision": {"api_key": secret, "arguments": secret},
                    },
                    {"type": "outline", "id": "/tmp/private/key", "revision": 7},
                    {"type": "outline", "id": "sk-private-token", "revision": 8},
                    {
                        "type": "character",
                        "id": "mobile:character-legacy-1",
                        "revision": 9,
                    },
                    {"type": "sk-private-token", "id": _resource_uuid(9), "revision": 10},
                ],
            },
            {
                "run_id": {"hidden_reasoning": secret},
                "step_id": "step-invalid",
                "tool": "read_outline",
                "status": "ok",
            },
        ]
        checkpoint.project_refs_json = [
            {
                "type": "outline",
                "id": _resource_uuid(1),
                "reason": {"provider_diagnostic": secret},
            },
            {"type": "outline", "id": "/tmp/private/key", "reason": "reread"},
            {"type": "outline", "id": "sk-private-token", "reason": "reread"},
            {"type": "sk-private-token", "id": _resource_uuid(9), "reason": "reread"},
        ]
        checkpoint.validation_json = {
            "warnings": [{"provider_diagnostic": secret}],
            "raw_output": secret,
        }
        state.last_budget_json = {
            "trigger": {"arguments": secret},
            "capacity_assurance": {"api_key": secret},
            "recent_exact_turn_count": {"value": secret},
            "original_history_tokens": [secret],
            "active_history_tokens": {"value": secret},
            "warnings": [{"hidden_reasoning": secret}],
        }
        db.commit()

    state_response = client.get(f"{base}/context-state")
    detail_response = client.get(f"{base}/checkpoints/checkpoint-malformed")
    list_response = client.get(f"{base}/checkpoints")
    for response in (state_response, detail_response, list_response):
        assert response.status_code == 200
        serialized = json.dumps(response.json(), ensure_ascii=False)
        assert secret not in serialized
        for forbidden in ("api_key", "arguments", "hidden_reasoning", "raw_output"):
            assert forbidden not in serialized

    state_payload = state_response.json()["data"]
    assert state_payload["model_binding"] is None
    assert state_payload["capacity_assurance"] == "unverified"
    assert state_payload["trigger"] == "within_capacity"
    assert state_payload["recent_exact_turn_count"] == 0
    assert state_payload["warnings"] == []

    detail_payload = detail_response.json()["data"]
    assert detail_payload["author_quotes"] == []
    assert detail_payload["project_refs"] == []
    assert detail_payload["warnings"] == []
    assert detail_payload["execution_ledger"] == [
        {
            "run_id": "run-1",
            "step_id": "step-1",
            "tool": "read_outline",
            "status": "ok",
            "resource_refs": [
                {"type": "outline", "id": _resource_uuid(1), "revision": 3},
                {"type": "outline", "id": _resource_uuid(2)},
                {"type": "outline", "id": _resource_uuid(3)},
                {"type": "outline", "id": _resource_uuid(4)},
                {"type": "outline", "id": _resource_uuid(5)},
                {"type": "outline", "id": _resource_uuid(6)},
            ],
            "error_code": None,
        }
    ]


def test_cancel_is_real_and_rebuild_never_fakes_ready(context_api) -> None:
    client, factory, base = context_api
    _create_checkpoint(factory, checkpoint_id="checkpoint-b", status="pending")

    response = client.post(f"{base}/checkpoints/checkpoint-b/cancel")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "failed"
    assert response.json()["data"]["error_code"] == "conversation_checkpoint_cancelled"
    with factory() as db:
        checkpoint = SqlAlchemyAssistantWorkspace(db).context_checkpoint(
            "workspace",
            "conversation-1",
            "checkpoint-b",
            owner_id="project-1",
        )
        assert checkpoint is not None
        assert checkpoint.status == "cancelled"
        assert checkpoint.cancel_requested_at is not None

    replay = client.post(f"{base}/checkpoints/checkpoint-b/cancel")
    assert replay.status_code == 200
    assert replay.json()["data"]["status"] == "failed"
    assert client.post(
        "/api/v1/projects/project-2/ai/assistant/conversations/"
        "conversation-1/checkpoints/checkpoint-b/cancel"
    ).status_code == 404

    rebuild = client.post(f"{base}/checkpoints/rebuild")
    assert rebuild.status_code == 409
    assert "发送新消息触发按需重建" in rebuild.json()["message"]
    assert client.post(
        "/api/v1/projects/project-2/ai/assistant/conversations/conversation-1/checkpoints/rebuild"
    ).status_code == 404


def test_cancel_never_reflects_internal_context_error_detail(
    context_api, monkeypatch
) -> None:
    client, factory, base = context_api
    _create_checkpoint(factory, checkpoint_id="checkpoint-secret", status="pending")
    secret = "sk-provider-secret raw arguments={delete:true} hidden reasoning"

    def fail_cancel(**_kwargs):
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_FAILED,
            secret,
            details={"raw_provider_error": secret},
        )

    monkeypatch.setattr(
        "app.routers.conversation_context.context_runtime.cancel_checkpoint_attempt",
        fail_cancel,
    )
    response = client.post(f"{base}/checkpoints/checkpoint-secret/cancel")

    assert response.status_code == 409
    serialized = response.text
    assert secret not in serialized
    assert "raw_provider_error" not in serialized
    assert response.json()["message"] == (
        "对话历史整理失败，本次任务未执行；请重试。"
        "若当前使用本机 Agent CLI，请切换已验证的 API 模型或新建对话。"
    )


def test_delete_preserves_active_and_referenced_audit_segments(context_api) -> None:
    client, factory, base = context_api
    _create_checkpoint(factory, checkpoint_id="checkpoint-c", status="ready")
    active = client.delete(f"{base}/checkpoints/checkpoint-c")
    assert active.status_code == 409

    _create_checkpoint(factory, checkpoint_id="checkpoint-d", status="failed")
    _create_checkpoint(
        factory,
        checkpoint_id="checkpoint-e",
        status="cancelled",
        parent_checkpoint_id="checkpoint-d",
    )
    referenced = client.delete(f"{base}/checkpoints/checkpoint-d")
    assert referenced.status_code == 409
    assert "未执行删除" in referenced.json()["message"]

    unavailable = client.delete(f"{base}/checkpoints/checkpoint-e")
    assert unavailable.status_code == 409
    assert client.delete(
        "/api/v1/projects/project-2/ai/assistant/conversations/"
        "conversation-1/checkpoints/checkpoint-e"
    ).status_code == 404
    assert "未执行删除" in unavailable.json()["message"]
    with factory() as db:
        store = SqlAlchemyAssistantWorkspace(db)
        assert store.context_checkpoint(
            "workspace",
            "conversation-1",
            "checkpoint-e",
            owner_id="project-1",
        ) is not None
        assert len(store.conversation_messages("conversation-1")) == 2


def _openapi_success_data_schema(client: TestClient, path: str, method: str) -> dict:
    document = client.app.openapi()
    schemas = document["components"]["schemas"]
    response_schema = document["paths"][path][method]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    wrapper = schemas[response_schema["$ref"].rsplit("/", 1)[-1]]
    data_ref = next(
        item["$ref"]
        for item in wrapper["properties"]["data"]["anyOf"]
        if "$ref" in item
    )
    return schemas[data_ref.rsplit("/", 1)[-1]]


def test_context_routes_publish_typed_openapi_and_drop_unknown_fields(
    context_api, monkeypatch
) -> None:
    client, _factory, _base = context_api
    openapi_base = (
        "/api/v1/projects/{project_id}/ai/assistant/conversations/{conversation_id}"
    )
    cases = (
        (f"{openapi_base}/context-state", "get", "ConversationContextStateResponse"),
        (f"{openapi_base}/checkpoints", "get", "ConversationCheckpointListResponse"),
        (
            f"{openapi_base}/checkpoints/{{checkpoint_id}}",
            "get",
            "ConversationCheckpointDetailResponse",
        ),
        (
            f"{openapi_base}/checkpoints/{{checkpoint_id}}/cancel",
            "post",
            "ConversationContextStateResponse",
        ),
    )
    for path, method, title in cases:
        schema = _openapi_success_data_schema(client, path, method)
        assert schema["title"] == title
        assert schema["type"] == "object"

    state_schema = _openapi_success_data_schema(
        client, f"{openapi_base}/context-state", "get"
    )
    assert set(state_schema["required"]) >= {
        "status",
        "policy_version",
        "source_message_count",
        "recent_exact_turn_count",
        "original_history_tokens",
        "active_history_tokens",
        "trigger",
        "capacity_assurance",
        "retryable",
    }
    assert state_schema.get("additionalProperties") is not True

    original = __import__(
        "app.routers.conversation_context",
        fromlist=["_state_payload"],
    )._state_payload

    def state_with_future_field(*args, **kwargs):
        return {
            **original(*args, **kwargs),
            "future_internal_budget_field": {
                "tokens": 123,
                "provider_secret": "sk-must-not-leak",
            },
        }

    monkeypatch.setattr(
        "app.routers.conversation_context._state_payload",
        state_with_future_field,
    )
    response = client.get(
        "/api/v1/projects/project-1/ai/assistant/conversations/"
        "conversation-short/context-state"
    )
    assert response.status_code == 200
    serialized = json.dumps(response.json(), ensure_ascii=False)
    assert "future_internal_budget_field" not in serialized
    assert "sk-must-not-leak" not in serialized
