"""Tests for cataloging plan in agent planner."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.agent.planner import detect_intent, build_plan_from_intent


class CatalogingIntentTest(unittest.TestCase):
    """Verify cataloging intent detection and plan building."""

    def test_detects_cataloging_intent(self):
        intent = detect_intent("帮我建档")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "project_init")

    def test_cataloging_plan_uses_real_tools(self):
        """Cataloging plan should use list_chapters and start_cataloging_job."""
        from app.services.agent.planner import plan_cataloging_init
        plan = plan_cataloging_init(chapter_ids=[])
        step_names = set(plan.steps.keys())
        self.assertIn("list_chapters", step_names)
        self.assertIn("start_cataloging_job", step_names)
        # Should NOT have pseudo-tools
        self.assertNotIn("extract_facts", step_names)
        self.assertNotIn("resolve_targets", step_names)
        self.assertNotIn("apply_candidates", step_names)

    def test_cataloging_plan_has_correct_dependencies(self):
        from app.services.agent.planner import plan_cataloging_init
        plan = plan_cataloging_init(chapter_ids=[])
        self.assertEqual(plan.steps["start_cataloging_job"].depends_on, ["list_chapters"])

    def test_cataloging_plan_passes_execution_mode(self):
        from app.services.agent.planner import plan_cataloging_init
        plan = plan_cataloging_init(execution_mode="manual")
        self.assertEqual(
            plan.steps["start_cataloging_job"].args["execution_mode"],
            "manual",
        )

    def test_cataloging_plan_defaults_to_auto(self):
        from app.services.agent.planner import plan_cataloging_init
        plan = plan_cataloging_init()
        self.assertEqual(
            plan.steps["start_cataloging_job"].args["execution_mode"],
            "auto",
        )


class NoDuplicatePlanTest(unittest.TestCase):
    """Verify there is only one plan_cataloging_init definition."""

    def test_no_duplicate_function(self):
        """plan_cataloging_init should be defined once."""
        import app.services.agent.planner as mod
        # Count occurrences of the function name in the source
        import inspect
        source = inspect.getsource(mod)
        count = source.count("def plan_cataloging_init(")
        self.assertEqual(count, 1, f"Found {count} definitions of plan_cataloging_init")


if __name__ == "__main__":
    unittest.main()
