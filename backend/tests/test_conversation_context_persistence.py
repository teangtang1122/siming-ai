from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.database import models as _models  # noqa: F401
from app.database.bootstrap import alembic_config
from app.database.models import Project
from app.database.session import Base
from app.modules.assistant.infrastructure.models import (
    AssistantConversation,
    ConversationContextCheckpoint,
    ConversationContextCheckpointSource,
    ConversationContextState,
    SystemAssistantConversation,
)
from app.services.persistence.assistant_workspace import SqlAlchemyAssistantWorkspace


@pytest.fixture
def database_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record) -> None:
        del connection_record
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _workspace_conversation(
    session: Session,
    *,
    project_id: str = "project-1",
    conversation_id: str = "conversation-1",
) -> AssistantConversation:
    project = Project(id=project_id, title=project_id)
    conversation = AssistantConversation(
        id=conversation_id,
        project_id=project_id,
        title="Context test",
    )
    session.add_all([project, conversation])
    session.flush()
    return conversation


def _checkpoint_values(key: str, source_hash: str = "a" * 64) -> dict[str, object]:
    return {
        "idempotency_key": key,
        "source_first_sequence": 1,
        "source_last_sequence": 2,
        "source_message_count": 2,
        "source_hash": source_hash,
        "transcript_revision": 2,
        "model_binding_json": {"provider": "openai", "model": "gpt-test"},
    }


def test_workspace_messages_receive_stable_consecutive_sequences(
    database_session: Session,
) -> None:
    conversation = _workspace_conversation(database_session)
    repository = SqlAlchemyAssistantWorkspace(database_session)

    user = repository.create_message(
        conversation_id=conversation.id,
        role="user",
        content="first",
        status="completed",
    )
    assistant = repository.create_message(
        conversation_id=conversation.id,
        role="assistant",
        content="second",
        status="completed",
    )

    assert (user.sequence_no, assistant.sequence_no) == (1, 2)
    assert [item.id for item in repository.conversation_messages(conversation.id)] == [
        user.id,
        assistant.id,
    ]
    with pytest.raises(ValueError, match="next conversation sequence"):
        repository.create_message(
            conversation_id=conversation.id,
            role="user",
            sequence_no=8,
            content="invalid gap",
        )


def test_concurrent_workspace_turns_reserve_contiguous_sequence_pairs(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "concurrent-sequence.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, connection_record) -> None:
        del connection_record
        dbapi_connection.execute("PRAGMA busy_timeout=10000")
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as setup:
        _workspace_conversation(setup)
        setup.commit()
    barrier = Barrier(2)

    def _persist_turn(label: str) -> tuple[int, int]:
        with factory() as session:
            repository = SqlAlchemyAssistantWorkspace(session)
            barrier.wait(timeout=10)
            user = repository.create_message(
                conversation_id="conversation-1",
                role="user",
                content=f"{label}-user",
            )
            assistant = repository.create_message(
                conversation_id="conversation-1",
                role="assistant",
                content=f"{label}-assistant",
            )
            session.commit()
            return user.sequence_no, assistant.sequence_no

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_persist_turn, "first"),
            executor.submit(_persist_turn, "second"),
        ]
        pairs = sorted(future.result() for future in futures)
    assert pairs == [(1, 2), (3, 4)]
    engine.dispose()


