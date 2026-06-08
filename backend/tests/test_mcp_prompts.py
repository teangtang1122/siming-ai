"""Tests for MCP prompts — moshu_writing_context and related prompts."""
import json
import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.prompts import (
    list_prompts, get_prompt, render_prompt,
    render_writing_context, render_continuity_check, render_fanfic_draft,
    McpPromptMessage,
)


class ListPromptsTest(unittest.TestCase):
    """Verify prompt listing and metadata."""

    def test_list_returns_prompts(self):
        prompts = list_prompts()
        self.assertGreater(len(prompts), 0)

    def test_writing_context_exists(self):
        p = get_prompt("moshu_writing_context")
        self.assertIsNotNone(p)
        self.assertEqual(p.name, "moshu_writing_context")

    def test_continuity_check_exists(self):
        p = get_prompt("moshu_continuity_check")
        self.assertIsNotNone(p)

    def test_fanfic_draft_exists(self):
        p = get_prompt("moshu_fanfic_draft")
        self.assertIsNotNone(p)

    def test_writing_context_has_required_args(self):
        p = get_prompt("moshu_writing_context")
        arg_names = {a.name for a in p.args}
        self.assertIn("project_id", arg_names)
        self.assertIn("outline_node_id", arg_names)
        self.assertIn("requirements", arg_names)

    def test_project_id_is_required(self):
        p = get_prompt("moshu_writing_context")
        for arg in p.args:
            if arg.name == "project_id":
                self.assertTrue(arg.required)
                break
        else:
            self.fail("project_id arg not found")

    def test_unknown_prompt_returns_none(self):
        self.assertIsNone(get_prompt("nonexistent_prompt"))


class RenderWritingContextTest(unittest.TestCase):
    """Verify moshu_writing_context rendering."""

    def _mock_db(self):
        """Create a mock DB with sample data."""
        db = MagicMock()

        # Project
        project = MagicMock()
        project.id = "p1"
        project.title = "Test Novel"
        project.description = "A test story"
        project.writing_style = "natural"
        project.forbidden_sentence_patterns = "仿佛\n不由得"

        # Characters
        char = MagicMock()
        char.name = "Hero"
        char.role_type = "protagonist"
        char.current_location = "Castle"
        char.current_goal = "Save the world"

        # Worldbuilding
        wb = MagicMock()
        wb.title = "Magic System"
        wb.dimension = "power_system"
        wb.content = "Magic requires mana"

        # Chapter with summary
        chapter = MagicMock()
        chapter.title = "Chapter 1"
        summary = MagicMock()
        summary.summary_text = "Hero begins journey"
        chapter.summary = summary

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            q.order_by.return_value = q
            # Make .limit() return q itself so .all() works on q
            q.limit.return_value = q
            model_name = model.__name__ if hasattr(model, '__name__') else str(model)
            if "Project" in model_name:
                q.first.return_value = project
            elif "Character" in model_name:
                q.all.return_value = [char]
            elif "Worldbuilding" in model_name:
                q.all.return_value = [wb]
            elif "Chapter" in model_name:
                q.all.return_value = [chapter]
            elif "OutlineNode" in model_name:
                q.first.return_value = None
                q.all.return_value = []
            else:
                q.first.return_value = None
                q.all.return_value = []
            return q

        db.query.side_effect = query_side_effect
        return db

    def test_returns_messages(self):
        db = self._mock_db()
        msgs = render_writing_context(db, "p1")
        self.assertGreater(len(msgs), 0)
        self.assertIsInstance(msgs[0], McpPromptMessage)

    def test_contains_project_title(self):
        db = self._mock_db()
        msgs = render_writing_context(db, "p1")
        content = msgs[0].content
        self.assertIn("Test Novel", content)

    def test_contains_writing_style(self):
        db = self._mock_db()
        msgs = render_writing_context(db, "p1")
        content = msgs[0].content
        self.assertIn("natural", content)

    def test_contains_characters(self):
        db = self._mock_db()
        msgs = render_writing_context(db, "p1")
        content = msgs[0].content
        self.assertIn("Hero", content)

    def test_contains_worldbuilding(self):
        db = self._mock_db()
        msgs = render_writing_context(db, "p1")
        content = msgs[0].content
        self.assertIn("Magic System", content)

    def test_contains_warnings(self):
        db = self._mock_db()
        msgs = render_writing_context(db, "p1")
        content = msgs[0].content
        self.assertIn("Warning", content)

    def test_with_requirements(self):
        db = self._mock_db()
        msgs = render_writing_context(db, "p1", requirements="Write in first person")
        content = msgs[0].content
        self.assertIn("first person", content)

    def test_project_not_found(self):
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = None
        db.query.return_value = q
        msgs = render_writing_context(db, "nonexistent")
        self.assertIn("Error", msgs[0].content)


class RenderContinuityCheckTest(unittest.TestCase):
    """Verify moshu_continuity_check rendering."""

    def test_returns_messages(self):
        db = MagicMock()
        project = MagicMock()
        project.id = "p1"
        project.title = "Test"

        char = MagicMock()
        char.name = "Hero"
        char.personality = "Brave"
        char.current_goal = "Save world"

        wb = MagicMock()
        wb.title = "Rule"
        wb.content = "No time travel"

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            q.limit.return_value = q
            model_name = model.__name__ if hasattr(model, '__name__') else str(model)
            if "Project" in model_name:
                q.first.return_value = project
            elif "Character" in model_name:
                q.all.return_value = [char]
            elif "Worldbuilding" in model_name:
                q.all.return_value = [wb]
            else:
                q.first.return_value = None
                q.all.return_value = []
            return q

        db.query.side_effect = query_side_effect
        msgs = render_continuity_check(db, "p1")
        self.assertGreater(len(msgs), 0)
        content = msgs[0].content
        self.assertIn("Hero", content)
        self.assertIn("No time travel", content)


class RenderFanficDraftTest(unittest.TestCase):
    """Verify moshu_fanfic_draft rendering."""

    def test_returns_messages_with_rules(self):
        db = MagicMock()
        project = MagicMock()
        project.id = "p1"
        project.title = "Test"
        project.description = "Original work"

        char = MagicMock()
        char.name = "Hero"
        char.personality = "Brave"
        char.background = "Orphan"

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            q.limit.return_value = q
            model_name = model.__name__ if hasattr(model, '__name__') else str(model)
            if "Project" in model_name:
                q.first.return_value = project
            elif "Character" in model_name:
                q.all.return_value = [char]
            else:
                q.first.return_value = None
                q.all.return_value = []
            return q

        db.query.side_effect = query_side_effect
        msgs = render_fanfic_draft(db, "p1")
        content = msgs[0].content
        self.assertIn("anti-OOC", content)
        self.assertIn("API key", content)


class RenderPromptDispatchTest(unittest.TestCase):
    """Verify render_prompt dispatches correctly."""

    def test_unknown_returns_none(self):
        db = MagicMock()
        result = render_prompt(db, "unknown", {"project_id": "p1"})
        self.assertIsNone(result)

    def test_missing_project_id_returns_error(self):
        db = MagicMock()
        result = render_prompt(db, "moshu_writing_context", {})
        self.assertIsNotNone(result)
        self.assertIn("Error", result[0].content)


if __name__ == "__main__":
    unittest.main()
