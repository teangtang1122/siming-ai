"""Tests for the memory system: tools, RAG sync, and category compatibility."""
import asyncio
import unittest

from sqlalchemy import text

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, Project, AssistantMemory, RagDocument, RagChunk
from app.services.workspace.tools.memory import (
    normalize_category,
    remember,
    recall,
    forget,
    list_memories,
    VALID_CATEGORIES,
    LEGACY_CATEGORY_MAP,
)
from app.services.agent.bridge import _build_memory_context, _inject_memory_into_intent


def run(coro):
    return asyncio.run(coro)


class CategoryNormalizationTest(unittest.TestCase):
    def test_new_categories_pass_through(self):
        for cat in VALID_CATEGORIES:
            self.assertEqual(normalize_category(cat), cat)

    def test_legacy_categories_mapped(self):
        for old, new in LEGACY_CATEGORY_MAP.items():
            self.assertEqual(normalize_category(old), new)

    def test_unknown_defaults_to_user_preference(self):
        self.assertEqual(normalize_category("unknown"), "user_preference")
        self.assertEqual(normalize_category(""), "user_preference")

    def test_whitespace_stripped(self):
        self.assertEqual(normalize_category("  preference  "), "user_preference")


class MemoryToolsTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        # Create FTS5 virtual table for RAG sync tests
        try:
            self.db.execute(text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5("
                "chunk_id UNINDEXED, project_id UNINDEXED, source_type UNINDEXED, "
                "title, content, metadata_json, tokenize='unicode61'"
                ")"
            ))
        except Exception:
            pass
        self.project = Project(id="proj-1", title="Test", description="")
        self.db.add(self.project)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_remember_create(self):
        result = run(remember(self.db, "proj-1", {
            "key": "风格偏好", "value": "不喜欢太文艺", "category": "user_preference", "importance": 8,
        }))
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["data"][0]["updated"])
        mem = self.db.query(AssistantMemory).filter_by(key="风格偏好").first()
        self.assertIsNotNone(mem)
        self.assertEqual(mem.category, "user_preference")

    def test_remember_upsert(self):
        run(remember(self.db, "proj-1", {"key": "k", "value": "v1", "category": "user_preference"}))
        result = run(remember(self.db, "proj-1", {"key": "k", "value": "v2", "category": "user_preference"}))
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["data"][0]["updated"])
        mem = self.db.query(AssistantMemory).filter_by(key="k").first()
        self.assertEqual(mem.value, "v2")

    def test_remember_legacy_category_normalized(self):
        run(remember(self.db, "proj-1", {"key": "k", "value": "v", "category": "preference"}))
        mem = self.db.query(AssistantMemory).filter_by(key="k").first()
        self.assertEqual(mem.category, "user_preference")

    def test_remember_empty_key_value_errors(self):
        result = run(remember(self.db, "proj-1", {"key": "", "value": "v"}))
        self.assertEqual(result["status"], "error")

    def test_recall_keyword(self):
        run(remember(self.db, "proj-1", {"key": "文风", "value": "古风", "category": "writing_style"}))
        run(remember(self.db, "proj-1", {"key": "节奏", "value": "快", "category": "user_preference"}))
        result = run(recall(self.db, "proj-1", {"query": "文风"}))
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["data"][0]["key"], "文风")

    def test_recall_category_filter(self):
        run(remember(self.db, "proj-1", {"key": "k1", "value": "v1", "category": "writing_style"}))
        run(remember(self.db, "proj-1", {"key": "k2", "value": "v2", "category": "user_preference"}))
        result = run(recall(self.db, "proj-1", {"category": "writing_style"}))
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["data"][0]["category"], "writing_style")

    def test_recall_legacy_category_matches_old_data(self):
        mem = AssistantMemory(project_id="proj-1", category="preference", key="k", value="v")
        self.db.add(mem)
        self.db.commit()
        result = run(recall(self.db, "proj-1", {"category": "user_preference"}))
        self.assertEqual(len(result["data"]), 1)

    def test_forget_by_id(self):
        run(remember(self.db, "proj-1", {"key": "k", "value": "v"}))
        mem = self.db.query(AssistantMemory).filter_by(key="k").first()
        result = run(forget(self.db, "proj-1", {"id": mem.id}))
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(self.db.query(AssistantMemory).filter_by(key="k").first())

    def test_forget_by_key(self):
        run(remember(self.db, "proj-1", {"key": "k", "value": "v"}))
        result = run(forget(self.db, "proj-1", {"key": "k"}))
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(self.db.query(AssistantMemory).filter_by(key="k").first())

    def test_list_memories(self):
        run(remember(self.db, "proj-1", {"key": "k1", "value": "v1", "category": "writing_style"}))
        run(remember(self.db, "proj-1", {"key": "k2", "value": "v2", "category": "user_preference"}))
        result = run(list_memories(self.db, "proj-1", {}))
        self.assertEqual(len(result["data"]), 2)

    def test_list_memories_filter_category(self):
        run(remember(self.db, "proj-1", {"key": "k1", "value": "v1", "category": "writing_style"}))
        run(remember(self.db, "proj-1", {"key": "k2", "value": "v2", "category": "user_preference"}))
        result = run(list_memories(self.db, "proj-1", {"category": "writing_style"}))
        self.assertEqual(len(result["data"]), 1)

    def test_project_isolation(self):
        run(remember(self.db, "proj-1", {"key": "k1", "value": "v1"}))
        run(remember(self.db, "proj-other", {"key": "k2", "value": "v2"}))
        result = run(recall(self.db, "proj-1", {}))
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["data"][0]["key"], "k1")

    def test_plan_memory_context_injects_requirements_without_changing_outline_query(self):
        run(remember(self.db, "proj-1", {
            "key": "禁用表达",
            "value": "少用比喻",
            "category": "writing_style",
            "importance": 9,
        }))
        run(remember(self.db, "proj-1", {
            "key": "青石村",
            "value": "村口有一口老井",
            "category": "project_fact",
            "importance": 8,
        }))

        context = _build_memory_context(self.db, "proj-1", "帮我写第151章，青石村视角")
        self.assertIn("少用比喻", context)
        self.assertIn("村口有一口老井", context)

        intent = {
            "intent_type": "chapter",
            "requirements": "帮我写第151章",
            "outline_query": "帮我写第151章",
        }
        enriched = _inject_memory_into_intent(intent, context)
        self.assertIn("少用比喻", enriched["requirements"])
        self.assertIn("村口有一口老井", enriched["requirements"])
        self.assertEqual(enriched["outline_query"], "帮我写第151章")


class RAGSyncTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        try:
            self.db.execute(text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5("
                "chunk_id UNINDEXED, project_id UNINDEXED, source_type UNINDEXED, "
                "title, content, metadata_json, tokenize='unicode61'"
                ")"
            ))
        except Exception:
            pass
        self.project = Project(id="proj-1", title="Test", description="")
        self.db.add(self.project)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_remember_creates_rag_chunks(self):
        run(remember(self.db, "proj-1", {"key": "风格", "value": "古风", "category": "writing_style", "importance": 8}))
        mem = self.db.query(AssistantMemory).filter_by(key="风格").first()
        chunks = self.db.query(RagChunk).filter_by(source_type="assistant_memory", source_id=mem.id).all()
        self.assertGreater(len(chunks), 0)

    def test_forget_removes_rag_chunks(self):
        run(remember(self.db, "proj-1", {"key": "k", "value": "v"}))
        mem = self.db.query(AssistantMemory).filter_by(key="k").first()
        self.assertGreater(self.db.query(RagChunk).filter_by(source_id=mem.id).count(), 0)
        run(forget(self.db, "proj-1", {"id": mem.id}))
        self.assertEqual(self.db.query(RagChunk).filter_by(source_id=mem.id).count(), 0)

    def test_upsert_updates_rag_content(self):
        run(remember(self.db, "proj-1", {"key": "k", "value": "old content", "category": "user_preference"}))
        mem = self.db.query(AssistantMemory).filter_by(key="k").first()
        old_chunks = self.db.query(RagChunk).filter_by(source_id=mem.id).all()
        self.assertTrue(any("old content" in (c.content or "") for c in old_chunks))

        run(remember(self.db, "proj-1", {"key": "k", "value": "new content", "category": "user_preference"}))
        self.db.expire_all()
        new_chunks = self.db.query(RagChunk).filter_by(source_id=mem.id).all()
        self.assertTrue(any("new content" in (c.content or "") for c in new_chunks))
        self.assertFalse(any("old content" in (c.content or "") for c in new_chunks))


if __name__ == "__main__":
    unittest.main()
