"""Durable idempotency claims for novel-creation stage commands."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from json import dumps
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.architecture.uow import commit_session
from app.database.models import NovelCreationRunClaim


def get_creation_claim_by_idempotency_key(
    db: Session,
    *,
    session_id: str,
    idempotency_key: str,
) -> NovelCreationRunClaim | None:
    """Return a durable creation claim without leaking ORM access into HTTP routers."""
    return db.query(NovelCreationRunClaim).filter(
        NovelCreationRunClaim.session_id == session_id,
        NovelCreationRunClaim.idempotency_key == idempotency_key,
    ).first()


def creation_idempotency_key(
    *,
    session_id: str,
    stage: str,
    operation: str,
    request: dict,
    input_revision: int,
    input_snapshot_hash: str,
    explicit_key: str | None = None,
) -> str:
    """Return a stable request identity, preferring the client-supplied key."""
    if isinstance(explicit_key, str) and explicit_key.strip():
        return explicit_key.strip()[:128]
    payload = {
        "session_id": session_id,
        "stage": stage,
        "operation": operation,
        "instruction": request.get("instruction"),
        "model": request.get("model"),
        "entity_id": request.get("entity_id"),
        "entity_type": request.get("entity_type"),
        "entity_count": request.get("entity_count"),
        "context_entity_ids": request.get("context_entity_ids") or [],
        "context_artifacts": request.get("context_artifacts") or [],
        "use_model": request.get("use_model"),
        "auto_confirm": request.get("auto_confirm"),
        "input_revision": input_revision,
        "input_snapshot_hash": input_snapshot_hash,
    }
    return sha256(dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def claim_or_replay_creation_run(
    db: Session,
    *,
    session_id: str,
    artifact_key: str,
    idempotency_key: str,
    input_revision: int,
    input_snapshot_hash: str,
) -> tuple[NovelCreationRunClaim, bool]:
    """Acquire one active stage producer or return its durable replay claim."""
    claim = db.query(NovelCreationRunClaim).filter(
        NovelCreationRunClaim.session_id == session_id,
        NovelCreationRunClaim.idempotency_key == idempotency_key,
    ).first()
    if claim:
        return claim, True

    active = db.query(NovelCreationRunClaim).filter(
        NovelCreationRunClaim.session_id == session_id,
        NovelCreationRunClaim.artifact_key == artifact_key,
        NovelCreationRunClaim.status == "running",
    ).first()
    if active:
        return active, True

    claim = NovelCreationRunClaim(
        session_id=session_id,
        artifact_key=artifact_key,
        idempotency_key=idempotency_key,
        claim_token=str(uuid4()),
        status="running",
        input_revision=input_revision,
        input_snapshot_hash=input_snapshot_hash,
    )
    db.add(claim)
    try:
        commit_session(db)
    except IntegrityError:
        db.rollback()
        replay = db.query(NovelCreationRunClaim).filter(
            NovelCreationRunClaim.session_id == session_id,
            NovelCreationRunClaim.idempotency_key == idempotency_key,
        ).first()
        if replay:
            return replay, True
        active = db.query(NovelCreationRunClaim).filter(
            NovelCreationRunClaim.session_id == session_id,
            NovelCreationRunClaim.artifact_key == artifact_key,
            NovelCreationRunClaim.status == "running",
        ).first()
        if active:
            return active, True
        raise
    return claim, False


def complete_creation_claim(
    db: Session,
    claim_id: str | None,
    *,
    result: dict | None = None,
    error: str | None = None,
    status: str | None = None,
) -> None:
    if not claim_id:
        return
    claim = db.get(NovelCreationRunClaim, claim_id)
    if not claim:
        return
    claim.status = status or ("completed" if error is None else "failed")
    claim.result_json = result
    claim.error = error
    claim.completed_at = datetime.utcnow()
