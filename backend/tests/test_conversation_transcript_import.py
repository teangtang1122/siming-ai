from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.exceptions import AppException, app_exception_handler
from app.database.models import Project
from app.database.session import Base, create_session_engine, get_db
from app.modules.assistant.infrastructure.models import (
    AssistantConversation,
    AssistantConversationReplica,
    AssistantMessage,
    AssistantTranscriptImportReceipt,
    SystemAssistantConversation,
    SystemAssistantMessage,
)
from app.routers.ai_writer import delete_assistant_conversation
from app.routers.conversation_transcript_import import router
from app.services.conversation_context.canonical import canonical_sha256
from app.services.workspace.transcript_import import (
    TranscriptImportCommand,
    TranscriptImportConflictError,
    TranscriptImportMessage,
    _request_hash,
    ensure_workspace_transcript_from_system_conversation,
    import_workspace_transcript,
    normalize_transcript_import_title,
    transcript_message_hash,
)


def _message(
    message_id: str,
    sequence_no: int,
    role: str,
    content: str,
    status: str = "completed",
) -> dict[str, object]:
    return {
        "id": message_id,
        "sequence_no": sequence_no,
        "role": role,
        "content": content,
        "status": status,
        "message_hash": transcript_message_hash(
            message_id=message_id,
            sequence_no=sequence_no,
            role=role,
            content=content,
            status=status,
        ),
    }


@pytest.fixture
def transcript_api() -> Iterator[tuple[TestClient, sessionmaker]]:
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
                Project(id="project-2", title="Other"),
            ]
        )
        db.commit()

    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(router, prefix="/api/v1")

    @app.middleware("http")
    async def inject_test_gateway_identity(request, call_next):
        device_id = request.headers.get("x-test-gateway-device")
        if device_id:
            request.state.gateway_device_id = device_id
        return await call_next(request)

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client, factory
    engine.dispose()


def _import_payload(
    *,
    client_id: str = "mobile-conversation-1",
    key: str = "import-1",
    messages: list[dict[str, object]] | None = None,
    revision: int | None = None,
    server_id: str | None = None,
    title: str | None = None,
) -> dict[str, object]:
    values = messages or [
        _message("mobile-user-1", 1, "user", "继续写下一章"),
        _message("mobile-assistant-1", 2, "assistant", "已完成上一章。"),
    ]
    result: dict[str, object] = {
        "client_conversation_id": client_id,
        "transcript_revision": revision or int(values[-1]["sequence_no"]),
        "idempotency_key": key,
        "messages": values,
    }
    if server_id:
        result["server_conversation_id"] = server_id
    if title is not None:
        result["title"] = title
    return result


