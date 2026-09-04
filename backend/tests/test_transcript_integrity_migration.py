"""Upgrade coverage for legacy transcript rows created with SQLite FKs disabled."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from alembic import command
from app.database.bootstrap import alembic_config
from app.database.models import (
    AssistantConversation,
    AssistantConversationReplica,
    AssistantTranscriptImportReceipt,
    DataIntegrityQuarantine,
    DataIntegrityQuarantineBatch,
    Project,
)
from app.database.session import Base, create_session_engine
from app.services.workspace.transcript_import import (
    TranscriptImportCommand,
    TranscriptImportMessage,
    import_workspace_transcript,
    transcript_message_hash,
)


def _message(message_id: str, sequence_no: int, role: str, content: str):
    status = "completed"
    return TranscriptImportMessage(
        message_id=message_id,
        sequence_no=sequence_no,
        role=role,  # type: ignore[arg-type]
        content=content,
        status=status,
        message_hash=transcript_message_hash(
            message_id=message_id,
            sequence_no=sequence_no,
            role=role,
            content=content,
            status=status,
        ),
    )


def test_upgrade_quarantines_orphans_and_preserves_namespace_for_reimport(tmp_path) -> None:
    database_path = tmp_path / "legacy-orphans.db"
    url = f"sqlite:///{database_path.as_posix()}"
    config = alembic_config(url)
    command.upgrade(config, "300a30_direct_mcp_integrity")

    legacy_engine = create_engine(url)
    try:
        with Session(legacy_engine) as db:
            assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 0
            project = Project(id="legacy-project", title="Legacy")
            conversation = AssistantConversation(
                id="deleted-conversation",
                project_id=project.id,
                title="Deleted by 3.3.8",
                scope="project",
            )
            replica = AssistantConversationReplica(
                id="orphan-replica",
                project_id=project.id,
                assistant_conversation_id=conversation.id,
                device_scope="gateway:legacy-device",
                client_conversation_id="legacy-client",
            )
            receipt = AssistantTranscriptImportReceipt(
                id="orphan-receipt",
                project_id=project.id,
                assistant_conversation_id=conversation.id,
                replica_id=replica.id,
                device_scope=replica.device_scope,
                idempotency_key="legacy-key",
                request_hash="a" * 64,
                source_transcript_revision=2,
                source_first_sequence=1,
                source_last_sequence=2,
                source_message_count=2,
                imported_message_count=2,
                result_transcript_revision=2,
                created_at=datetime(2025, 1, 2, 3, 4, 5),
            )
            db.add_all([project, conversation, replica, receipt])
            db.commit()
            db.delete(conversation)
            db.commit()
            assert db.get(AssistantConversationReplica, replica.id) is not None
            assert db.get(AssistantTranscriptImportReceipt, receipt.id) is not None
    finally:
        legacy_engine.dispose()

    command.upgrade(config, "300a31_transcript_integrity")

    managed_engine = create_session_engine(url)
    try:
        with Session(managed_engine) as db:
            assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
            assert db.query(AssistantConversationReplica).count() == 0
            assert db.query(AssistantTranscriptImportReceipt).count() == 0

            rows = db.query(DataIntegrityQuarantine).order_by(
                DataIntegrityQuarantine.source_table
            ).all()
            assert len(rows) == 2
            by_table = {row.source_table: row for row in rows}
            replica_copy = by_table["assistant_conversation_replicas"]
            receipt_copy = by_table["assistant_transcript_import_receipts"]
            assert "missing_conversation" in replica_copy.reason
            assert "missing_conversation" in receipt_copy.reason
            assert json.loads(replica_copy.payload_json) == {
                "assistant_conversation_id": "deleted-conversation",
                "client_conversation_id": "legacy-client",
                "created_at": json.loads(replica_copy.payload_json)["created_at"],
                "device_scope": "gateway:legacy-device",
                "id": "orphan-replica",
                "project_id": "legacy-project",
                "updated_at": json.loads(replica_copy.payload_json)["updated_at"],
            }
            receipt_payload = json.loads(receipt_copy.payload_json)
            assert receipt_payload["id"] == "orphan-receipt"
            assert receipt_payload["request_hash"] == "a" * 64
            assert receipt_payload["assistant_conversation_id"] == "deleted-conversation"
            assert receipt_payload["replica_id"] == "orphan-replica"
            assert receipt_payload["created_at"] == "2025-01-02 03:04:05.000000"

            batch = db.get(DataIntegrityQuarantineBatch, "300a31_transcript_integrity")
            assert batch is not None
            assert batch.quarantined_receipt_count == 1
            assert batch.quarantined_replica_count == 1

            reimported = import_workspace_transcript(
                db,
                TranscriptImportCommand(
                    project_id="legacy-project",
                    device_scope="gateway:legacy-device",
                    client_conversation_id="legacy-client",
                    transcript_revision=2,
                    idempotency_key="legacy-key",
                    messages=(
                        _message("restored-user", 1, "user", "恢复"),
                        _message("restored-assistant", 2, "assistant", "完成"),
                    ),
                ),
            )
            db.commit()
            assert reimported.imported_message_count == 2
            assert reimported.idempotent is False
            assert db.query(AssistantConversationReplica).count() == 1
            assert db.query(AssistantTranscriptImportReceipt).count() == 1
    finally:
        managed_engine.dispose()


def test_production_memory_engine_enables_foreign_keys() -> None:
    engine = create_session_engine("sqlite:///:memory:")
    try:
        # Production foreign-key enforcement must remain compatible with the
        # metadata lifecycle used by isolated test and recovery databases.
        # The chapter/outline relationship is cyclic, so its declared
        # ``use_alter`` edge is what keeps SQLAlchemy's drop order valid.
        Base.metadata.create_all(engine)
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        Base.metadata.drop_all(engine)
    finally:
        engine.dispose()


def test_alembic_rejects_supplied_sqlite_transaction_when_foreign_keys_cannot_enable(
    tmp_path,
) -> None:
    database_path = tmp_path / "active-transaction.db"
    url = f"sqlite:///{database_path.as_posix()}"
    command.upgrade(alembic_config(url), "300a30_direct_mcp_integrity")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text(
                    "UPDATE siming_schema_metadata SET value = value "
                    "WHERE key = 'schema_epoch'"
                )
            )
            config = alembic_config(url)
            config.attributes["connection"] = connection
            with pytest.raises(
                RuntimeError,
                match="SQLite migration connection has foreign keys disabled",
            ):
                command.upgrade(config, "300a31_transcript_integrity")
            transaction.rollback()
    finally:
        engine.dispose()


def test_upgrade_rejects_nonunique_quarantine_table_drift(tmp_path) -> None:
    database_path = tmp_path / "quarantine-drift.db"
    url = f"sqlite:///{database_path.as_posix()}"
    config = alembic_config(url)
    command.upgrade(config, "300a30_direct_mcp_integrity")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE data_integrity_quarantine"))
            connection.execute(
                text(
                    "CREATE TABLE data_integrity_quarantine ("
                    "id VARCHAR(64) NOT NULL PRIMARY KEY, "
                    "migration_revision VARCHAR(64) NOT NULL, "
                    "source_table VARCHAR(100) NOT NULL, "
                    "source_id VARCHAR(128) NOT NULL, "
                    "reason VARCHAR(500) NOT NULL, "
                    "payload_json TEXT NOT NULL, "
                    "quarantined_at DATETIME NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_data_integrity_quarantine_migration_revision "
                    "ON data_integrity_quarantine (migration_revision)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_data_integrity_quarantine_source_table "
                    "ON data_integrity_quarantine (source_table)"
                )
            )

        with pytest.raises(
            RuntimeError,
            match="uq_data_integrity_quarantine_source must be UNIQUE",
        ):
            command.upgrade(config, "300a31_transcript_integrity")
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version"))
            assert revision.scalar_one() == "300a30_direct_mcp_integrity"
    finally:
        engine.dispose()
