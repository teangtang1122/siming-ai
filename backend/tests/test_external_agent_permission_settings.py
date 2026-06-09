"""Tests for external Agent permission settings model and API."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database.models import ExternalAgentSettings
from app.schemas.external_agent_settings import (
    ExternalAgentSettingsRead,
    ExternalAgentSettingsUpdate,
    DEFAULT_ENABLED_PACKS,
    DEFAULT_TRUSTED_LOCAL_ENABLED,
    DEFAULT_TRUSTED_LOCAL_CLIENTS,
    DEFAULT_REQUIRE_CONFIRMATION_FOR_WRITES,
    DEFAULT_REQUIRE_CONFIRMATION_FOR_DESTRUCTIVE,
)


class ExternalAgentSettingsModelTest(unittest.TestCase):
    """Verify the ExternalAgentSettings model has required fields."""

    def test_has_table_name(self):
        self.assertEqual(ExternalAgentSettings.__tablename__, "external_agent_settings")

    def test_has_required_columns(self):
        columns = {c.name for c in ExternalAgentSettings.__table__.columns}
        required = {
            "id", "project_id", "enabled_packs", "trusted_local_enabled",
            "trusted_local_clients", "require_confirmation_for_writes",
            "require_confirmation_for_destructive", "created_at", "updated_at",
        }
        missing = required - columns
        self.assertEqual(missing, set(), f"Missing columns: {missing}")

    def test_default_trusted_local_disabled(self):
        col = ExternalAgentSettings.__table__.columns["trusted_local_enabled"]
        self.assertFalse(col.default.arg)


class ExternalAgentSettingsSchemaTest(unittest.TestCase):
    """Verify Pydantic schemas for external Agent settings."""

    def test_read_schema(self):
        from datetime import datetime
        data = ExternalAgentSettingsRead(
            id="s1", project_id="p1",
            enabled_packs=["readonly_collaboration"],
            trusted_local_enabled=False,
            trusted_local_clients=[],
            require_confirmation_for_writes=True,
            require_confirmation_for_destructive=True,
            created_at=datetime(2026, 6, 9),
        )
        self.assertEqual(data.project_id, "p1")
        self.assertFalse(data.trusted_local_enabled)

    def test_update_schema_partial(self):
        data = ExternalAgentSettingsUpdate(enabled_packs=["readonly_collaboration", "draft_generation"])
        self.assertEqual(len(data.enabled_packs), 2)
        self.assertIsNone(data.trusted_local_enabled)

    def test_defaults(self):
        self.assertEqual(DEFAULT_ENABLED_PACKS, ["readonly_collaboration"])
        self.assertFalse(DEFAULT_TRUSTED_LOCAL_ENABLED)
        self.assertEqual(DEFAULT_TRUSTED_LOCAL_CLIENTS, [])
        self.assertTrue(DEFAULT_REQUIRE_CONFIRMATION_FOR_WRITES)
        self.assertTrue(DEFAULT_REQUIRE_CONFIRMATION_FOR_DESTRUCTIVE)


class ExternalAgentSettingsRouterTest(unittest.TestCase):
    """Verify router has settings endpoints."""

    def test_router_has_settings_route(self):
        from app.routers.external_agent import router
        paths = [r.path for r in router.routes]
        self.assertTrue(any("settings" in p for p in paths))


if __name__ == "__main__":
    unittest.main()
