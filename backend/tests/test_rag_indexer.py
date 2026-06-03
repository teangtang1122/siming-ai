"""Tests for RAG indexer: chunking, hashing, FTS5 sync, and dirty detection."""
import unittest
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, Project, Chapter, WorldbuildingEntry, Character, RagDocument, RagChunk
from app.services.rag.indexer import (
    _chunk_text,
    _content_hash,
    _split_long_paragraph,
    detect_fts5_available,
    index_document,
    reindex_project,
    reindex_project_types,
    mark_dirty,
    ensure_indexed,
    project_has_chunks,
)


class ChunkTextTestCase(unittest.TestCase):
    def test_empty_text_returns_empty(self):
        self.assertEqual(_chunk_text(""), [])
        self.assertEqual(_chunk_text("   "), [])
        self.assertEqual(_chunk_text(None), [])

    def test_short_text_single_chunk(self):
        text = "这是一段短文本。"
        chunks = _chunk_text(text, max_chunk_chars=800)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], text)

    def test_paragraph_splitting(self):
        text = "段落一。\n段落二。\n段落三。"
        chunks = _chunk_text(text, max_chunk_chars=6, overlap=0)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0], "段落一。")
        self.assertEqual(chunks[1], "段落二。")
        self.assertEqual(chunks[2], "段落三。")

    def test_long_paragraph_split_at_sentence(self):
        text = "句子一。句子二。句子三。句子四。"
        chunks = _chunk_text(text, max_chunk_chars=12, overlap=0)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 16)  # allow some overflow for sentence boundaries

    def test_overlap_applied(self):
        text = "第一段很长的内容需要超过二十个字符才能触发分块。\n第二段也很长的内容同样需要超过二十个字符。\n第三段也是一样很长的内容。"
        chunks = _chunk_text(text, max_chunk_chars=20, overlap=5)
        self.assertGreaterEqual(len(chunks), 2)


class ContentHashTestCase(unittest.TestCase):
    def test_deterministic(self):
        h1 = _content_hash("hello world")
        h2 = _content_hash("hello world")
        self.assertEqual(h1, h2)

    def test_different_text_different_hash(self):
        h1 = _content_hash("hello")
        h2 = _content_hash("world")
        self.assertNotEqual(h1, h2)

    def test_empty_string(self):
        h = _content_hash("")
        self.assertEqual(len(h), 64)  # SHA-256 hex


class SplitLongParagraphTestCase(unittest.TestCase):
    def test_short_paragraph(self):
        result = _split_long_paragraph("短段落。", 100, 10)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "短段落。")

    def test_long_paragraph_splits(self):
        para = "句子一。" * 50
        result = _split_long_paragraph(para, 20, 5)
        self.assertGreater(len(result), 1)


class DetectFTS5TestCase(unittest.TestCase):
    def test_fts5_detection_on_memory_sqlite(self):
        """Standard Python 3.9+ ships SQLite with FTS5 — detection should return True."""
        import app.services.rag.indexer as mod
        mod._fts5_available = None

        engine = create_engine("sqlite:///:memory:")
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            result = detect_fts5_available(db)
            self.assertTrue(result, "Expected FTS5 to be available in Python's bundled SQLite")
        finally:
            db.close()
            mod._fts5_available = None

    def test_fts5_detection_caches_result(self):
        import app.services.rag.indexer as mod
        mod._fts5_available = None

        engine = create_engine("sqlite:///:memory:")
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            first = detect_fts5_available(db)
            # Second call should return cached value without hitting DB
            second = detect_fts5_available(db)
            self.assertEqual(first, second)
        finally:
            db.close()
            mod._fts5_available = None

    def test_fts5_detection_returns_false_when_unavailable(self):
        import app.services.rag.indexer as mod
        mod._fts5_available = None
        db = MagicMock()
        db.execute.side_effect = Exception("no such module: fts5")
        db.rollback = MagicMock()
        result = detect_fts5_available(db)
        self.assertFalse(result)
        mod._fts5_available = None  # reset


class IndexDocumentTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        # Try to create FTS table
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

    def test_index_worldbuilding_entry(self):
        entry = WorldbuildingEntry(
            id="w1", project_id="p1", dimension="geography",
            title="北方山脉", content="连绵千里的山脉，终年积雪。传说中有龙族栖息。",
        )
        self.db.add(entry)
        self.db.commit()

        result = index_document(self.db, "p1", "worldbuilding", "w1")
        self.assertEqual(result["chunks_created"], 1)
        self.assertFalse(result["skipped"])

        doc = self.db.query(RagDocument).filter(
            RagDocument.source_type == "worldbuilding",
            RagDocument.source_id == "w1",
        ).first()
        self.assertIsNotNone(doc)
        self.assertEqual(doc.chunk_count, 1)

        chunks = self.db.query(RagChunk).filter(RagChunk.source_id == "w1").all()
        self.assertEqual(len(chunks), 1)

    def test_index_chapter(self):
        chapter = Chapter(
            id="c1", project_id="p1", title="第一章 出发",
            content="这是第一章的内容。\n\n第二段落。\n\n" + "很长的内容。" * 200,
        )
        self.db.add(chapter)
        self.db.commit()

        result = index_document(self.db, "p1", "chapter", "c1")
        self.assertGreater(result["chunks_created"], 0)

    def test_index_nonexistent_returns_zero(self):
        result = index_document(self.db, "p1", "worldbuilding", "nonexistent")
        self.assertEqual(result["chunks_created"], 0)
        self.assertTrue(result["skipped"])

    def test_index_unknown_type_skipped(self):
        result = index_document(self.db, "p1", "unknown_type", "id1")
        self.assertTrue(result["skipped"])


class ReindexProjectTestCase(unittest.TestCase):
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

    def test_reindex_empty_project(self):
        result = reindex_project(self.db, "p1")
        self.assertEqual(result["total_chunks"], 0)

    def test_reindex_with_entries(self):
        for i in range(5):
            self.db.add(WorldbuildingEntry(
                id=f"w{i}", project_id="p1", dimension="geography",
                title=f"设定{i}", content=f"内容{i}" * 50,
            ))
        self.db.commit()

        result = reindex_project(self.db, "p1")
        self.assertEqual(result["total_chunks"], 5)
        self.assertEqual(result["by_type"]["worldbuilding"], 5)


