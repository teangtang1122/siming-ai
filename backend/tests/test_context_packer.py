"""Tests for RAG context packer: budget enforcement, pinned items, explanations."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base, Project, Chapter, ChapterSummary, Character, OutlineNode,
    OutlineNodeCharacter, WorldbuildingEntry, RagChunk, RagDocument,
)
from app.services.rag.context_packer import (
    ContextBudget,
    ContextSection,
    PackedContext,
    pack_context,
    _pack_outline,
    _pack_summaries,
)


class ContextBudgetTestCase(unittest.TestCase):
    def test_default_budget(self):
        budget = ContextBudget()
        self.assertGreater(budget.total_chars, 0)
        self.assertEqual(budget.max_system_chars, 0)

    def test_can_fit_within_budget(self):
        budget = ContextBudget()
        total = (
            budget.max_chapter_chars + budget.max_summary_chars
            + budget.max_character_chars + budget.max_worldbuilding_chars
            + budget.max_memory_chars + budget.max_outline_chars
        )
        self.assertTrue(budget.can_fit(0, 1000))
        self.assertTrue(budget.can_fit(total - 100, 50))
        self.assertFalse(budget.can_fit(total, 1))

    def test_to_dict(self):
        budget = ContextBudget()
        d = budget.to_dict()
        self.assertIn("max_chapter_chars", d)
        self.assertIn("max_worldbuilding_chars", d)
        self.assertIn("reserve_chars", d)

    def test_custom_budget(self):
        budget = ContextBudget(max_worldbuilding_chars=16000)
        self.assertEqual(budget.max_worldbuilding_chars, 16000)


class PackContextTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        try:
            with self.engine.begin() as conn:
                conn.execute(text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5("
                    "chunk_id UNINDEXED, project_id UNINDEXED, source_type UNINDEXED, "
                    "title, content, metadata_json, tokenize='unicode61'"
                    ")"
                ))
        except Exception:
            pass
        Session = sessionmaker(bind=self.engine)
        self.db = Session()
        self.project = Project(id="p1", title="测试项目", description="一个测试项目")
        self.db.add(self.project)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_pack_empty_project(self):
        result = pack_context(self.db, "p1", requirements="测试需求")
        self.assertIsInstance(result, PackedContext)
        self.assertIsInstance(result.sections, list)
        self.assertIsInstance(result.total_used_chars, int)
        self.assertIsInstance(result.budget, dict)
        self.assertIsInstance(result.used_chars, dict)
        self.assertIsInstance(result.explanations, list)
        self.assertIsInstance(result.warnings, list)
        self.assertIsInstance(result.fts_available, bool)

    def test_pack_with_outline_node(self):
        node = OutlineNode(id="o1", project_id="p1", title="第一章", node_type="chapter", summary="故事开始。")
        self.db.add(node)
        self.db.commit()

        result = pack_context(self.db, "p1", outline_node_id="o1")
        outline_sections = [s for s in result.sections if s.category == "outline"]
        self.assertGreater(len(outline_sections), 0)

    def test_pack_budget_enforcement(self):
        # Add many worldbuilding entries
        for i in range(20):
            self.db.add(WorldbuildingEntry(
                id=f"w{i}", project_id="p1", dimension="geography",
                title=f"设定{i}", content=f"这是第{i}条很长的设定内容。" * 50,
            ))
        self.db.commit()

        budget = ContextBudget(max_worldbuilding_chars=500)
        result = pack_context(self.db, "p1", budget=budget)
        wb_used = result.used_chars.get("worldbuilding", 0)
        self.assertLessEqual(wb_used, 600)  # Allow small overflow for a single chunk

    def test_pack_with_requirements(self):
        self.db.add(WorldbuildingEntry(
            id="w1", project_id="p1", dimension="geography",
            title="北方山脉", content="连绵千里的山脉，终年积雪。传说中有龙族栖息。",
        ))
        self.db.add(WorldbuildingEntry(
            id="w2", project_id="p1", dimension="culture",
            title="南方文化", content="南方人民热爱歌舞。",
        ))
        self.db.commit()

        result = pack_context(self.db, "p1", requirements="龙族山脉传说")
        self.assertIsInstance(result, PackedContext)

    def test_pack_warnings_no_outline(self):
        result = pack_context(self.db, "p1")
        outline_warnings = [w for w in result.warnings if "大纲节点" in w]
        self.assertGreater(len(outline_warnings), 0)

    def test_pack_pinned_chunks(self):
        chunk = RagChunk(
            id="pin1", document_id="doc1", project_id="p1",
            source_type="worldbuilding", source_id="w1",
            chunk_index=0, title="固定设定", content="这条设定必须出现。",
        )
        self.db.add(chunk)
        self.db.commit()

        result = pack_context(self.db, "p1", pinned_chunk_ids=["pin1"])
        pinned_sections = [s for s in result.sections if s.category == "pinned"]
        self.assertGreater(len(pinned_sections), 0)
        self.assertEqual(pinned_sections[0].chunk_ids, ["pin1"])

    def test_pack_explanations_present(self):
        node = OutlineNode(id="o1", project_id="p1", title="第一章", node_type="chapter", summary="开始。")
        self.db.add(node)
        self.db.commit()

        result = pack_context(self.db, "p1", outline_node_id="o1")
        self.assertGreater(len(result.explanations), 0)


class PackOutlineTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        Session = sessionmaker(bind=self.engine)
        self.db = Session()
        self.project = Project(id="p1", title="Test")
        self.db.add(self.project)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_pack_outline_no_node(self):
        section, ctx = _pack_outline(self.db, "p1", None, ContextBudget())
        self.assertIsNone(section)

    def test_pack_outline_with_node(self):
        node = OutlineNode(id="o1", project_id="p1", title="第一章", node_type="chapter", summary="故事开始。")
        self.db.add(node)
        self.db.commit()

        section, ctx = _pack_outline(self.db, "p1", "o1", ContextBudget())
        self.assertIsNotNone(section)
        self.assertEqual(section.category, "outline")
        self.assertIn("第一章", section.content)


class PackSummariesTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        Session = sessionmaker(bind=self.engine)
        self.db = Session()
        self.project = Project(id="p1", title="Test")
        self.db.add(self.project)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_pack_summaries_empty(self):
        section = _pack_summaries(self.db, "p1", ContextBudget())
        self.assertEqual(section.category, "summary")
        self.assertEqual(section.used_chars, 0)


if __name__ == "__main__":
    unittest.main()
