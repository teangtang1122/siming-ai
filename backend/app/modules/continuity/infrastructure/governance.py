"""SQLAlchemy narrative governance command implementation."""

from __future__ import annotations

from ....architecture.uow import SqlAlchemyUnitOfWork
from .models import CausalEdge, Foreshadowing, NarrativeDebt


class SqlAlchemyNarrativeGovernanceCommands:
    def update_status(
        self,
        session,
        project_id: str,
        item_type: str,
        item_id: str,
        values: dict,
    ) -> bool:
        model = {
            "foreshadowings": Foreshadowing,
            "causal-edges": CausalEdge,
            "narrative-debts": NarrativeDebt,
        }.get(item_type)
        if not model:
            raise ValueError("不支持的治理对象类型")
        row = (
            session.query(model)
            .filter(model.project_id == project_id, model.id == item_id)
            .first()
        )
        if not row:
            return False
        with SqlAlchemyUnitOfWork.from_session(session) as uow:
            for key, value in values.items():
                if hasattr(row, key):
                    setattr(row, key, value)
            uow.commit()
        return True

__all__ = ["SqlAlchemyNarrativeGovernanceCommands"]