def test_import_persists_exact_messages_and_retry_is_idempotent(transcript_api) -> None:
    client, factory = transcript_api
    path = "/api/v1/projects/project-1/ai/assistant/conversations/transcript-import"
    payload = _import_payload()

    first = client.post(path, json=payload)
    assert first.status_code == 200, first.text
    data = first.json()["data"]
    assert data["transcript_revision"] == 2
    assert data["applied_revision"] == 2
    assert data["imported_message_count"] == 2
    assert data["idempotent"] is False

    retry = client.post(path, json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.json()["data"] == {
        "conversation_id": data["conversation_id"],
        "transcript_revision": 2,
        "applied_revision": 2,
        "imported_message_count": 2,
        "idempotent": True,
    }

    with factory() as db:
        rows = (
            db.query(AssistantMessage)
            .filter(AssistantMessage.conversation_id == data["conversation_id"])
            .order_by(AssistantMessage.sequence_no)
            .all()
        )
        assert [(row.id, row.role, row.content, row.status) for row in rows] == [
            ("mobile-user-1", "user", "继续写下一章", "completed"),
            ("mobile-assistant-1", "assistant", "已完成上一章。", "completed"),
        ]
        assert db.query(AssistantConversationReplica).count() == 1
        assert db.query(AssistantTranscriptImportReceipt).count() == 1


def test_response_loss_title_change_uses_new_key_and_old_key_replays_without_overwrite(
    transcript_api,
) -> None:
    client, factory = transcript_api
    path = "/api/v1/projects/project-1/ai/assistant/conversations/transcript-import"
    original = _import_payload(key="title-snapshot-v1", title="　第一\r\n章 ")

    first = client.post(path, json=original)
    assert first.status_code == 200, first.text
    conversation_id = first.json()["data"]["conversation_id"]

    normalized_replay = client.post(
        path,
        json=_import_payload(key="title-snapshot-v1", title="第一 章"),
    )
    assert normalized_replay.status_code == 200, normalized_replay.text
    assert normalized_replay.json()["data"]["idempotent"] is True

    changed_same_key = client.post(
        path,
        json=_import_payload(key="title-snapshot-v1", title="第二章"),
    )
    assert changed_same_key.status_code == 409

    changed_new_key = client.post(
        path,
        json=_import_payload(key="title-snapshot-v2", title="第二章"),
    )
    assert changed_new_key.status_code == 200, changed_new_key.text
    assert changed_new_key.json()["data"] == {
        "conversation_id": conversation_id,
        "transcript_revision": 2,
        "applied_revision": 2,
        "imported_message_count": 0,
        "idempotent": True,
    }

    old_snapshot_replay = client.post(path, json=original)
    assert old_snapshot_replay.status_code == 200, old_snapshot_replay.text
    assert old_snapshot_replay.json()["data"]["conversation_id"] == conversation_id
    assert old_snapshot_replay.json()["data"]["idempotent"] is True

    with factory() as db:
        conversation = db.get(AssistantConversation, conversation_id)
        assert conversation is not None
        assert conversation.title == "第一 章"
        assert db.query(AssistantTranscriptImportReceipt).count() == 2
        rows = (
            db.query(AssistantMessage)
            .filter(AssistantMessage.conversation_id == conversation_id)
            .order_by(AssistantMessage.sequence_no)
            .all()
        )
        assert [row.content for row in rows] == ["继续写下一章", "已完成上一章。"]


def test_shared_mobile_import_key_fixture_covers_server_request_identity() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "fixtures"
        / "assistant-transcript-import-v1-interop.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))["import_key"]
    payload = fixture["payload"]

    assert normalize_transcript_import_title(fixture["source_title"]) == fixture[
        "normalized_title"
    ]
    assert set(payload) == {
        "schema",
        "project_id",
        "client_conversation_id",
        "server_conversation_id",
        "transcript_revision",
        "title",
        "messages",
    }
    assert canonical_sha256(payload) == fixture["sha256"]
    assert fixture["idempotency_key"] == f"mobile-transcript:{fixture['sha256']}"

    messages = tuple(
        TranscriptImportMessage(
            message_id=item["id"],
            sequence_no=item["sequence_no"],
            role=item["role"],
            content=item["content"],
            status=item["status"],
            message_hash=transcript_message_hash(
                message_id=item["id"],
                sequence_no=item["sequence_no"],
                role=item["role"],
                content=item["content"],
                status=item["status"],
            ),
        )
        for item in payload["messages"]
    )
    command = TranscriptImportCommand(
        project_id=payload["project_id"],
        device_scope="gateway:fixture",
        client_conversation_id=payload["client_conversation_id"],
        server_conversation_id=payload["server_conversation_id"],
        transcript_revision=payload["transcript_revision"],
        idempotency_key=fixture["idempotency_key"],
        title=payload["title"],
        messages=messages,
    )
    assert _request_hash(command) == fixture["server_request_hash"]


def test_receipt_replay_fails_closed_when_owner_join_is_inconsistent(transcript_api) -> None:
    client, factory = transcript_api
    path = "/api/v1/projects/project-1/ai/assistant/conversations/transcript-import"
    payload = _import_payload()
    created = client.post(path, json=payload)
    assert created.status_code == 200, created.text

    with factory() as db:
        foreign_conversation = AssistantConversation(
            id="foreign-receipt-conversation",
            project_id="project-2",
            title="Foreign",
            scope="project",
        )
        db.add(foreign_conversation)
        receipt = db.query(AssistantTranscriptImportReceipt).one()
        receipt.project_id = "project-2"
        receipt.assistant_conversation_id = foreign_conversation.id
        db.commit()

    replay = client.post(path, json=payload)
    assert replay.status_code == 409
    assert replay.json()["message"] == (
        "transcript import conflicts with the authenticated owner, device, or revision"
    )
    with factory() as db:
        assert db.query(AssistantConversationReplica).count() == 1
        assert db.query(AssistantTranscriptImportReceipt).count() == 1
        assert db.query(AssistantMessage).count() == 2


def test_production_sqlite_session_cascades_delete_and_allows_exact_reimport(tmp_path) -> None:
    engine = create_session_engine(f"sqlite:///{tmp_path / 'production-session.db'}")
    factory = sessionmaker(bind=engine, autoflush=False)
    Base.metadata.create_all(bind=engine)
    messages = tuple(
        TranscriptImportMessage(
            message_id=str(item["id"]),
            sequence_no=int(item["sequence_no"]),
            role=str(item["role"]),  # type: ignore[arg-type]
            content=str(item["content"]),
            status=str(item["status"]),  # type: ignore[arg-type]
            message_hash=str(item["message_hash"]),
        )
        for item in [
            _message("production-user", 1, "user", "继续"),
            _message("production-assistant", 2, "assistant", "完成"),
        ]
    )
    command = TranscriptImportCommand(
        project_id="project-1",
        device_scope="gateway:production-device",
        client_conversation_id="production-client",
        transcript_revision=2,
        idempotency_key="production-import",
        messages=messages,
    )
    try:
        with factory() as db:
            assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
            db.add(Project(id="project-1", title="Owned"))
            db.commit()
            first = import_workspace_transcript(db, command)
            db.commit()
            deleted_id = first.conversation_id
            asyncio.run(
                delete_assistant_conversation("project-1", deleted_id, db)
            )

            assert db.query(AssistantConversationReplica).count() == 0
            assert db.query(AssistantTranscriptImportReceipt).count() == 0
            assert db.query(AssistantMessage).count() == 0

            reimported = import_workspace_transcript(db, command)
            db.commit()
            assert reimported.conversation_id != deleted_id
            assert reimported.imported_message_count == 2
            assert reimported.idempotent is False
    finally:
        engine.dispose()


def test_increment_can_overlap_exact_prefix_and_only_appends_new_turn(transcript_api) -> None:
    client, factory = transcript_api
    path = "/api/v1/projects/project-1/ai/assistant/conversations/transcript-import"
    first = client.post(path, json=_import_payload()).json()["data"]
    messages = [
        _message("mobile-user-1", 1, "user", "继续写下一章"),
        _message("mobile-assistant-1", 2, "assistant", "已完成上一章。"),
        _message("mobile-user-2", 3, "user", "再检查时间线"),
        _message("mobile-assistant-2", 4, "assistant", "时间线一致。", "cancelled"),
    ]
    response = client.post(
        path,
        json=_import_payload(
            key="import-2",
            messages=messages,
            revision=4,
            server_id=first["conversation_id"],
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["imported_message_count"] == 2
    assert response.json()["data"]["transcript_revision"] == 4
    with factory() as db:
        rows = (
            db.query(AssistantMessage)
            .filter(AssistantMessage.conversation_id == first["conversation_id"])
            .order_by(AssistantMessage.sequence_no)
            .all()
        )
        assert [row.id for row in rows] == [
            "mobile-user-1",
            "mobile-assistant-1",
            "mobile-user-2",
            "mobile-assistant-2",
        ]
        assert rows[-1].status == "cancelled"


def test_idempotency_key_and_stable_message_conflicts_never_overwrite(transcript_api) -> None:
    client, factory = transcript_api
    path = "/api/v1/projects/project-1/ai/assistant/conversations/transcript-import"
    original = _import_payload()
    conversation_id = client.post(path, json=original).json()["data"]["conversation_id"]

    changed = _import_payload(
        messages=[
            _message("mobile-user-1", 1, "user", "被篡改的任务"),
            _message("mobile-assistant-1", 2, "assistant", "已完成上一章。"),
        ]
    )
    reused_key = client.post(path, json=changed)
    assert reused_key.status_code == 409

    changed["idempotency_key"] = "import-changed-message"
    changed["server_conversation_id"] = conversation_id
    changed_message = client.post(path, json=changed)
    assert changed_message.status_code == 409
    with factory() as db:
        stored = db.get(AssistantMessage, "mobile-user-1")
        assert stored is not None
        assert stored.content == "继续写下一章"


def test_client_mapping_is_project_scoped_and_hashes_are_mandatory(transcript_api) -> None:
    client, _factory = transcript_api
    base = "/api/v1/projects/{}/ai/assistant/conversations/transcript-import"
    assert client.post(base.format("project-1"), json=_import_payload()).status_code == 200

    foreign = client.post(
        base.format("project-2"),
        json=_import_payload(key="other-project-import"),
    )
    assert foreign.status_code == 409

    bad_hash = _import_payload(client_id="mobile-conversation-2", key="bad-hash")
    bad_hash["messages"][0]["message_hash"] = "0" * 64  # type: ignore[index]
    rejected = client.post(base.format("project-1"), json=bad_hash)
    assert rejected.status_code == 400


def test_partial_or_noncontiguous_turn_is_rejected(transcript_api) -> None:
    client, _factory = transcript_api
    path = "/api/v1/projects/project-1/ai/assistant/conversations/transcript-import"
    partial = _import_payload(
        client_id="partial",
        key="partial",
        messages=[_message("only-user", 1, "user", "未闭合")],
    )
    assert client.post(path, json=partial).status_code == 422

    noncontiguous_messages = [
        _message("gap-user", 3, "user", "有缺口"),
        _message("gap-assistant", 4, "assistant", "不能导入"),
    ]
    gap = client.post(
        path,
        json=_import_payload(
            client_id="gap",
            key="gap",
            messages=noncontiguous_messages,
            revision=4,
        ),
    )
    assert gap.status_code == 409


def test_same_client_namespace_is_isolated_between_authenticated_devices() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        db.add(Project(id="project-1", title="Owned"))
        db.commit()
        base_messages = tuple(
            TranscriptImportMessage(
                message_id=str(item["id"]),
                sequence_no=int(item["sequence_no"]),
                role=str(item["role"]),  # type: ignore[arg-type]
                content=str(item["content"]),
                status=str(item["status"]),  # type: ignore[arg-type]
                message_hash=str(item["message_hash"]),
            )
            for item in [
                _message("device-a-user", 1, "user", "A"),
                _message("device-a-assistant", 2, "assistant", "A done"),
            ]
        )
        first = import_workspace_transcript(
            db,
            TranscriptImportCommand(
                project_id="project-1",
                device_scope="gateway:device-a",
                client_conversation_id="same-local-id",
                transcript_revision=2,
                idempotency_key="same-key",
                messages=base_messages,
            ),
        )
        db.commit()
        device_b_messages = tuple(
            TranscriptImportMessage(
                message_id=str(item["id"]),
                sequence_no=int(item["sequence_no"]),
                role=str(item["role"]),  # type: ignore[arg-type]
                content=str(item["content"]),
                status=str(item["status"]),  # type: ignore[arg-type]
                message_hash=str(item["message_hash"]),
            )
            for item in [
                _message("device-b-user", 1, "user", "B"),
                _message("device-b-assistant", 2, "assistant", "B done"),
            ]
        )
        second = import_workspace_transcript(
            db,
            TranscriptImportCommand(
                project_id="project-1",
                device_scope="gateway:device-b",
                client_conversation_id="same-local-id",
                transcript_revision=2,
                idempotency_key="same-key",
                messages=device_b_messages,
            ),
        )
        db.commit()
        assert first.conversation_id != second.conversation_id


def test_server_mapping_probe_is_indistinguishable_across_devices(transcript_api) -> None:
    client, factory = transcript_api
    path = "/api/v1/projects/project-1/ai/assistant/conversations/transcript-import"
    created = client.post(
        path,
        headers={"x-test-gateway-device": "device-a"},
        json=_import_payload(key="device-a-import"),
    )
    assert created.status_code == 200
    server_id = created.json()["data"]["conversation_id"]

    foreign_mapping = client.post(
        path,
        headers={"x-test-gateway-device": "device-b"},
        json=_import_payload(
            client_id="device-b-conversation",
            key="device-b-foreign",
            server_id=server_id,
        ),
    )
    missing_mapping = client.post(
        path,
        headers={"x-test-gateway-device": "device-b"},
        json=_import_payload(
            client_id="device-b-conversation",
            key="device-b-missing",
            server_id="00000000-0000-0000-0000-000000000000",
        ),
    )

    assert foreign_mapping.status_code == missing_mapping.status_code == 409
    assert foreign_mapping.json()["message"] == missing_mapping.json()["message"]
    with factory() as db:
        assert db.query(AssistantMessage).count() == 2
        assert db.query(AssistantConversationReplica).count() == 1
        assert db.query(AssistantTranscriptImportReceipt).count() == 1


def test_system_conversation_bootstrap_imports_only_prior_closed_turns() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        db.add(Project(id="project-1", title="Owned"))
        source = SystemAssistantConversation(
            id="system-conversation",
            title="Project chat",
            scope_type="project",
            scope_id="project-1",
            project_id="project-1",
        )
        db.add(source)
        db.add_all(
            [
                SystemAssistantMessage(
                    id="system-user-1",
                    conversation_id=source.id,
                    sequence_no=1,
                    role="user",
                    content="历史任务",
                    status="completed",
                ),
                SystemAssistantMessage(
                    id="system-assistant-1",
                    conversation_id=source.id,
                    sequence_no=2,
                    role="assistant",
                    content="历史答复",
                    status="completed",
                ),
                SystemAssistantMessage(
                    id="system-user-current",
                    conversation_id=source.id,
                    sequence_no=3,
                    role="user",
                    content="当前任务",
                    status="completed",
                ),
                SystemAssistantMessage(
                    id="system-assistant-current",
                    conversation_id=source.id,
                    sequence_no=4,
                    role="assistant",
                    content="",
                    status="running",
                ),
            ]
        )
        db.commit()

        imported = ensure_workspace_transcript_from_system_conversation(
            db,
            project_id="project-1",
            system_conversation_id=source.id,
        )
        db.commit()
        assert imported.created is True
        assert imported.transcript_revision == 2
        assert imported.imported_message_count == 2
        assert imported.conversation.canonical_conversation_id == source.id
        rows = (
            db.query(AssistantMessage)
            .filter(AssistantMessage.conversation_id == imported.conversation.id)
            .order_by(AssistantMessage.sequence_no)
            .all()
        )
        assert [row.id for row in rows] == ["system-user-1", "system-assistant-1"]

        again = ensure_workspace_transcript_from_system_conversation(
            db,
            project_id="project-1",
            system_conversation_id=source.id,
        )
        assert again.created is False
        assert again.conversation.id == imported.conversation.id
        assert again.transcript_revision == 2


def test_system_conversation_owner_mismatch_is_hidden_as_conflict() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        db.add_all(
            [
                Project(id="project-1", title="Owned"),
                Project(id="project-2", title="Other"),
                SystemAssistantConversation(
                    id="foreign-system",
                    title="Foreign",
                    scope_type="project",
                    scope_id="project-2",
                    project_id="project-2",
                ),
            ]
        )
        db.commit()
        with pytest.raises(TranscriptImportConflictError):
            ensure_workspace_transcript_from_system_conversation(
                db,
                project_id="project-1",
                system_conversation_id="foreign-system",
            )


def test_transcript_import_publishes_typed_success_contract(transcript_api) -> None:
    client, _factory = transcript_api
    document = client.app.openapi()
    path = (
        "/api/v1/projects/{project_id}/ai/assistant/conversations/transcript-import"
    )
    response_schema = document["paths"][path]["post"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    schemas = document["components"]["schemas"]
    wrapper = schemas[response_schema["$ref"].rsplit("/", 1)[-1]]
    data_ref = next(
        item["$ref"]
        for item in wrapper["properties"]["data"]["anyOf"]
        if "$ref" in item
    )
    payload = schemas[data_ref.rsplit("/", 1)[-1]]
    assert payload["title"] == "TranscriptImportResponse"
    assert set(payload["required"]) == {
        "conversation_id",
        "transcript_revision",
        "applied_revision",
        "imported_message_count",
        "idempotent",
    }
    assert payload["properties"]["transcript_revision"]["type"] == "integer"
    assert payload["properties"]["idempotent"]["type"] == "boolean"
