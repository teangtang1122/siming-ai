"""SQLAlchemy public prompt-pack catalog."""
from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ....core.db_helpers import get_project_or_404
from ....services.prompt_packs.seed import ensure_builtin_packs
from .models import PublicPromptPack


class SqlAlchemyPromptPackCatalog:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_project(self, project_id: str) -> dict:
        get_project_or_404(self._session, project_id)
        ensure_builtin_packs(self._session)
        packs = (
            self._session.query(PublicPromptPack)
            .filter(
                PublicPromptPack.enabled.is_(True),
                or_(
                    PublicPromptPack.project_id.is_(None),
                    PublicPromptPack.project_id == project_id,
                ),
            )
            .order_by(PublicPromptPack.scope, PublicPromptPack.pack_id)
            .all()
        )
        items = [
            {
                "id": pack.id,
                "project_id": pack.project_id,
                "pack_id": pack.pack_id,
                "version": pack.version,
                "scope": pack.scope,
                "title": pack.title,
                "summary": pack.summary,
                "is_builtin": pack.is_builtin,
                "enabled": pack.enabled,
                "updated_at": pack.updated_at.isoformat() if pack.updated_at else None,
            }
            for pack in packs
        ]
        return {"items": items, "total": len(items)}


__all__ = ["SqlAlchemyPromptPackCatalog"]
