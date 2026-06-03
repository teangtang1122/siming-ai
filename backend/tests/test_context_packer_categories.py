"""Tests for pack_context include_categories and RAG miss warnings."""
import unittest
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base, Project, WorldbuildingEntry, OutlineNode, Character,
)
from app.services.rag.context_packer import (
    pack_context, ContextBudget, ContextSection, PackedContext,
)


class IncludeCategoriesTestCase(unittest.TestCase):
    """Test include_categories parameter behavior."""

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
        self.project = Project(id="p1", title="Test Project")
        self.db.add(self.project)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _add_worldbuilding(self, count=5):
        for i in range(count):
            self.db.add(WorldbuildingEntry(
                id=f"wb-{i}", project_id="p1",
                title=f"设定{i}", content=f"内容{i}",
                dimension="geography", sort_order=i,
            ))
        self.db.commit()

    def _add_character(self, name="角色A", char_id="c1"):
        self.db.add(Character(
            id=char_id, project_id="p1",
            name=name, role_type="主角",
            personality="勇敢",
        ))
        self.db.commit()

    @patch("app.services.rag.context_packer.detect_fts5_available", return_value=False)
    def test_none_includes_all_sections(self, _mock_fts):
        """include_categories=None should build all sections."""
        self._add_worldbuilding(3)
        self._add_character()
        packed = pack_context(self.db, "p1", include_categories=None)
        categories = {s.category for s in packed.sections}
        self.assertIn("worldbuilding", categories)
        self.assertIn("characters", categories)
        self.assertIn("summary", categories)

    @patch("app.services.rag.context_packer.detect_fts5_available", return_value=False)
    def test_exclude_characters(self, _mock_fts):
        """Excluding characters should not generate characters section."""
        self._add_worldbuilding(3)
        self._add_character()
        packed = pack_context(
            self.db, "p1",
            include_categories={"outline", "summary", "worldbuilding"},
        )
        categories = {s.category for s in packed.sections}
        self.assertNotIn("characters", categories)
        self.assertIn("worldbuilding", categories)
        self.assertIn("summary", categories)

    @patch("app.services.rag.context_packer.detect_fts5_available", return_value=False)
    def test_exclude_worldbuilding(self, _mock_fts):
        """Excluding worldbuilding should not generate worldbuilding section."""
        self._add_worldbuilding(3)
        packed = pack_context(
            self.db, "p1",
            include_categories={"outline", "summary", "characters"},
        )
        categories = {s.category for s in packed.sections}
        self.assertNotIn("worldbuilding", categories)

    @patch("app.services.rag.context_packer.detect_fts5_available", return_value=False)
    def test_only_summary(self, _mock_fts):
        """Can include only a single category."""
        self._add_worldbuilding(3)
        packed = pack_context(self.db, "p1", include_categories={"summary"})
        categories = {s.category for s in packed.sections}
        self.assertEqual(categories, {"summary"})


class SelectionReasonTestCase(unittest.TestCase):
    """Test that selection_reason strings are clear and distinguishable."""

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
        self.project = Project(id="p1", title="Test Project")
        self.db.add(self.project)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    @patch("app.services.rag.context_packer.detect_fts5_available", return_value=False)
    def test_traditional_reason_for_small_project(self, _mock_fts):
        """Projects with <= 50 entries should show traditional selection reason."""
        for i in range(5):
            self.db.add(WorldbuildingEntry(
                id=f"wb-{i}", project_id="p1",
                title=f"设定{i}", content=f"内容{i}",
                dimension="geography", sort_order=i,
            ))
        self.db.commit()
        packed = pack_context(self.db, "p1", requirements="测试")
        wb_section = next(s for s in packed.sections if s.category == "worldbuilding")
        self.assertIn("传统关键词筛选", wb_section.selection_reason)

    @patch("app.services.rag.context_packer.detect_fts5_available", return_value=False)
    def test_no_character_warning_when_excluded(self, _mock_fts):
        """Should not warn about missing characters when characters category is excluded."""
        packed = pack_context(
            self.db, "p1",
            include_categories={"outline", "summary", "worldbuilding"},
        )
        char_warnings = [w for w in packed.warnings if "角色" in w]
        self.assertEqual(len(char_warnings), 0)


class RAGMissWarningTestCase(unittest.TestCase):
    """Test RAG miss warning logic."""

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
        self.project = Project(id="p1", title="Test Project")
        self.db.add(self.project)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    @patch("app.services.rag.context_packer.detect_fts5_available", return_value=False)
    @patch("app.services.rag.context_packer.search_chunks", return_value=[])
    def test_rag_miss_warning_for_large_project(self, _mock_search, _mock_fts):
        """Worldbuilding > 50 entries with RAG miss should warn."""
        for i in range(60):
            self.db.add(WorldbuildingEntry(
                id=f"wb-{i}", project_id="p1",
                title=f"设定{i}", content=f"这是第{i}条世界观设定内容",
                dimension="geography", sort_order=i,
            ))
        self.db.commit()
        packed = pack_context(self.db, "p1", requirements="测试查询")
        rag_warnings = [w for w in packed.warnings if "RAG" in w]
        self.assertTrue(len(rag_warnings) > 0, f"Expected RAG warning, got: {packed.warnings}")

    @patch("app.services.rag.context_packer.detect_fts5_available", return_value=False)
    def test_no_rag_warning_for_small_project(self, _mock_fts):
        """Worldbuilding <= 50 entries should not trigger RAG miss warning."""
        for i in range(10):
            self.db.add(WorldbuildingEntry(
                id=f"wb-{i}", project_id="p1",
                title=f"设定{i}", content=f"内容{i}",
                dimension="geography", sort_order=i,
            ))
        self.db.commit()
        packed = pack_context(self.db, "p1", requirements="测试")
        rag_warnings = [w for w in packed.warnings if "RAG" in w and "世界观" in w]
        self.assertEqual(len(rag_warnings), 0)

    @patch("app.services.rag.context_packer.detect_fts5_available", return_value=False)
    def test_no_rag_warning_when_worldbuilding_excluded(self, _mock_fts):
        """Should not warn about RAG miss when worldbuilding is excluded."""
        for i in range(60):
            self.db.add(WorldbuildingEntry(
                id=f"wb-{i}", project_id="p1",
                title=f"设定{i}", content=f"内容{i}",
                dimension="geography", sort_order=i,
            ))
        self.db.commit()
        packed = pack_context(
            self.db, "p1",
            requirements="测试",
            include_categories={"outline", "summary"},
        )
        rag_warnings = [w for w in packed.warnings if "RAG" in w and "世界观" in w]
        self.assertEqual(len(rag_warnings), 0)


if __name__ == "__main__":
    unittest.main()
