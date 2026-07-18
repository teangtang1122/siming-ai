"""Application-boundary tests for External Agent settings."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.session import Base
from app.modules.integrations.infrastructure.external_agent_settings import (
    SqlAlchemyExternalAgentSettingsStore,
)
from app.modules.story.infrastructure.entities import Project


def test_settings_store_resolves_project_override_without_router_queries():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    project = Project(title="Settings boundary")
    db.add(project)
    db.flush()
    store = SqlAlchemyExternalAgentSettingsStore(db)

    defaults = store.get_global()
    assert defaults["mcp_permission_source"] == "global_settings"
    assert "trusted_local_maintenance" in defaults["enabled_packs"]

    store.update_global({"enabled_packs": ["readonly_collaboration"]})
    store.update_project(project.id, {"enabled_packs": ["project_writing"]})
    effective = store.effective_permissions(project.id)

    assert effective["source"] == "project_override"
    assert effective["effective_pack"] == "project_writing"
    assert effective["project_enabled_packs"] == ["project_writing"]
