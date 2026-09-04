from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import models as _models  # noqa: F401
from app.database.models import Project
from app.database.session import Base
from app.modules.assistant.infrastructure.models import AssistantConversation
from app.services.persistence.assistant_workspace import SqlAlchemyAssistantWorkspace


def _factory(path: Path):
    engine = create_engine(
        f"sqlite:///{path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, connection_record) -> None:
        del connection_record
        dbapi_connection.execute("PRAGMA busy_timeout=10000")
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _seed_checkpoint(factory, *, status: str):
    with factory() as session:
        session.add(Project(id="project", title="Project"))
        session.add(
            AssistantConversation(
                id="conversation",
                project_id="project",
                title="Conversation",
            )
        )
        session.flush()
        store = SqlAlchemyAssistantWorkspace(session)
        checkpoint = store.create_context_checkpoint(
            "workspace",
            "conversation",
            owner_id="project",
            idempotency_key="attempt",
            source_first_sequence=1,
            source_last_sequence=2,
            source_message_count=2,
            source_hash="a" * 64,
            transcript_revision=3,
            model_binding_json={"provider": "openai", "model": "test"},
        )
        assert checkpoint is not None
        if status in {"compressing", "ready"}:
            assert store.update_context_checkpoint_status(
                "workspace",
                "conversation",
                checkpoint.id,
                "compressing",
                owner_id="project",
                expected_statuses=["pending"],
            )
        if status == "ready":
            assert store.update_context_checkpoint_status(
                "workspace",
                "conversation",
                checkpoint.id,
                "ready",
                owner_id="project",
                expected_statuses=["compressing"],
            )
            assert store.publish_context_checkpoint(
                "workspace",
                "conversation",
                checkpoint.id,
                0,
                owner_id="project",
            )
        session.commit()
        return checkpoint.id


def test_cancelled_checkpoint_cannot_be_overwritten_by_stale_generator(tmp_path: Path) -> None:
    engine, factory = _factory(tmp_path / "checkpoint-transition-cas.db")
    checkpoint_id = _seed_checkpoint(factory, status="compressing")

    with factory() as stale_session, factory() as cancel_session:
        stale_store = SqlAlchemyAssistantWorkspace(stale_session)
        cancel_store = SqlAlchemyAssistantWorkspace(cancel_session)
        # The generator loaded ``compressing`` before cancellation committed.
        stale = stale_store.context_checkpoint(
            "workspace", "conversation", checkpoint_id, owner_id="project"
        )
        assert stale is not None and stale.status == "compressing"

        cancelled = cancel_store.update_context_checkpoint_status(
            "workspace",
            "conversation",
            checkpoint_id,
            "cancelled",
            owner_id="project",
            expected_statuses=["compressing"],
        )
        assert cancelled is not None
        cancel_session.commit()

        assert (
            stale_store.update_context_checkpoint_status(
                "workspace",
                "conversation",
                checkpoint_id,
                "ready",
                owner_id="project",
                expected_statuses=["compressing"],
            )
            is None
        )
        stale_session.rollback()

    with factory() as verify:
        record = SqlAlchemyAssistantWorkspace(verify).context_checkpoint(
            "workspace", "conversation", checkpoint_id, owner_id="project"
        )
        assert record is not None and record.status == "cancelled"
    engine.dispose()


def test_only_one_stale_reader_can_cas_clear_the_active_checkpoint(tmp_path: Path) -> None:
    engine, factory = _factory(tmp_path / "checkpoint-invalidate-cas.db")
    checkpoint_id = _seed_checkpoint(factory, status="ready")

    with factory() as first_session, factory() as second_session:
        first_store = SqlAlchemyAssistantWorkspace(first_session)
        second_store = SqlAlchemyAssistantWorkspace(second_session)
        first_state = first_store.context_state("workspace", "conversation", owner_id="project")
        second_state = second_store.context_state("workspace", "conversation", owner_id="project")
        assert first_state is not None and second_state is not None
        expected_revision = first_state.revision
        assert second_state.revision == expected_revision

        assert first_store.invalidate_active_context_checkpoint(
            "workspace",
            "conversation",
            checkpoint_id,
            expected_revision,
            owner_id="project",
            error_code="conversation_source_changed",
            error_detail="source hash changed",
        )
        first_session.commit()

        assert not second_store.invalidate_active_context_checkpoint(
            "workspace",
            "conversation",
            checkpoint_id,
            expected_revision,
            owner_id="project",
            error_code="conversation_source_changed",
            error_detail="late stale reader",
        )
        second_session.rollback()

    with factory() as verify:
        store = SqlAlchemyAssistantWorkspace(verify)
        state = store.context_state("workspace", "conversation", owner_id="project")
        record = store.context_checkpoint(
            "workspace", "conversation", checkpoint_id, owner_id="project"
        )
        assert state is not None
        assert state.active_checkpoint_id is None
        assert state.active_source_last_sequence == 0
        assert state.revision == expected_revision + 1
        assert record is not None and record.status == "superseded"
        assert record.error_detail == "source hash changed"
    engine.dispose()


def test_ready_publish_loser_is_retired_without_clearing_concurrent_winner(
    tmp_path: Path,
) -> None:
    engine, factory = _factory(tmp_path / "checkpoint-ready-loser-cas.db")
    original_id = _seed_checkpoint(factory, status="ready")

    with factory() as setup:
        store = SqlAlchemyAssistantWorkspace(setup)

        def ready_attempt(key: str, first_sequence: int, source_hash: str) -> str:
            checkpoint = store.create_context_checkpoint(
                "workspace",
                "conversation",
                owner_id="project",
                idempotency_key=key,
                source_first_sequence=first_sequence,
                source_last_sequence=first_sequence + 1,
                source_message_count=2,
                source_hash=source_hash,
                transcript_revision=first_sequence + 1,
                model_binding_json={"provider": "openai", "model": "test"},
            )
            assert checkpoint is not None
            assert store.update_context_checkpoint_status(
                "workspace",
                "conversation",
                checkpoint.id,
                "compressing",
                owner_id="project",
                expected_statuses=["pending"],
            )
            assert store.update_context_checkpoint_status(
                "workspace",
                "conversation",
                checkpoint.id,
                "ready",
                owner_id="project",
                expected_statuses=["compressing"],
            )
            return checkpoint.id

        loser_id = ready_attempt("loser", 3, "b" * 64)
        winner_id = ready_attempt("winner", 5, "c" * 64)
        setup.commit()

    with factory() as winner_session:
        winner_store = SqlAlchemyAssistantWorkspace(winner_session)
        assert winner_store.publish_context_checkpoint(
            "workspace",
            "conversation",
            winner_id,
            1,
            owner_id="project",
        )
        winner_session.commit()

    with factory() as loser_session, factory() as late_loser_session:
        loser_store = SqlAlchemyAssistantWorkspace(loser_session)
        late_loser_store = SqlAlchemyAssistantWorkspace(late_loser_session)
        first_state = loser_store.context_state(
            "workspace", "conversation", owner_id="project"
        )
        late_state = late_loser_store.context_state(
            "workspace", "conversation", owner_id="project"
        )
        assert first_state is not None and first_state.revision == 2
        assert late_state is not None and late_state.revision == 2

        assert loser_store.supersede_inactive_context_checkpoint(
            "workspace",
            "conversation",
            loser_id,
            2,
            owner_id="project",
            error_code="conversation_checkpoint_superseded",
            error_detail="lost publish CAS",
        )
        loser_session.commit()
        # A concurrent cleanup that inspected the same active+revision loses
        # the CAS and cannot bump state or disturb the active winner.
        assert not late_loser_store.supersede_inactive_context_checkpoint(
            "workspace",
            "conversation",
            loser_id,
            2,
            owner_id="project",
            error_code="conversation_checkpoint_superseded",
            error_detail="late cleanup",
        )
        late_loser_session.rollback()

    with factory() as verify:
        store = SqlAlchemyAssistantWorkspace(verify)
        state = store.context_state("workspace", "conversation", owner_id="project")
        original = store.context_checkpoint(
            "workspace", "conversation", original_id, owner_id="project"
        )
        loser = store.context_checkpoint(
            "workspace", "conversation", loser_id, owner_id="project"
        )
        winner = store.context_checkpoint(
            "workspace", "conversation", winner_id, owner_id="project"
        )
        assert state is not None
        assert state.active_checkpoint_id == winner_id
        assert state.active_source_last_sequence == 6
        assert state.revision == 3
        assert original is not None and original.status == "superseded"
        assert loser is not None and loser.status == "superseded"
        assert loser.error_detail == "lost publish CAS"
        assert winner is not None and winner.status == "ready"
        assert not store.publish_context_checkpoint(
            "workspace",
            "conversation",
            loser_id,
            2,
            owner_id="project",
        )
        assert not store.supersede_inactive_context_checkpoint(
            "workspace",
            "conversation",
            winner_id,
            3,
            owner_id="project",
            error_code="should-not-apply",
            error_detail="active winner",
        )
    engine.dispose()
