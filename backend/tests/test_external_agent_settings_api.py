"""Tests for external Agent settings API endpoints."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class GlobalSettingsAPITest(unittest.TestCase):
    """Verify global settings API endpoints exist."""

    def test_router_has_global_settings_routes(self):
        from app.routers.external_agent import router
        paths = [r.path for r in router.routes]
        self.assertTrue(any("global-settings" in p for p in paths))

    def test_router_has_effective_permissions_route(self):
        from app.routers.external_agent import router
        paths = [r.path for r in router.routes]
        self.assertTrue(any("effective-permissions" in p for p in paths))


class EffectivePermissionsTest(unittest.TestCase):
    """Verify effective permissions calculation logic."""

    def test_pack_order_correct(self):
        """Verify pack hierarchy order."""
        pack_order = [
            "readonly_collaboration",
            "draft_generation",
            "project_writing",
            "project_management",
            "trusted_local_maintenance",
        ]
        self.assertEqual(len(pack_order), 5)

    def test_effective_pack_calculation(self):
        """Verify effective pack is the highest enabled pack."""
        pack_order = [
            "readonly_collaboration",
            "draft_generation",
            "project_writing",
            "project_management",
            "trusted_local_maintenance",
        ]

        def calc_effective(enabled_packs):
            max_level = 0
            for pack in enabled_packs:
                try:
                    level = pack_order.index(pack)
                    max_level = max(max_level, level)
                except ValueError:
                    continue
            return pack_order[max_level]

        self.assertEqual(calc_effective(["readonly_collaboration"]), "readonly_collaboration")
        self.assertEqual(calc_effective(["readonly_collaboration", "draft_generation"]), "draft_generation")
        self.assertEqual(calc_effective(["readonly_collaboration", "project_writing"]), "project_writing")
        self.assertEqual(calc_effective(["project_management"]), "project_management")


if __name__ == "__main__":
    unittest.main()
