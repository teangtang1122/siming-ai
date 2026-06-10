"""Tests for global external Agent settings model and schema."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database.models import ExternalAgentGlobalSettings
from app.schemas.external_agent_settings import (
    ExternalAgentGlobalSettingsRead,
    ExternalAgentGlobalSettingsUpdate,
    EffectivePermissions,
    DEFAULT_ENABLED_PACKS,
)


class GlobalSettingsModelTest(unittest.TestCase):
    """Verify the ExternalAgentGlobalSettings model has required fields."""

    def test_has_table_name(self):
        self.assertEqual(ExternalAgentGlobalSettings.__tablename__, "external_agent_global_settings")

    def test_has_required_columns(self):
        columns = {c.name for c in ExternalAgentGlobalSettings.__table__.columns}
        required = {
            "id", "enabled_packs", "trusted_local_enabled",
            "trusted_local_clients", "require_confirmation_for_writes",
            "require_confirmation_for_destructive", "mcp_permission_source",
            "created_at", "updated_at",
        }
        missing = required - columns
        self.assertEqual(missing, set(), f"Missing columns: {missing}")

    def test_default_mcp_permission_source(self):
        col = ExternalAgentGlobalSettings.__table__.columns["mcp_permission_source"]
        self.assertEqual(col.default.arg, "global_settings")

    def test_default_trusted_local_disabled(self):
        col = ExternalAgentGlobalSettings.__table__.columns["trusted_local_enabled"]
        self.assertFalse(col.default.arg)


class GlobalSettingsSchemaTest(unittest.TestCase):
    """Verify Pydantic schemas for global settings."""

    def test_read_schema(self):
        from datetime import datetime
        data = ExternalAgentGlobalSettingsRead(
            id="g1",
            enabled_packs=["readonly_collaboration"],
            trusted_local_enabled=False,
            trusted_local_clients=[],
            require_confirmation_for_writes=True,
            require_confirmation_for_destructive=True,
            mcp_permission_source="global_settings",
            created_at=datetime(2026, 6, 9),
        )
        self.assertEqual(data.mcp_permission_source, "global_settings")

    def test_update_schema_partial(self):
        data = ExternalAgentGlobalSettingsUpdate(
            enabled_packs=["readonly_collaboration", "draft_generation"],
        )
        self.assertEqual(len(data.enabled_packs), 2)
        self.assertIsNone(data.trusted_local_enabled)

    def test_effective_permissions_schema(self):
        data = EffectivePermissions(
            global_enabled_packs=["readonly_collaboration"],
            effective_pack="readonly_collaboration",
            source="global_settings",
            cli_override=False,
        )
        self.assertEqual(data.effective_pack, "readonly_collaboration")
        self.assertFalse(data.cli_override)

    def test_defaults(self):
        self.assertEqual(DEFAULT_ENABLED_PACKS, ["readonly_collaboration"])


if __name__ == "__main__":
    unittest.main()
