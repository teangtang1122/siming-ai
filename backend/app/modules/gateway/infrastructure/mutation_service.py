"""Apply one revisioned Gateway mutation without bloating the orchestration service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import AppException
from app.modules.gateway.application.contracts import MutationResult, SyncMutation
from app.services.gateway_legacy_replication import (
    apply_domain_mutation,
    domain_snapshot_for_entity,
)

from .models import SyncChange, SyncConflict, SyncEntityState, SyncTombstone
from .support import MAX_ENTITY_PAYLOAD_BYTES, canonical_payload, payload_hash, utcnow


class GatewayMutationApplier:
    """Validate, project, and record one idempotent client mutation."""

    def __init__(
        self,
        db: Session,
        *,
        tombstone_retention_days: int,
        refresh_project_manifest: Callable[[str], None],
    ) -> None:
        self.db = db
        self.tombstone_retention_days = tombstone_retention_days
        self.refresh_project_manifest = refresh_project_manifest

    def apply(
        self,
        mutation: SyncMutation,
        *,
        device_id: str | None,
        project_domain: bool = True,
    ) -> MutationResult:
        previous = self._previous_result(mutation)
        if previous is not None:
            return previous
        if len(canonical_payload(mutation.payload)) > MAX_ENTITY_PAYLOAD_BYTES:
            return MutationResult(
                mutation_id=mutation.mutation_id,
                status="rejected",
                message="单个同步实体不能超过 1 MiB",
            )

        state = self._entity_state(mutation)
        conflict = self._record_conflict(mutation, state=state, device_id=device_id)
        if conflict is not None:
            return conflict
        projection_error = self._project_to_domain(mutation) if project_domain else None
        if projection_error is not None:
            return projection_error
        return self._record_applied(mutation, state=state, device_id=device_id)

    def _previous_result(self, mutation: SyncMutation) -> MutationResult | None:
        change = (
            self.db.query(SyncChange).filter(SyncChange.mutation_id == mutation.mutation_id).first()
        )
        if change is not None:
            return MutationResult(
                mutation_id=mutation.mutation_id,
                status="duplicate",
                revision=change.revision,
            )
        conflict = (
            self.db.query(SyncConflict)
            .filter(SyncConflict.mutation_id == mutation.mutation_id)
            .first()
        )
        if conflict is None:
            return None
        return MutationResult(
            mutation_id=mutation.mutation_id,
            status="conflict",
            revision=conflict.server_revision,
            conflict_id=conflict.id,
        )

    def _entity_state(self, mutation: SyncMutation) -> SyncEntityState | None:
        return (
            self.db.query(SyncEntityState)
            .filter(
                SyncEntityState.project_id == mutation.project_id,
                SyncEntityState.entity_type == mutation.entity_type,
                SyncEntityState.entity_id == mutation.entity_id,
            )
            .first()
        )

    def _record_conflict(
        self,
        mutation: SyncMutation,
        *,
        state: SyncEntityState | None,
        device_id: str | None,
    ) -> MutationResult | None:
        server_revision = state.revision if state is not None else 0
        if mutation.base_revision == server_revision:
            return None
        conflict = SyncConflict(
            mutation_id=mutation.mutation_id,
            project_id=mutation.project_id,
            entity_type=mutation.entity_type,
            entity_id=mutation.entity_id,
            device_id=device_id,
            client_base_revision=mutation.base_revision,
            server_revision=server_revision,
            client_operation=mutation.operation,
            server_operation="delete" if state is not None and state.is_deleted else "upsert",
            client_payload_json=mutation.payload,
            server_payload_json=(None if state is None or state.is_deleted else state.payload_json),
        )
        self.db.add(conflict)
        self.db.flush()
        snapshot = None
        if state is not None:
            snapshot = {
                "revision": state.revision,
                "operation": "delete" if state.is_deleted else "upsert",
                "payload": None if state.is_deleted else state.payload_json,
                "content_hash": state.content_hash,
            }
        return MutationResult(
            mutation_id=mutation.mutation_id,
            status="conflict",
            revision=server_revision,
            conflict_id=conflict.id,
            message="服务端版本已变化，已永久保留双方版本",
            server_snapshot=snapshot,
        )

    def _project_to_domain(self, mutation: SyncMutation) -> MutationResult | None:
        try:
            with self.db.begin_nested():
                self.db.info["siming_sync_projection"] = True
                apply_domain_mutation(
                    self.db,
                    project_id=mutation.project_id,
                    entity_type=mutation.entity_type,
                    entity_id=mutation.entity_id,
                    operation=mutation.operation,
                    payload=mutation.payload,
                )
        except (AppException, SQLAlchemyError, ValueError) as exc:
            return MutationResult(
                mutation_id=mutation.mutation_id,
                status="rejected",
                message=f"同步内容未通过数据校验：{exc}",
            )
        finally:
            self.db.info.pop("siming_sync_projection", None)
        return None

    def _record_applied(
        self,
        mutation: SyncMutation,
        *,
        state: SyncEntityState | None,
        device_id: str | None,
    ) -> MutationResult:
        now = utcnow()
        effective_payload = mutation.payload
        if mutation.operation != "delete":
            effective_payload = domain_snapshot_for_entity(
                self.db,
                project_id=mutation.project_id,
                entity_type=mutation.entity_type,
                entity_id=mutation.entity_id,
            ) or mutation.payload
        digest = payload_hash(effective_payload)
        change = SyncChange(
            mutation_id=mutation.mutation_id,
            project_id=mutation.project_id,
            entity_type=mutation.entity_type,
            entity_id=mutation.entity_id,
            operation=mutation.operation,
            base_revision=mutation.base_revision,
            payload_json=effective_payload,
            content_hash=digest,
            device_id=device_id,
            changed_at=now,
        )
        self.db.add(change)
        self.db.flush()
        self._update_state(
            mutation,
            state=state,
            revision=change.revision,
            digest=digest,
            payload=effective_payload,
            device_id=device_id,
            now=now,
        )
        self._update_tombstone(
            mutation,
            revision=change.revision,
            device_id=device_id,
            now=now,
        )
        self.refresh_project_manifest(mutation.project_id)
        return MutationResult(
            mutation_id=mutation.mutation_id,
            status="applied",
            revision=change.revision,
        )

    def _update_state(
        self,
        mutation: SyncMutation,
        *,
        state: SyncEntityState | None,
        revision: int,
        digest: str,
        payload: dict | None,
        device_id: str | None,
        now: datetime,
    ) -> None:
        values = {
            "revision": revision,
            "payload_json": payload,
            "content_hash": digest,
            "is_deleted": mutation.operation == "delete",
            "modified_by_device_id": device_id,
            "server_modified_at": now,
        }
        if state is None:
            self.db.add(
                SyncEntityState(
                    project_id=mutation.project_id,
                    entity_type=mutation.entity_type,
                    entity_id=mutation.entity_id,
                    **values,
                )
            )
            return
        for field, value in values.items():
            setattr(state, field, value)

    def _update_tombstone(
        self,
        mutation: SyncMutation,
        *,
        revision: int,
        device_id: str | None,
        now: datetime,
    ) -> None:
        tombstone = (
            self.db.query(SyncTombstone)
            .filter(
                SyncTombstone.project_id == mutation.project_id,
                SyncTombstone.entity_type == mutation.entity_type,
                SyncTombstone.entity_id == mutation.entity_id,
            )
            .first()
        )
        if mutation.operation != "delete":
            if tombstone is not None:
                self.db.delete(tombstone)
            return
        values = {
            "revision": revision,
            "deleted_by_device_id": device_id,
            "deleted_at": now,
            "expires_at": now + timedelta(days=self.tombstone_retention_days),
        }
        if tombstone is None:
            self.db.add(
                SyncTombstone(
                    project_id=mutation.project_id,
                    entity_type=mutation.entity_type,
                    entity_id=mutation.entity_id,
                    **values,
                )
            )
            return
        for field, value in values.items():
            setattr(tombstone, field, value)


__all__ = ["GatewayMutationApplier"]
