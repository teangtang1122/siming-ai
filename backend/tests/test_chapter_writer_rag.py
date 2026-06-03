"""Tests for chapter_writer RAG integration: alias resolution, snapshot metadata, lazy indexing."""
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base, Project, Character, CharacterAlias, CharacterRelationship,
    WorldbuildingEntry, OutlineNode, OutlineNodeCharacter, Chapter, ChapterSummary,
)
from app.services.workspace.tools.chapter_writer import (
    _build_character_details_with_rag,
    _build_enriched_snapshot,
    _section_text,
    _format_character_detail,
)
from app.services.rag.context_packer import PackedContext, ContextSection


class SectionTextTestCase(unittest.TestCase):
    def test_returns_content_for_matching_category(self):
        packed = PackedContext(
            sections=[
                ContextSection(category="worldbuilding", title="t", content="hello",
                               source_type="worldbuilding", source_id="",
                               chunk_ids=[], selection_reason="r", score=1.0, used_chars=5),
            ],
            total_used_chars=5, budget={}, used_chars={},
            explanations=[], warnings=[], fts_available=False,
        )
        self.assertEqual(_section_text(packed, "worldbuilding"), "hello")

    def test_returns_none_for_missing_category(self):
        packed = PackedContext(
            sections=[], total_used_chars=0, budget={}, used_chars={},
            explanations=[], warnings=[], fts_available=False,
        )
        self.assertIsNone(_section_text(packed, "worldbuilding"))


class AliasResolutionTestCase(unittest.TestCase):
    """Test character alias resolution in chapter_writer helpers."""

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
        char = Character(id="c1", project_id="p1", name="陆老爷子", role_type="配角",
                         personality="沉稳")
        self.db.add(char)
        self.db.commit()
        text, aliases, rag_used = _build_character_details_with_rag(
            self.db, "p1", None, ["陆老爷子"], "测试"
        )
        self.assertIn("陆老爷子", text)
        self.assertEqual(aliases, {})
        self.assertFalse(rag_used)

    def test_alias_resolves_to_canonical(self):
        char = Character(id="c1", project_id="p1", name="陆老爷子", role_type="配角",
                         personality="沉稳")
        alias = CharacterAlias(id="a1", project_id="p1", character_id="c1", alias="爷爷")
        self.db.add_all([char, alias])
        self.db.commit()
        text, aliases, rag_used = _build_character_details_with_rag(
            self.db, "p1", None, ["爷爷"], "测试"
        )
        self.assertIn("陆老爷子", text)
        self.assertEqual(aliases, {"爷爷": "陆老爷子"})
        self.assertFalse(rag_used)

    def test_no_duplicate_when_name_and_alias_match_same_char(self):
        char = Character(id="c1", project_id="p1", name="陆老爷子", role_type="配角",
                         personality="沉稳")
        alias = CharacterAlias(id="a1", project_id="p1", character_id="c1", alias="爷爷")
        self.db.add_all([char, alias])
        self.db.commit()
        text, aliases, rag_used = _build_character_details_with_rag(
            self.db, "p1", None, ["陆老爷子", "爷爷"], "测试"
        )
        # Should only have one 【陆老爷子】 block
        self.assertEqual(text.count("【陆老爷子】"), 1)

    @patch("app.services.workspace.tools.chapter_writer.search_chunks")
    def test_rag_fallback_when_no_name_match(self, mock_search):
        char = Character(id="c1", project_id="p1", name="神秘人", role_type="反派",
                         personality="阴险")
        self.db.add(char)
        self.db.commit()
        mock_search.return_value = [
            MagicMock(source_id="c1", chunk_id="ch1", title="神秘人",
                      content="反派角色", metadata={}, score=5.0, reason="LIKE"),
        ]
        text, aliases, rag_used = _build_character_details_with_rag(
            self.db, "p1", None, ["不存在的角色"], "神秘人出现"
        )
        self.assertTrue(rag_used)
        self.assertIn("神秘人", text)

    def test_empty_involved_names(self):
        text, aliases, rag_used = _build_character_details_with_rag(
            self.db, "p1", None, [], "测试"
        )
        self.assertEqual(text, "未指定角色。")
        self.assertEqual(aliases, {})
        self.assertFalse(rag_used)

    def test_outline_node_linked_characters(self):
        """Only outline_node_id, no involved_characters — linked chars should appear."""
        char = Character(id="c1", project_id="p1", name="陆老爷子", role_type="配角",
                         personality="沉稳", background="退休老干部")
        node = OutlineNode(id="n1", project_id="p1", title="第151章", node_type="chapter")
        link = OutlineNodeCharacter(id="l1", outline_node_id="n1", character_id="c1")
        self.db.add_all([char, node, link])
        self.db.commit()
        text, aliases, rag_used = _build_character_details_with_rag(
            self.db, "p1", "n1", [], "测试写作"
        )
        self.assertIn("陆老爷子", text)
        self.assertIn("沉稳", text)
        self.assertEqual(aliases, {})
        self.assertFalse(rag_used)


