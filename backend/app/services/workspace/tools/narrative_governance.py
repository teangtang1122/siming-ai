from __future__ import annotations

from app.architecture.uow import commit_session

from typing import Any

from sqlalchemy.orm import Session

from ....database.models import NarrativeCheckpoint
from ....services.narrative_governance import (
    apply_governance_candidates,
    checkpoint_diff,
    governance_context,
    governance_dashboard,
    restore_narrative_checkpoint,
)


async def get_narrative_governance(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    view = str(args.get("view") or "all")
    if view not in {"all", "chapter", "due", "risk"}:
        view = "all"
    data = governance_dashboard(db, project_id, chapter_id=str(args.get("chapter_id") or ""), view=view)
    data["context_text"] = governance_context(db, project_id, chapter_id=str(args.get("chapter_id") or "") or None)
    return {"tool": "get_narrative_governance", "status": "ok", "detail": "叙事治理状态已读取", "data": data}


async def apply_narrative_governance_candidates(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    candidates = args.get("candidates") if isinstance(args.get("candidates"), list) else []
    mode = str(args.get("mode") or "preview")
    if mode != "apply":
        return {"tool": "apply_narrative_governance_candidates", "status": "ok", "detail": f"已预览 {len(candidates)} 条治理候选", "data": {"items": candidates, "applied": False}}
    items = apply_governance_candidates(db, project_id, candidates, chapter_id=str(args.get("chapter_id") or "") or None)
    commit_session(db)
    return {"tool": "apply_narrative_governance_candidates", "status": "ok", "detail": f"已应用 {len(items)} 条治理候选", "data": {"items": items, "applied": True}}


async def diff_narrative_checkpoint(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    checkpoint_id = str(args.get("checkpoint_id") or "")
    if not checkpoint_id:
        return {"tool": "diff_narrative_checkpoint", "status": "error", "detail": "checkpoint_id is required", "data": None}
    return {"tool": "diff_narrative_checkpoint", "status": "ok", "detail": "检查点差异已生成", "data": checkpoint_diff(db, project_id, checkpoint_id)}


async def restore_narrative_governance_checkpoint(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    checkpoint_id = str(args.get("checkpoint_id") or "")
    row = restore_narrative_checkpoint(db, project_id, checkpoint_id)
    commit_session(db)
    return {"tool": "restore_narrative_governance_checkpoint", "status": "ok", "detail": "叙事治理状态已回滚", "data": {"id": row.id, "sequence": row.sequence, "label": row.label}}


async def list_narrative_checkpoints(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    limit = max(1, min(int(args.get("limit") or 30), 100))
    rows = db.query(NarrativeCheckpoint).filter(NarrativeCheckpoint.project_id == project_id).order_by(NarrativeCheckpoint.sequence.desc()).limit(limit).all()
    return {"tool": "list_narrative_checkpoints", "status": "ok", "detail": f"共 {len(rows)} 个叙事检查点", "data": {"items": [{"id": row.id, "sequence": row.sequence, "label": row.label, "chapter_id": row.chapter_id, "chapter_snapshot_id": row.chapter_snapshot_id, "trigger_type": row.trigger_type, "created_at": row.created_at.isoformat()} for row in rows], "total": len(rows)}}
