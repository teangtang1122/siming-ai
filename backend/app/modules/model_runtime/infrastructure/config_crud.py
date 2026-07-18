"""SQLAlchemy model configuration CRUD implementation."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .legacy_models import LocalModel
from .models import APIConfig


class SqlAlchemyModelConfigCrud:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_configs(self):
        return self.session.query(APIConfig).order_by(APIConfig.created_at.desc()).all()

    def get_provider(self, provider: str):
        return self.session.query(APIConfig).filter(APIConfig.provider == provider).first()

    def create(self, **values: Any):
        config = APIConfig(**values)
        self.session.add(config)
        return config

    def delete(self, config: Any) -> None:
        self.session.delete(config)

    def get_global(self):
        return self.session.query(APIConfig).filter(APIConfig.is_global_default == True).first()  # noqa: E712

    def get_ready_global(self):
        return self.session.query(APIConfig).filter(
            APIConfig.is_global_default == True,  # noqa: E712
            APIConfig.readiness_status == "ready",
        ).first()

    def clear_global(self) -> None:
        self.session.query(APIConfig).update({"is_global_default": False})

    def list_local_models(self):
        return self.session.query(LocalModel).order_by(LocalModel.recommended_vram_gb.asc()).all()


__all__ = ["SqlAlchemyModelConfigCrud"]