def test_checkpoint_sources_owner_validation_and_cas_publication(
    database_session: Session,
) -> None:
    conversation = _workspace_conversation(database_session)
    other_project = Project(id="project-2", title="project-2")
    other_conversation = AssistantConversation(
        id="conversation-2",
        project_id=other_project.id,
        title="Other",
    )
    database_session.add_all([other_project, other_conversation])
    database_session.flush()
    repository = SqlAlchemyAssistantWorkspace(database_session)
    user = repository.create_message(
        conversation_id=conversation.id,
        role="user",
        content="plan",
    )
    assistant = repository.create_message(
        conversation_id=conversation.id,
        role="assistant",
        content="proposal",
    )
    foreign_message = repository.create_message(
        conversation_id=other_conversation.id,
        role="user",
        content="foreign",
    )

    first = repository.create_context_checkpoint(
        "workspace",
        conversation.id,
        owner_id="project-1",
        **_checkpoint_values("checkpoint-attempt-1"),
    )
    assert first is not None
    assert (
        repository.context_checkpoint(
            "workspace",
            conversation.id,
            first.id,
            owner_id="project-2",
        )
        is None
    )
    assert (
        repository.add_context_checkpoint_sources(
            "workspace",
            conversation.id,
            first.id,
            [
                {
                    "source_kind": "message",
                    "source_id": foreign_message.id,
                    "source_sequence": foreign_message.sequence_no,
                    "source_hash": "b" * 64,
                }
            ],
            owner_id="project-1",
        )
        is None
    )
    sources = repository.add_context_checkpoint_sources(
        "workspace",
        conversation.id,
        first.id,
        [
            {
                "source_kind": "message",
                "source_id": user.id,
                "source_sequence": user.sequence_no,
                "source_hash": "c" * 64,
            },
            {
                "source_kind": "message",
                "source_id": assistant.id,
                "source_sequence": assistant.sequence_no,
                "source_hash": "d" * 64,
            },
        ],
        owner_id="project-1",
    )
    assert sources is not None
    assert [source.source_sequence for source in sources] == [1, 2]

    assert (
        repository.update_context_checkpoint_status(
            "workspace",
            conversation.id,
            first.id,
            "compressing",
            owner_id="project-1",
            expected_statuses=["pending"],
        )
        is not None
    )
    assert (
        repository.update_context_checkpoint_status(
            "workspace",
            conversation.id,
            first.id,
            "ready",
            owner_id="project-1",
            expected_statuses=["compressing"],
            semantic_navigation_json={"authority": "non_authoritative_navigation"},
            original_tokens=80_000,
            checkpoint_tokens=12_000,
        )
        is not None
    )
    assert repository.publish_context_checkpoint(
        "workspace",
        conversation.id,
        first.id,
        0,
        owner_id="project-1",
        last_budget_json={"capacity_assurance": "exact"},
    )
    state = repository.context_state("workspace", conversation.id, owner_id="project-1")
    assert state is not None
    assert state.revision == 1
    assert state.active_checkpoint_id == first.id
    assert state.active_source_last_sequence == 2

    second = repository.create_context_checkpoint(
        "workspace",
        conversation.id,
        owner_id="project-1",
        parent_checkpoint_id=first.id,
        **_checkpoint_values("checkpoint-attempt-2", "e" * 64),
    )
    assert second is not None
    assert (
        repository.update_context_checkpoint_status(
            "workspace",
            conversation.id,
            second.id,
            "compressing",
            owner_id="project-1",
        )
        is not None
    )
    assert (
        repository.update_context_checkpoint_status(
            "workspace",
            conversation.id,
            second.id,
            "ready",
            owner_id="project-1",
        )
        is not None
    )
    assert not repository.publish_context_checkpoint(
        "workspace",
        conversation.id,
        second.id,
        0,
        owner_id="project-1",
    )
    assert repository.publish_context_checkpoint(
        "workspace",
        conversation.id,
        second.id,
        1,
        owner_id="project-1",
    )
    database_session.refresh(first)
    database_session.refresh(state)
    assert first.status == "superseded"
    assert state.revision == 2
    assert state.active_checkpoint_id == second.id


def test_creation_owner_and_conversation_cascade_are_enforced(
    database_session: Session,
) -> None:
    conversation = SystemAssistantConversation(
        id="creation-conversation",
        title="Creation",
        scope_type="creation",
        scope_id="creation-session-1",
        creation_session_id="creation-session-1",
    )
    database_session.add(conversation)
    database_session.flush()
    repository = SqlAlchemyAssistantWorkspace(database_session)
    checkpoint = repository.create_context_checkpoint(
        "creation",
        conversation.id,
        owner_id="creation-session-1",
        **_checkpoint_values("creation-checkpoint"),
    )
    assert checkpoint is not None
    assert repository.context_state("creation", conversation.id, owner_id="wrong-session") is None
    database_session.delete(conversation)
    database_session.flush()

    assert database_session.query(ConversationContextCheckpoint).count() == 0
    assert database_session.query(ConversationContextCheckpointSource).count() == 0
    assert database_session.query(ConversationContextState).count() == 0