class MarkDirtyTestCase(unittest.TestCase):
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
        self.project = Project(id="p1", title="Test")
        self.db.add(self.project)
        self.db.add(WorldbuildingEntry(
            id="w1", project_id="p1", dimension="geography",
            title="北方", content="山脉连绵。",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_mark_dirty_removes_chunks(self):
        index_document(self.db, "p1", "worldbuilding", "w1")
        self.assertEqual(self.db.query(RagChunk).count(), 1)

        mark_dirty(self.db, "p1", "worldbuilding", "w1")
        self.assertEqual(self.db.query(RagChunk).count(), 0)
        self.assertEqual(self.db.query(RagDocument).count(), 0)


class EnsureIndexedTestCase(unittest.TestCase):
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
        self.project = Project(id="p1", title="Test")
        self.db.add(self.project)
        self.db.add(WorldbuildingEntry(
            id="w1", project_id="p1", dimension="geography",
            title="北方", content="山脉连绵。",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_ensure_indexed_creates_chunks(self):
        result = ensure_indexed(self.db, "p1", "worldbuilding", "w1")
        self.assertTrue(result)
        self.assertEqual(self.db.query(RagChunk).count(), 1)

    def test_ensure_indexed_skips_unchanged(self):
        ensure_indexed(self.db, "p1", "worldbuilding", "w1")
        self.assertEqual(self.db.query(RagChunk).count(), 1)

        # Call again with same content — should not create duplicate chunks
        ensure_indexed(self.db, "p1", "worldbuilding", "w1")
        self.assertEqual(self.db.query(RagChunk).count(), 1)


class ProjectHasChunksTestCase(unittest.TestCase):
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
        self.project = Project(id="p1", title="Test")
        self.db.add(self.project)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_empty_project_returns_false(self):
        self.assertFalse(project_has_chunks(self.db, "p1"))

    def test_project_with_chunks_returns_true(self):
        self.db.add(WorldbuildingEntry(
            id="w1", project_id="p1", dimension="geography",
            title="北方", content="山脉连绵。",
        ))
        self.db.commit()
        index_document(self.db, "p1", "worldbuilding", "w1")
        self.assertTrue(project_has_chunks(self.db, "p1"))

    def test_source_type_filter(self):
        self.db.add(WorldbuildingEntry(
            id="w1", project_id="p1", dimension="geography",
            title="北方", content="山脉连绵。",
        ))
        self.db.commit()
        index_document(self.db, "p1", "worldbuilding", "w1")

        self.assertTrue(project_has_chunks(self.db, "p1", source_types=["worldbuilding"]))
        self.assertFalse(project_has_chunks(self.db, "p1", source_types=["character"]))


class SearchContextAutoIndexTestCase(unittest.TestCase):
    """Test that search_context auto-indexes when no chunks exist."""

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

    def test_search_worldbuilding_auto_indexes(self):
        """New project with worldbuilding but no RagDocument — search should auto-index."""
        self.db.add(WorldbuildingEntry(
            id="w1", project_id="p1", dimension="geography",
            title="灵气节点", content="东北方向存在灵气节点，节点会产生规律波动。",
        ))
        self.db.commit()

        # Verify no chunks exist yet
        self.assertFalse(project_has_chunks(self.db, "p1"))

        # Call search_context handler directly
        import asyncio
        from app.services.workspace.tools.rag_tools import search_context
        result = asyncio.run(search_context(self.db, "p1", {"query": "灵气节点"}))

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["data"]["auto_indexed"])
        self.assertGreater(len(result["data"]["results"]), 0)
        self.assertIn("首次检索", result["detail"])

        # Verify chunks now exist
        self.assertTrue(project_has_chunks(self.db, "p1"))

    def test_search_character_auto_indexes(self):
        """New project with character but no index — search should auto-index."""
        self.db.add(Character(
            id="c1", project_id="p1", name="张三",
            personality="性格豪爽，嫉恶如仇。", background="出身贫寒。",
        ))
        self.db.commit()

        self.assertFalse(project_has_chunks(self.db, "p1"))

        import asyncio
        from app.services.workspace.tools.rag_tools import search_context
        result = asyncio.run(search_context(self.db, "p1", {
            "query": "豪爽",
            "source_types": ["character"],
        }))

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["data"]["auto_indexed"])
        self.assertGreater(len(result["data"]["results"]), 0)

    def test_search_chapter_auto_indexes(self):
        """New project with chapter but no index — search should auto-index."""
        self.db.add(Chapter(
            id="c1", project_id="p1", title="第一章 出发",
            content="少年背着行囊踏上了旅程。前方的路充满未知。",
        ))
        self.db.commit()

        import asyncio
        from app.services.workspace.tools.rag_tools import search_context
        result = asyncio.run(search_context(self.db, "p1", {"query": "旅程"}))

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["data"]["auto_indexed"])
        self.assertGreater(len(result["data"]["results"]), 0)

    def test_search_with_source_types_only_indexes_specified(self):
        """When source_types specified, only those types should be indexed."""
        self.db.add(WorldbuildingEntry(
            id="w1", project_id="p1", dimension="geography",
            title="灵气节点", content="东北方向存在灵气节点。",
        ))
        self.db.add(Character(
            id="c1", project_id="p1", name="张三", personality="豪爽。",
        ))
        self.db.commit()

        import asyncio
        from app.services.workspace.tools.rag_tools import search_context
        result = asyncio.run(search_context(self.db, "p1", {
            "query": "灵气",
            "source_types": ["worldbuilding"],
        }))

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["data"]["auto_indexed"])
        # Character should NOT be indexed since we only requested worldbuilding
        self.assertFalse(project_has_chunks(self.db, "p1", source_types=["character"]))
        self.assertTrue(project_has_chunks(self.db, "p1", source_types=["worldbuilding"]))

    def test_search_already_indexed_skips_reindex(self):
        """When chunks already exist, auto-index should be skipped."""
        self.db.add(WorldbuildingEntry(
            id="w1", project_id="p1", dimension="geography",
            title="灵气节点", content="东北方向存在灵气节点。",
        ))
        self.db.commit()

        # Pre-index
        index_document(self.db, "p1", "worldbuilding", "w1")

        import asyncio
        from app.services.workspace.tools.rag_tools import search_context
        result = asyncio.run(search_context(self.db, "p1", {"query": "灵气"}))

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["data"]["auto_indexed"])


if __name__ == "__main__":
    unittest.main()
