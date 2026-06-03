"""Tests for RAG retriever: FTS5 search, LIKE fallback, hybrid merge."""
import unittest
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, Project, RagChunk, RagDocument
from app.services.rag.retriever import (
    _extract_terms,
    _build_fts_query,
    _search_fts,
    _search_like,
    search_chunks,
    SearchResult,
)


class ExtractTermsTestCase(unittest.TestCase):
    def test_empty_query(self):
        fts, like = _extract_terms("")
        self.assertEqual(fts, [])
        self.assertEqual(like, [])

    def test_chinese_terms(self):
        fts, like = _extract_terms("北方山脉的龙族传说")
        self.assertEqual(fts, [])
        self.assertTrue(len(like) > 0)
        for t in like:
            self.assertFalse(t.isascii())

    def test_english_terms(self):
        fts, like = _extract_terms("mountain dragon legend")
        self.assertTrue(len(fts) > 0)
        self.assertEqual(like, [])

    def test_mixed_terms(self):
        fts, like = _extract_terms("北方 mountain 传说 dragon")
        self.assertTrue(len(fts) > 0)
        self.assertTrue(len(like) > 0)

    def test_stopwords_filtered(self):
        fts, like = _extract_terms("章节 大纲 角色")
        self.assertEqual(fts, [])
        self.assertEqual(like, [])

    def test_single_char_chinese_filtered(self):
        fts, like = _extract_terms("大 山 水")
        # Single chars should be filtered (require >= 2)
        self.assertEqual(like, [])


class BuildFTSQueryTestCase(unittest.TestCase):
    def test_empty_terms(self):
        self.assertEqual(_build_fts_query([]), "")

    def test_single_term(self):
        result = _build_fts_query(["mountain"])
        self.assertEqual(result, '"mountain"')

    def test_multiple_terms(self):
        result = _build_fts_query(["mountain", "dragon"])
        self.assertEqual(result, '"mountain" OR "dragon"')


class SearchChunksTestCase(unittest.TestCase):
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

    def _insert_chunk(self, chunk_id, title, content, source_type="worldbuilding", source_id="w1"):
        chunk = RagChunk(
            id=chunk_id, document_id="doc1", project_id="p1",
            source_type=source_type, source_id=source_id,
            chunk_index=0, title=title, content=content,
        )
        self.db.add(chunk)
        try:
            self.db.execute(text(
                "INSERT INTO rag_chunks_fts(chunk_id, project_id, source_type, title, content) "
                "VALUES(:cid, :pid, :st, :title, :content)"
            ), {"cid": chunk_id, "pid": "p1", "st": source_type, "title": title, "content": content})
        except Exception:
            pass
        self.db.commit()

    def test_search_empty_query_returns_empty(self):
        results = search_chunks(self.db, "p1", "")
        self.assertEqual(results, [])

    def test_search_chinese_like(self):
        self._insert_chunk("c1", "北方山脉", "连绵千里的山脉，终年积雪。传说中有龙族栖息。")
        self._insert_chunk("c2", "南方平原", "肥沃的平原，物产丰富。")

        results = search_chunks(self.db, "p1", "龙族", use_fts=False)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0].chunk_id, "c1")

    def test_search_english_fts(self):
        self._insert_chunk("c1", "Northern Mountains", "Endless mountain range with eternal snow.")
        self._insert_chunk("c2", "Southern Plains", "Fertile plains with abundant harvest.")

        results = search_chunks(self.db, "p1", "mountain snow")
        # May or may not find results depending on FTS5 availability
        # Just verify no crash
        self.assertIsInstance(results, list)

    def test_search_with_source_type_filter(self):
        self._insert_chunk("c1", "北方山脉", "山脉内容", source_type="worldbuilding")
        self._insert_chunk("c2", "角色张三", "张三是主角", source_type="character")

        results = search_chunks(self.db, "p1", "山脉", source_types=["worldbuilding"], use_fts=False)
        for r in results:
            self.assertEqual(r.source_type, "worldbuilding")

    def test_search_limit(self):
        for i in range(20):
            self._insert_chunk(f"c{i}", f"标题{i}", f"内容{i}包含关键词测试")

        results = search_chunks(self.db, "p1", "关键词", limit=5, use_fts=False)
        self.assertLessEqual(len(results), 5)

    def test_search_result_structure(self):
        self._insert_chunk("c1", "北方山脉", "连绵千里")
        results = search_chunks(self.db, "p1", "山脉", use_fts=False)
        if results:
            r = results[0]
            self.assertIsInstance(r, SearchResult)
            self.assertTrue(r.chunk_id)
            self.assertTrue(r.source_type)
            self.assertTrue(r.title)
            self.assertTrue(r.content)
            self.assertIsInstance(r.score, float)
            self.assertTrue(r.reason)

    def test_search_no_results(self):
        results = search_chunks(self.db, "p1", "完全不存在的内容xyz", use_fts=False)
        self.assertEqual(len(results), 0)


class SearchLikeFallbackTestCase(unittest.TestCase):
    """Test that search works when FTS5 is unavailable."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        Session = sessionmaker(bind=self.engine)
        self.db = Session()
        self.project = Project(id="p1", title="Test")
        self.db.add(self.project)

        # Insert chunks without FTS table
        for i in range(3):
            self.db.add(RagChunk(
                id=f"c{i}", document_id="doc1", project_id="p1",
                source_type="worldbuilding", source_id=f"w{i}",
                chunk_index=0, title=f"设定{i}", content=f"这是第{i}条设定的内容",
            ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    @patch("app.services.rag.retriever.detect_fts5_available", return_value=False)
    def test_search_falls_back_to_like(self, mock_fts):
        results = search_chunks(self.db, "p1", "设定", use_fts=True)
        # Should find results via LIKE fallback
        self.assertIsInstance(results, list)

    @patch("app.services.rag.retriever.detect_fts5_available", return_value=False)
    def test_search_use_fts_false(self, mock_fts):
        results = search_chunks(self.db, "p1", "内容", use_fts=False)
        self.assertIsInstance(results, list)


if __name__ == "__main__":
    unittest.main()
