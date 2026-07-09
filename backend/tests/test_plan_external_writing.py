"""Tests for external writing intent in plan agent."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.agent.planner import detect_intent, build_plan_from_intent


class DetectExternalWritingIntentTest(unittest.TestCase):
    """Verify external_writing intent detection."""

    def test_detects_external_writing(self):
        intent = detect_intent("让外部agent写这一章")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "external_writing")

    def test_detects_external_model(self):
        intent = detect_intent("外部模型写")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "external_writing")

    def test_detects_no_api(self):
        intent = detect_intent("不用内部api写")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "external_writing")


class BuildExternalWritingPlanTest(unittest.TestCase):
    """Verify external_writing plan building."""

    def test_builds_plan(self):
        intent = {"intent_type": "external_writing", "requirements": "写第三章"}
        plan = build_plan_from_intent(intent)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.name, "external_writing")

    def test_plan_has_required_steps(self):
        intent = {"intent_type": "external_writing", "requirements": "写第三章"}
        plan = build_plan_from_intent(intent)
        step_names = set(plan.steps.keys())
        self.assertIn("prepare_context", step_names)
        self.assertIn("save_draft", step_names)
        self.assertIn("record_review", step_names)
        self.assertIn("create_chapter", step_names)
        self.assertIn("archive_updates", step_names)

    def test_plan_steps_have_dependencies(self):
        intent = {"intent_type": "external_writing", "requirements": "写第三章"}
        plan = build_plan_from_intent(intent)
        self.assertEqual(plan.steps["save_draft"].depends_on, ["prepare_context"])
        self.assertEqual(plan.steps["create_chapter"].depends_on, ["record_review"])
        self.assertEqual(plan.steps["archive_updates"].depends_on, ["create_chapter"])


if __name__ == "__main__":
    unittest.main()
