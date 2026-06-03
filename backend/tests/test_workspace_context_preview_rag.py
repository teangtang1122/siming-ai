"""Tests for preview_writing_context RAG integration."""
import unittest
from unittest.mock import patch, MagicMock
import asyncio

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base, Project, Character, CharacterAlias, CharacterRelationship,
    WorldbuildingEntry, OutlineNode, Chapter, ChapterSummary, OutlineNodeCharacter,
)
from app.services.workspace.tools.context_preview import (
    preview_writing_context,
    _resolve_characters_with_aliases,
    _section_text,
)


class ResolveCharactersWithAliasesTestCase(unittest.TestCase):
    """Test _resolve_characters_with_aliases helper."""

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

    def test_direct_name_match(self):
        char = Character(id="c1", project_id="p1", name="陆糖", role_type="主角")
        self.db.add(char)
        self.db.commit()
        chars, aliases = _resolve_characters_with_aliases(self.db, "p1", None, ["陆糖"], 10)
        self.assertEqual(len(chars), 1)
        self.assertEqual(chars[0].name, "陆糖")
        self.assertEqual(aliases, {})

    def test_alias_match(self):
        char = Character(id="c1", project_id="p1", name="陆老爷子", role_type="配角")
        alias = CharacterAlias(id="a1", project_id="p1", character_id="c1", alias="爷爷")
        self.db.add_all([char, alias])
        self.db.commit()
        chars, aliases = _resolve_characters_with_aliases(self.db, "p1", None, ["爷爷"], 10)
        self.assertEqual(len(chars), 1)
        self.assertEqual(chars[0].name, "陆老爷子")
        self.assertEqual(aliases, {"爷爷": "陆老爷子"})

    def test_outline_node_linked_characters(self):
        char = Character(id="c1", project_id="p1", name="角色A", role_type="主角")
        node = OutlineNode(id="n1", project_id="p1", title="节点1", node_type="chapter")
        link = OutlineNodeCharacter(id="l1", outline_node_id="n1", character_id="c1")
        self.db.add_all([char, node, link])
        self.db.commit()
        chars, aliases = _resolve_characters_with_aliases(self.db, "p1", "n1", [], 10)
        self.assertEqual(len(chars), 1)
        self.assertEqual(chars[0].name, "角色A")

    def test_deduplication(self):
        char = Character(id="c1", project_id="p1", name="陆老爷子", role_type="配角")
        alias = CharacterAlias(id="a1", project_id="p1", character_id="c1", alias="爷爷")
        self.db.add_all([char, alias])
        self.db.commit()
        chars, aliases = _resolve_characters_with_aliases(
            self.db, "p1", None, ["陆老爷子", "爷爷"], 10
        )
        self.assertEqual(len(chars), 1)

    def test_limit_respected(self):
        for i in range(5):
            self.db.add(Character(id=f"c{i}", project_id="p1", name=f"角色{i}"))
        self.db.commit()
        chars, _ = _resolve_characters_with_aliases(
            self.db, "p1", None, [f"角色{i}" for i in range(5)], 3
        )
        self.assertEqual(len(chars), 3)


class PreviewWritingContextIntegrationTestCase(unittest.TestCase):
    """Integration tests for preview_writing_context with RAG."""

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
                title=f"设定{i}", content=f"这是第{i}条世界观设定内容",
                dimension="geography", sort_order=i,
            ))
        self.db.commit()

    def _add_character_with_alias(self):
        char = Character(id="c1", project_id="p1", name="陆老爷子", role_type="配角",
                         personality="沉稳", background="退休老干部")
        alias = CharacterAlias(id="a1", project_id="p1", character_id="c1", alias="爷爷")
        self.db.add_all([char, alias])
        self.db.commit()

    @patch("app.services.workspace.tools.context_preview.reindex_project_types")
    @patch("app.services.workspace.tools.context_preview.project_has_chunks", return_value=True)
    @patch("app.services.rag.context_packer.detect_fts5_available", return_value=False)
    def test_returns_rag_sections(self, _mock_fts, _mock_has, _mock_reindex):
        self._add_worldbuilding(3)
        result = asyncio.run(preview_writing_context(self.db, "p1", {
            "requirements": "测试写作",
        }))
        self.assertEqual(result["tool"], "preview_writing_context")
        self.assertIn("rag_sections", result["data"])
        self.assertIsInstance(result["data"]["rag_sections"], list)
        self.assertIn("total_used_chars", result["data"])
        self.assertIn("explanations", result["data"])
        self.assertIn("rag_used", result["data"])

    @patch("app.services.workspace.tools.context_preview.reindex_project_types")
    @patch("app.services.workspace.tools.context_preview.project_has_chunks", return_value=True)
    @patch("app.services.rag.context_packer.detect_fts5_available", return_value=False)
    def test_alias_resolution_in_preview(self, _mock_fts, _mock_has, _mock_reindex):
        self._add_character_with_alias()
        result = asyncio.run(preview_writing_context(self.db, "p1", {
            "involved_characters": ["爷爷"],
        }))
        resolved = result["data"].get("resolved_aliases", {})
        self.assertEqual(resolved, {"爷爷": "陆老爷子"})
        # Character should appear in the characters list
        self.assertTrue(len(result["data"]["characters"]) > 0)

    @patch("app.services.workspace.tools.context_preview.reindex_project_types")
    @patch("app.services.workspace.tools.context_preview.project_has_chunks", return_value=True)
    @patch("app.services.rag.context_packer.detect_fts5_available", return_value=False)
    def test_backward_compatible_fields_present(self, _mock_fts, _mock_has, _mock_reindex):
        self._add_worldbuilding(3)
        result = asyncio.run(preview_writing_context(self.db, "p1", {
            "requirements": "测试",
        }))
        data = result["data"]
        # Old fields should still exist
        self.assertIn("outline_context", data)
        self.assertIn("recent_chapters", data)
        self.assertIn("recent_summaries_text", data)
        self.assertIn("characters", data)
        self.assertIn("relationships", data)
        self.assertIn("world_context", data)
        self.assertIn("warnings", data)
        self.assertIn("requirements_preview", data)

    @patch("app.services.workspace.tools.context_preview.reindex_project_types")
    @patch("app.services.workspace.tools.context_preview.project_has_chunks")
    @patch("app.services.rag.context_packer.detect_fts5_available", return_value=False)
    def test_lazy_index_triggered_per_type(self, _mock_fts, mock_has, mock_reindex):
        """Missing types should trigger reindex_project_types for those types."""
        # Simulate: worldbuilding has chunks, character doesn't
        def has_chunks_side_effect(db, project_id, source_types=None):
            if source_types and "character" in source_types:
                return False
            return True
        mock_has.side_effect = has_chunks_side_effect
        mock_reindex.return_value = {"total_chunks": 5, "by_type": {"character": 5}}

        self._add_worldbuilding(3)
        asyncio.run(preview_writing_context(self.db, "p1", {
            "requirements": "测试",
        }))
        mock_reindex.assert_called_once()
        call_args = mock_reindex.call_args
        self.assertIn("character", call_args[1].get("source_types", call_args[0][2] if len(call_args[0]) > 2 else []))


if __name__ == "__main__":
    unittest.main()
