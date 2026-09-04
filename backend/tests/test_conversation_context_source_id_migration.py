"""Migration contract for canonical Creation checkpoint source-run IDs."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from alembic import command
from app.database.bootstrap import alembic_config
from app.database.models import Project
from app.modules.assistant.infrastructure.models import (
    AssistantConversation,
    ConversationContextCheckpoint,
    ConversationContextCheckpointSource,
)

_HEAD = "300a32_context_source_ids"
_PARENT = "300a31_transcript_integrity"


def _source_id_length(engine) -> int | None:
    column = next(
        item
        for item in inspect(engine).get_columns("conversation_context_checkpoint_sources")
        if item["name"] == "source_id"
    )
    return getattr(column["type"], "length", None)


def test_300a31_widens_source_ids_and_round_trips_without_truncation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "context-source-ids.db"
    config = alembic_config(f"sqlite:///{database_path.as_posix()}")
    command.upgrade(config, _HEAD)

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _source_id_length(engine) == 128
    assistant_message_id = "11111111-1111-1111-1111-111111111111"
    canonical_source_id = f"creation-turn:{assistant_message_id}"
    assert len(canonical_source_id) > 36
    with Session(engine) as db:
        db.add(Project(id="project-1", title="Migration project"))
        db.add(
            AssistantConversation(
                id="conversation-1",
                project_id="project-1",
                title="Migration conversation",
            )
        )
        db.flush()
        db.add(
            ConversationContextCheckpoint(
                id="checkpoint-1",
                conversation_kind="workspace",
                assistant_conversation_id="conversation-1",
                idempotency_key="migration-checkpoint-attempt",
                source_first_sequence=1,
                source_last_sequence=2,
                source_message_count=2,
                source_hash="a" * 64,
                transcript_revision=2,
            )
        )
        db.flush()
        db.add(
            ConversationContextCheckpointSource(
                id="source-long",
                checkpoint_id="checkpoint-1",
                source_kind="run_step",
                source_id=canonical_source_id,
                source_hash="b" * 64,
            )
        )
        db.commit()
    engine.dispose()

    # PostgreSQL enforces VARCHAR(36); refuse a downgrade that would truncate
    # the canonical namespaced identity even though SQLite itself is permissive.
    with pytest.raises(RuntimeError, match="canonical Creation source-run IDs"):
        command.downgrade(config, _PARENT)

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _source_id_length(engine) == 128
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM conversation_context_checkpoint_sources WHERE id = 'source-long'")
        )
        connection.execute(
            text(
                "INSERT INTO conversation_context_checkpoint_sources "
                "(id, checkpoint_id, source_kind, source_id, source_hash, created_at) "
                "VALUES ('source-short', 'checkpoint-1', 'run_step', 'step-1', :hash, "
                "CURRENT_TIMESTAMP)"
            ),
            {"hash": "c" * 64},
        )
    engine.dispose()

    command.downgrade(config, _PARENT)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _source_id_length(engine) == 36
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT source_id FROM conversation_context_checkpoint_sources "
                    "WHERE id = 'source-short'"
                )
            ).scalar_one()
            == "step-1"
        )
    engine.dispose()

    command.upgrade(config, _HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _source_id_length(engine) == 128
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT source_id FROM conversation_context_checkpoint_sources "
                    "WHERE id = 'source-short'"
                )
            ).scalar_one()
            == "step-1"
        )
    engine.dispose()