def test_database_rejects_ambiguous_checkpoint_owner(database_session: Session) -> None:
    workspace = _workspace_conversation(database_session)
    system = SystemAssistantConversation(
        id="system-conversation",
        title="Creation",
        scope_type="creation",
        scope_id="creation-session",
        creation_session_id="creation-session",
    )
    database_session.add(system)
    database_session.flush()
    database_session.add(
        ConversationContextCheckpoint(
            conversation_kind="workspace",
            assistant_conversation_id=workspace.id,
            system_conversation_id=system.id,
            idempotency_key="ambiguous-owner",
            source_first_sequence=1,
            source_last_sequence=1,
            source_message_count=1,
            source_hash="f" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        database_session.flush()


def _create_pre_context_schema(database_path: Path) -> None:
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE assistant_conversations ("
                "id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL, "
                "title VARCHAR(200) NOT NULL, scope VARCHAR(50) NOT NULL, "
                "canonical_conversation_id VARCHAR(36), model VARCHAR(512), "
                "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE assistant_messages ("
                "id VARCHAR(36) PRIMARY KEY, conversation_id VARCHAR(36) NOT NULL, "
                "role VARCHAR(20) NOT NULL, content TEXT NOT NULL, payload_json TEXT, "
                "status VARCHAR(20) NOT NULL, created_at DATETIME NOT NULL, "
                "updated_at DATETIME NOT NULL, "
                "FOREIGN KEY(conversation_id) REFERENCES assistant_conversations(id) "
                "ON DELETE CASCADE)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE system_assistant_conversations ("
                "id VARCHAR(36) PRIMARY KEY, title VARCHAR(200) NOT NULL, "
                "scope_type VARCHAR(30) NOT NULL, scope_id VARCHAR(36), "
                "project_id VARCHAR(36), creation_session_id VARCHAR(36), "
                "user_brief TEXT, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE system_assistant_messages ("
                "id VARCHAR(36) PRIMARY KEY, conversation_id VARCHAR(36) NOT NULL, "
                "role VARCHAR(20) NOT NULL, content TEXT NOT NULL, run_id VARCHAR(36), "
                "operation_id VARCHAR(36), message_type VARCHAR(30) NOT NULL, "
                "payload_json JSON, status VARCHAR(20) NOT NULL, "
                "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
                "FOREIGN KEY(conversation_id) REFERENCES system_assistant_conversations(id) "
                "ON DELETE CASCADE)"
            )
        )
        timestamp = "2026-08-29 12:00:00"
        connection.execute(
            text(
                "INSERT INTO assistant_conversations VALUES "
                "('workspace', 'project', 'Workspace', 'writer', NULL, NULL, :ts, :ts)"
            ),
            {"ts": timestamp},
        )
        connection.execute(
            text(
                "INSERT INTO assistant_messages VALUES "
                "('assistant-1', 'workspace', 'assistant', 'a', NULL, 'completed', :ts, :ts), "
                "('user-1', 'workspace', 'user', 'u', NULL, 'completed', :ts, :ts), "
                "('user-2', 'workspace', 'user', 'u2', NULL, 'completed', :later, :later)"
            ),
            {"ts": timestamp, "later": "2026-08-29 12:01:00"},
        )
        connection.execute(
            text(
                "INSERT INTO system_assistant_conversations VALUES "
                "('creation', 'Creation', 'creation', 'session', NULL, 'session', "
                "NULL, :ts, :ts)"
            ),
            {"ts": timestamp},
        )
        connection.execute(
            text(
                "INSERT INTO system_assistant_messages VALUES "
                "('system-a', 'creation', 'assistant', 'a', NULL, NULL, 'text', NULL, "
                "'completed', :ts, :ts), "
                "('system-u', 'creation', 'user', 'u', NULL, NULL, 'text', NULL, "
                "'completed', :ts, :ts)"
            ),
            {"ts": timestamp},
        )
    engine.dispose()


def test_300a27_migration_backfills_stable_sequence_and_checkpoint_schema(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "pre-context.db"
    _create_pre_context_schema(database_path)
    config = alembic_config(f"sqlite:///{database_path.as_posix()}")
    command.stamp(config, "300a26_outline_drafts")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    assert {
        "conversation_context_checkpoints",
        "conversation_context_checkpoint_sources",
        "conversation_context_states",
    }.issubset(inspector.get_table_names())
    assert not {column["name"]: column for column in inspector.get_columns("assistant_messages")}[
        "sequence_no"
    ]["nullable"]
    assert "uq_assistant_messages_conversation_sequence" in {
        constraint["name"] for constraint in inspector.get_unique_constraints("assistant_messages")
    }
    with engine.connect() as connection:
        workspace_rows = connection.execute(
            text("SELECT id, sequence_no FROM assistant_messages ORDER BY sequence_no")
        ).all()
        creation_rows = connection.execute(
            text("SELECT id, sequence_no FROM system_assistant_messages ORDER BY sequence_no")
        ).all()
    # Timestamp ties retain the database's stable insertion order. In
    # particular, migration must never invent a user-before-assistant role
    # ordering, because that would rewrite the historical protocol sequence.
    assert workspace_rows == [("assistant-1", 1), ("user-1", 2), ("user-2", 3)]
    assert creation_rows == [("system-a", 1), ("system-u", 2)]

    command.downgrade(config, "300a26_outline_drafts")
    inspector = inspect(engine)
    assert "conversation_context_checkpoints" not in inspector.get_table_names()
    assert "sequence_no" not in {
        column["name"] for column in inspector.get_columns("assistant_messages")
    }
    engine.dispose()
