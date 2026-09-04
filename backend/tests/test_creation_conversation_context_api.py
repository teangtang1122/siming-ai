"""Durable, owner-scoped REST views for Creation Agent checkpoints."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.bootstrap.composition import configure_application_services
from app.core.exceptions import AppException, app_exception_handler
from app.database.session import Base, get_db
from app.modules.assistant.infrastructure.models import (
    SystemAssistantConversation,
    SystemAssistantMessage,
)
from app.modules.creation.infrastructure.models import NovelCreationSession
from app.routers.novel_creation_context import router
from app.services.persistence.assistant_workspace import SqlAlchemyAssistantWorkspace


@pytest.fixture
def creation_context_api() -> Iterator[tuple[TestClient, sessionmaker]]:
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
                NovelCreationSession(
                    id="creation-session-1",
                    status="drafting",
                    mode="internal_llm",
                ),
                NovelCreationSession(
                    id="creation-session-2",
                    status="drafting",
                    mode="internal_llm",
                ),
                SystemAssistantConversation(
                    id="creation-conversation-1",
                    title="Owned creation chat",
                    scope_type="creation",
                    scope_id="creation-session-1",
                    creation_session_id="creation-session-1",
                ),
                SystemAssistantConversation(
                    id="creation-conversation-2",
                    title="Foreign creation chat",
                    scope_type="creation",
                    scope_id="creation-session-2",
                    creation_session_id="creation-session-2",
                ),
            ]
        )
        db.add_all(
            [
                SystemAssistantMessage(
                    id="creation-user-1",
                    conversation_id="creation-conversation-1",
                    sequence_no=1,
                    role="user",
                    content="保留作者原设。",
                    status="completed",
                ),
                SystemAssistantMessage(
                    id="creation-assistant-1",
                    conversation_id="creation-conversation-1",
                    sequence_no=2,
                    role="assistant",
                    content="已记录。",
                    status="completed",
                ),
            ]
        )
        db.commit()

        store = SqlAlchemyAssistantWorkspace(db)
        checkpoint = store.create_context_checkpoint(
            "creation",
            "creation-conversation-1",
            owner_id="creation-session-1",
            id="creation-checkpoint-1",
            idempotency_key="creation-attempt-1",
            source_first_sequence=1,
            source_last_sequence=2,
            source_message_count=2,
            source_hash="c" * 64,
            transcript_revision=2,
            model_binding_json={
                "provider": "openai",
                "normalized_model": "openai:gpt-test",
            },
        )
        assert checkpoint is not None
        assert store.update_context_checkpoint_status(
            "creation",
            "creation-conversation-1",
            checkpoint.id,
            "compressing",
            owner_id="creation-session-1",
            expected_statuses=["pending"],
        ) is not None
        assert store.update_context_checkpoint_status(
            "creation",
            "creation-conversation-1",
            checkpoint.id,
            "ready",
            owner_id="creation-session-1",
            expected_statuses=["compressing"],
            semantic_navigation_json={
                "authority": "non_authoritative_navigation",
                "current_objectives": ["继续立项"],
            },
            original_tokens=80_000,
            checkpoint_tokens=5_000,
        ) is not None
        state = store.context_state(
            "creation",
            "creation-conversation-1",
            owner_id="creation-session-1",
        )
        assert state is not None
        assert store.publish_context_checkpoint(
            "creation",
            "creation-conversation-1",
            checkpoint.id,
            state.revision,
            owner_id="creation-session-1",
            last_budget_json={
                "trigger": "projected_next_step_over_capacity",
                "capacity_assurance": "exact",
                "recent_exact_turn_count": 4,
                "original_history_tokens": 80_000,
                "active_history_tokens": 18_000,
            },
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
        yield client, factory
    engine.dispose()


def test_creation_context_survives_reopen_with_typed_detail(
    creation_context_api,
) -> None:
    client, _factory = creation_context_api
    base = (
        "/api/v1/novel-creation/sessions/creation-session-1/conversations/"
        "creation-conversation-1"
    )

    state = client.get(f"{base}/context-state")
    assert state.status_code == 200
    assert state.json()["data"]["active_checkpoint_id"] == "creation-checkpoint-1"
    assert state.json()["data"]["original_history_tokens"] == 80_000

    listing = client.get(f"{base}/checkpoints")
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] == 1
    assert listing.json()["data"]["items"][0]["scope"] == "creation"
    assert "semantic_navigation" not in listing.json()["data"]["items"][0]

    detail = client.get(f"{base}/checkpoints/creation-checkpoint-1")
    assert detail.status_code == 200
    payload = detail.json()["data"]
    assert payload["scope"] == "creation"
    assert payload["semantic_navigation"]["authority"] == (
        "non_authoritative_navigation"
    )
    assert payload["source_range"] == {
        "first_sequence": 1,
        "last_sequence": 2,
        "message_count": 2,
        "source_hash": "c" * 64,
    }


def test_creation_context_hides_foreign_session_and_checkpoint_ids(
    creation_context_api,
) -> None:
    client, _factory = creation_context_api
    foreign_conversation = (
        "/api/v1/novel-creation/sessions/creation-session-1/conversations/"
        "creation-conversation-2/context-state"
    )
    missing_session = (
        "/api/v1/novel-creation/sessions/missing-session/conversations/"
        "creation-conversation-1/context-state"
    )
    own_base = (
        "/api/v1/novel-creation/sessions/creation-session-1/conversations/"
        "creation-conversation-1"
    )

    assert client.get(foreign_conversation).status_code == 404
    assert client.get(missing_session).status_code == 404
    assert client.get(f"{own_base}/checkpoints/unknown-checkpoint").status_code == 404


def test_creation_context_routes_publish_typed_openapi(creation_context_api) -> None:
    client, _factory = creation_context_api
    document = client.app.openapi()
    schemas = document["components"]["schemas"]
    base = (
        "/api/v1/novel-creation/sessions/{session_id}/conversations/"
        "{conversation_id}"
    )
    cases = (
        (f"{base}/context-state", "get", "ConversationContextStateResponse"),
        (f"{base}/checkpoints", "get", "ConversationCheckpointListResponse"),
        (
            f"{base}/checkpoints/{{checkpoint_id}}",
            "get",
            "ConversationCheckpointDetailResponse",
        ),
    )
    for path, method, expected in cases:
        response = document["paths"][path][method]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        wrapper = schemas[response["$ref"].rsplit("/", 1)[-1]]
        data_ref = next(
            item["$ref"]
            for item in wrapper["properties"]["data"]["anyOf"]
            if "$ref" in item
        )
        assert schemas[data_ref.rsplit("/", 1)[-1]]["title"] == expected
