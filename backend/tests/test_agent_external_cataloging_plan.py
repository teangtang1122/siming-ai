"""Tests for external cataloging intent in plan agent."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.agent.planner import detect_intent, build_plan_from_intent


class DetectExternalCatalogingIntentTest(unittest.TestCase):
    """Verify external_cataloging intent detection."""

    def test_detects_external_cataloging(self):
        intent = detect_intent("外部agent建档")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "external_cataloging")

    def test_detects_no_api_cataloging(self):
        intent = detect_intent("不用墨枢api建档")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "external_cataloging")

    def test_detects_claude_cataloging(self):
        intent = detect_intent("用claude建档")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "external_cataloging")

    def test_detects_no_siming_api_cataloging(self):
        intent = detect_intent("不用司命 API 建档")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "external_cataloging")

    def test_detects_external_cataloging_cn(self):
        intent = detect_intent("外部编目")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "external_cataloging")

    def test_detects_api_billing_keyword(self):
        """API欠费 should route to external cataloging."""
        intent = detect_intent("API欠费了，帮我建档")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "external_cataloging")

    def test_detects_api_billing_keyword_lower(self):
        """api欠费 should also match."""
        intent = detect_intent("api欠费了")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "external_cataloging")

    def test_detects_no_api_with_spaces(self):
        """'不用墨枢 API' with spaces should match."""
        intent = detect_intent("不用墨枢 API 建档")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "external_cataloging")

    def test_detects_no_api_standalone(self):
        """'不用墨枢 API' without 建档 should still route to external."""
        intent = detect_intent("不用墨枢 API")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "external_cataloging")

    def test_detects_use_claude_with_spaces(self):
        """'用 Claude 建档' with spaces should match."""
        intent = detect_intent("用 Claude 建档")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "external_cataloging")

    def test_normal_cataloging_not_routed_to_external(self):
        """Normal '建档' should route to project_init, not external_cataloging."""
        intent = detect_intent("帮我建档")
        if intent:
            self.assertNotEqual(intent["intent_type"], "external_cataloging")


class BuildExternalCatalogingPlanTest(unittest.TestCase):
    """Verify external_cataloging plan building."""

    def test_builds_plan(self):
        intent = {"intent_type": "external_cataloging", "requirements": "编目所有章节"}
        plan = build_plan_from_intent(intent)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.name, "external_cataloging")

    def test_plan_has_required_steps(self):
        intent = {"intent_type": "external_cataloging", "requirements": ""}
        plan = build_plan_from_intent(intent)
        step_names = set(plan.steps.keys())
        self.assertIn("start_job", step_names)
        self.assertIn("verify_progress", step_names)

    def test_plan_steps_have_dependencies(self):
        intent = {"intent_type": "external_cataloging", "requirements": ""}
        plan = build_plan_from_intent(intent)
        self.assertEqual(plan.steps["verify_progress"].depends_on, ["start_job"])


if __name__ == "__main__":
    unittest.main()