class SnapshotMetadataTestCase(unittest.TestCase):
    """Test that context_snapshot contains only metadata, not full content."""

    def test_snapshot_sections_have_no_content_field(self):
        packed = PackedContext(
            sections=[
                ContextSection(
                    category="worldbuilding", title="世界观设定",
                    content="这是一段很长的世界观内容" * 100,
                    source_type="worldbuilding", source_id="",
                    chunk_ids=["c1", "c2"], selection_reason="RAG检索(10条命中，8条入选)",
                    score=45.2, used_chars=6800,
                ),
            ],
            total_used_chars=6800,
            budget={"max_worldbuilding_chars": 8000},
            used_chars={"worldbuilding": 6800},
            explanations=["世界观：RAG检索"],
            warnings=[],
            fts_available=True,
        )
        snapshot = _build_enriched_snapshot(
            packed, "node1", ["角色A"], {"爷爷": "陆老爷子"},
            "【角色A】\n...", False,
        )
        # sections should have metadata but NOT content
        self.assertEqual(len(snapshot["sections"]), 1)
        section = snapshot["sections"][0]
        self.assertIn("category", section)
        self.assertIn("selection_reason", section)
        self.assertIn("used_chars", section)
        self.assertIn("chunk_count", section)
        self.assertNotIn("content", section)

    def test_snapshot_rag_used_true_when_chunks_present(self):
        packed = PackedContext(
            sections=[
                ContextSection(category="worldbuilding", title="t", content="c",
                               source_type="worldbuilding", source_id="",
                               chunk_ids=["c1"], selection_reason="RAG",
                               score=1.0, used_chars=100),
            ],
            total_used_chars=100, budget={}, used_chars={},
            explanations=[], warnings=[], fts_available=True,
        )
        snapshot = _build_enriched_snapshot(packed, None, [], {}, "未指定角色。", False)
        self.assertTrue(snapshot["rag_used"])

    def test_snapshot_rag_used_false_when_no_chunks(self):
        packed = PackedContext(
            sections=[
                ContextSection(category="worldbuilding", title="t", content="c",
                               source_type="worldbuilding", source_id="",
                               chunk_ids=[], selection_reason="传统",
                               score=1.0, used_chars=100),
            ],
            total_used_chars=100, budget={}, used_chars={},
            explanations=[], warnings=[], fts_available=False,
        )
        snapshot = _build_enriched_snapshot(packed, None, [], {}, "未指定角色。", False)
        self.assertFalse(snapshot["rag_used"])

    def test_snapshot_includes_resolved_aliases(self):
        packed = PackedContext(
            sections=[], total_used_chars=0, budget={}, used_chars={},
            explanations=[], warnings=[], fts_available=False,
        )
        snapshot = _build_enriched_snapshot(
            packed, None, ["爷爷"], {"爷爷": "陆老爷子"}, "【陆老爷子】", False,
        )
        self.assertEqual(snapshot["resolved_aliases"], {"爷爷": "陆老爷子"})


if __name__ == "__main__":
    unittest.main()
