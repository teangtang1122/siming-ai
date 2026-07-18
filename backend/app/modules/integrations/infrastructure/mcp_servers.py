"""SQLAlchemy MCP server configuration repository and use-case adapter."""

from __future__ import annotations

from ....architecture.uow import SqlAlchemyUnitOfWork
from .models import McpServerConfig


def _payload(config: McpServerConfig) -> dict:
    return {
        "id": config.id,
        "project_id": config.project_id,
        "name": config.name,
        "transport": config.transport,
        "command": config.command,
        "url": config.url,
        "enabled": config.enabled,
        "status": config.status,
        "last_error": config.last_error,
        "created_at": config.created_at,
        "updated_at": config.updated_at,
    }


def _find(session, project_id: str, config_id: str) -> McpServerConfig | None:
    return (
        session.query(McpServerConfig)
        .filter(McpServerConfig.project_id == project_id, McpServerConfig.id == config_id)
        .first()
    )


class SqlAlchemyMcpServerConfiguration:
    def list(self, session, project_id: str) -> list[dict]:
        rows = (
            session.query(McpServerConfig)
            .filter(McpServerConfig.project_id == project_id)
            .order_by(McpServerConfig.created_at.desc())
            .all()
        )
        return [_payload(row) for row in rows]

    def create(self, session, project_id: str, values: dict) -> dict:
        with SqlAlchemyUnitOfWork.from_session(session) as uow:
            row = McpServerConfig(project_id=project_id, **values)
            session.add(row)
            uow.commit()
            session.refresh(row)
        return _payload(row)

    def get(self, session, project_id: str, config_id: str) -> dict | None:
        row = _find(session, project_id, config_id)
        return _payload(row) if row else None

    def update(
        self,
        session,
        project_id: str,
        config_id: str,
        values: dict,
    ) -> dict | None:
        row = _find(session, project_id, config_id)
        if not row:
            return None
        with SqlAlchemyUnitOfWork.from_session(session) as uow:
            for field, value in values.items():
                setattr(row, field, value)
            uow.commit()
            session.refresh(row)
        return _payload(row)

    def delete(self, session, project_id: str, config_id: str) -> bool:
        row = _find(session, project_id, config_id)
        if not row:
            return False
        with SqlAlchemyUnitOfWork.from_session(session) as uow:
            session.delete(row)
            uow.commit()
        return True


__all__ = ["SqlAlchemyMcpServerConfiguration"]
