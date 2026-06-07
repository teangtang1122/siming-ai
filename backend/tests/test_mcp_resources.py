"""Tests for MCP resource URI scheme — parsing, construction, and reading."""
import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.resources import parse_uri, build_uri, ParsedUri, list_resource_uris, get_resource_description, read_resource


class ParseUriTest(unittest.TestCase):
    """Verify moshu:// URI parsing."""

    def test_projects_index(self):
        r = parse_uri("moshu://projects")
        self.assertIsNotNone(r)
        self.assertEqual(r.resource_type, "projects_index")
        self.assertEqual(r.project_id, "")
        self.assertEqual(r.entity_id, "")

    def test_project_detail(self):
        r = parse_uri("moshu://projects/abc123")
        self.assertIsNotNone(r)
        self.assertEqual(r.resource_type, "project_detail")
        self.assertEqual(r.project_id, "abc123")

    def test_chapters_index(self):
        r = parse_uri("moshu://projects/p1/chapters")
        self.assertIsNotNone(r)
        self.assertEqual(r.resource_type, "chapters_index")
        self.assertEqual(r.project_id, "p1")

    def test_chapter_detail(self):
        r = parse_uri("moshu://projects/p1/chapters/ch99")
        self.assertIsNotNone(r)
        self.assertEqual(r.resource_type, "chapter_detail")
        self.assertEqual(r.project_id, "p1")
        self.assertEqual(r.entity_id, "ch99")

    def test_characters_index(self):
        r = parse_uri("moshu://projects/p1/characters")
        self.assertIsNotNone(r)
        self.assertEqual(r.resource_type, "characters_index")

    def test_character_detail(self):
        r = parse_uri("moshu://projects/p1/characters/c42")
        self.assertIsNotNone(r)
        self.assertEqual(r.resource_type, "character_detail")
        self.assertEqual(r.entity_id, "c42")

    def test_worldbuilding_index(self):
        r = parse_uri("moshu://projects/p1/worldbuilding")
        self.assertIsNotNone(r)
        self.assertEqual(r.resource_type, "worldbuilding_index")

    def test_worldbuilding_detail(self):
        r = parse_uri("moshu://projects/p1/worldbuilding/wb7")
        self.assertIsNotNone(r)
        self.assertEqual(r.resource_type, "worldbuilding_detail")
        self.assertEqual(r.entity_id, "wb7")

    def test_outline_index(self):
        r = parse_uri("moshu://projects/p1/outline")
        self.assertIsNotNone(r)
        self.assertEqual(r.resource_type, "outline_index")

    def test_outline_detail(self):
        r = parse_uri("moshu://projects/p1/outline/n5")
        self.assertIsNotNone(r)
        self.assertEqual(r.resource_type, "outline_detail")
        self.assertEqual(r.entity_id, "n5")

    def test_relationships(self):
        r = parse_uri("moshu://projects/p1/relationships")
        self.assertIsNotNone(r)
        self.assertEqual(r.resource_type, "relationships")

    def test_invalid_scheme_returns_none(self):
        self.assertIsNone(parse_uri("http://projects"))
        self.assertIsNone(parse_uri("file:///tmp/test"))
        self.assertIsNone(parse_uri("moshu:"))

    def test_invalid_path_returns_none(self):
        self.assertIsNone(parse_uri("moshu://invalid"))
        self.assertIsNone(parse_uri("moshu://projects/p1/invalid"))
        self.assertIsNone(parse_uri("moshu://projects/p1/chapters/x/y/z"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_uri(""))

    def test_uuid_project_id(self):
        """UUID-style project IDs should parse correctly."""
        r = parse_uri("moshu://projects/550e8400-e29b-41d4-a716-446655440000/chapters")
        self.assertIsNotNone(r)
        self.assertEqual(r.project_id, "550e8400-e29b-41d4-a716-446655440000")
        self.assertEqual(r.resource_type, "chapters_index")


class BuildUriTest(unittest.TestCase):
    """Verify moshu:// URI construction."""

    def test_projects(self):
        self.assertEqual(build_uri("projects"), "moshu://projects")

    def test_project_detail(self):
        self.assertEqual(build_uri("projects", "abc"), "moshu://projects/abc")

    def test_chapters(self):
        self.assertEqual(build_uri("projects", "p1", "chapters"), "moshu://projects/p1/chapters")

    def test_chapter_detail(self):
        self.assertEqual(
            build_uri("projects", "p1", "chapters", "ch1"),
            "moshu://projects/p1/chapters/ch1",
        )

    def test_roundtrip(self):
        """build_uri output should parse back to the same values."""
        uri = build_uri("projects", "myproj", "characters", "char42")
        r = parse_uri(uri)
        self.assertIsNotNone(r)
        self.assertEqual(r.project_id, "myproj")
        self.assertEqual(r.entity_id, "char42")
        self.assertEqual(r.resource_type, "character_detail")


class ListResourceUrisTest(unittest.TestCase):
    """Verify list_resource_uris returns expected URIs."""

    def test_returns_expected_count(self):
        uris = list_resource_uris("p1")
        self.assertEqual(len(uris), 7)

    def test_contains_projects_index(self):
        uris = list_resource_uris("p1")
        self.assertIn("moshu://projects", uris)

    def test_contains_project_detail(self):
        uris = list_resource_uris("p1")
        self.assertIn("moshu://projects/p1", uris)

    def test_contains_all_index_types(self):
        uris = list_resource_uris("p1")
        expected = [
            "moshu://projects/p1/chapters",
            "moshu://projects/p1/characters",
            "moshu://projects/p1/worldbuilding",
            "moshu://projects/p1/outline",
            "moshu://projects/p1/relationships",
        ]
        for e in expected:
            self.assertIn(e, uris)

    def test_all_uris_parse(self):
        """Every URI returned by list_resource_uris must parse successfully."""
        for uri in list_resource_uris("test-proj"):
            r = parse_uri(uri)
            self.assertIsNotNone(r, f"Failed to parse: {uri}")


class ResourceDescriptionTest(unittest.TestCase):
    """Verify resource descriptions exist for all types."""

    def test_all_types_have_descriptions(self):
        types = [
            "projects_index", "project_detail",
            "chapters_index", "chapter_detail",
            "characters_index", "character_detail",
            "worldbuilding_index", "worldbuilding_detail",
            "outline_index", "outline_detail",
            "relationships",
        ]
        for t in types:
            desc = get_resource_description(t)
            self.assertTrue(desc, f"Empty description for {t}")

    def test_unknown_type_returns_generic(self):
        desc = get_resource_description("unknown_type")
        self.assertIn("Moshu", desc)


class ReadResourceTest(unittest.TestCase):
    """Verify read_resource dispatches to correct readers and returns data."""

    def _mock_db(self, model_name, items):
        """Create a mock DB that returns items for a given model query."""
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.order_by.return_value = query_mock
        query_mock.filter.return_value = query_mock
        query_mock.all.return_value = items
        query_mock.first.return_value = items[0] if items else None
        db.query.return_value = query_mock
        return db

    def test_invalid_uri_returns_none(self):
        db = MagicMock()
        result = read_resource(db, "http://invalid")
        self.assertIsNone(result)

    def test_projects_index(self):
        proj = MagicMock()
        proj.id = "p1"
        proj.title = "Test"
        proj.description = "Desc"
        db = self._mock_db("Project", [proj])
        result = read_resource(db, "moshu://projects")
        self.assertIsNotNone(result)
        self.assertEqual(result.mime_type, "application/json")
        data = json.loads(result.text)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["projects"][0]["id"], "p1")

    def test_project_detail(self):
        proj = MagicMock()
        proj.id = "p1"
        proj.title = "Test"
        proj.description = "Desc"
        proj.tags = None
        proj.narrative_perspective = "third_person"
        proj.writing_style = "natural"
        proj.daily_word_goal = 6000
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = proj
        db.query.return_value = query_mock
        result = read_resource(db, "moshu://projects/p1")
        self.assertIsNotNone(result)
        data = json.loads(result.text)
        self.assertEqual(data["title"], "Test")

    def test_chapters_index(self):
        ch = MagicMock()
        ch.id = "ch1"
        ch.title = "Chapter 1"
        ch.word_count = 1000
        ch.outline_node_id = "n1"
        db = self._mock_db("Chapter", [ch])
        result = read_resource(db, "moshu://projects/p1/chapters")
        self.assertIsNotNone(result)
        data = json.loads(result.text)
        self.assertEqual(data["total"], 1)

    def test_characters_index(self):
        char = MagicMock()
        char.id = "c1"
        char.name = "Hero"
        char.role_type = "protagonist"
        db = self._mock_db("Character", [char])
        result = read_resource(db, "moshu://projects/p1/characters")
        self.assertIsNotNone(result)
        data = json.loads(result.text)
        self.assertEqual(data["characters"][0]["name"], "Hero")

    def test_worldbuilding_index(self):
        entry = MagicMock()
        entry.id = "w1"
        entry.title = "Kingdom"
        entry.dimension = "geography"
        db = self._mock_db("WorldbuildingEntry", [entry])
        result = read_resource(db, "moshu://projects/p1/worldbuilding")
        self.assertIsNotNone(result)
        data = json.loads(result.text)
        self.assertEqual(data["total"], 1)

    def test_outline_index(self):
        node = MagicMock()
        node.id = "n1"
        node.title = "Act 1"
        node.node_type = "volume"
        node.parent_id = None
        node.status = "pending"
        db = self._mock_db("OutlineNode", [node])
        result = read_resource(db, "moshu://projects/p1/outline")
        self.assertIsNotNone(result)
        data = json.loads(result.text)
        self.assertEqual(data["outline"][0]["title"], "Act 1")

    def test_relationships(self):
        rel = MagicMock()
        rel.source_id = "c1"
        rel.target_id = "c2"
        rel.relationship_type = "friend"
        rel.description = "Best friends"
        db = self._mock_db("CharacterRelationship", [rel])
        result = read_resource(db, "moshu://projects/p1/relationships")
        self.assertIsNotNone(result)
        data = json.loads(result.text)
        self.assertEqual(data["relationships"][0]["relationship_type"], "friend")

    def test_chapter_detail(self):
        ch = MagicMock()
        ch.id = "ch1"
        ch.title = "Chapter 1"
        ch.content = "Once upon a time..."
        ch.word_count = 1000
        ch.outline_node_id = None
        ch.summary = None

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            if model.__name__ == "Chapter":
                q.first.return_value = ch
            else:
                q.first.return_value = None
                q.all.return_value = []
            return q

        db = MagicMock()
        db.query.side_effect = query_side_effect
        result = read_resource(db, "moshu://projects/p1/chapters/ch1")
        self.assertIsNotNone(result)
        data = json.loads(result.text)
        self.assertEqual(data["content"], "Once upon a time...")

    def test_chapter_detail_with_linked_metadata(self):
        """Chapter detail should include summary, outline, characters, and worldbuilding."""
        ch = MagicMock()
        ch.id = "ch1"
        ch.title = "Chapter 1"
        ch.content = "Text"
        ch.word_count = 100
        ch.outline_node_id = "n1"

        # Mock summary
        summary = MagicMock()
        summary.summary_text = "A summary"
        summary.key_events = '["event1"]'
        ch.summary = summary

        # Mock outline node
        node = MagicMock()
        node.id = "n1"
        node.title = "Act 1"
        node.summary = "The beginning"
        node.node_type = "chapter"

        # Mock character link
        char_link = MagicMock()
        char_link.character_id = "c1"
        char_link.appearance_type = "出场"

        char = MagicMock()
        char.id = "c1"
        char.name = "Hero"
        char.role_type = "protagonist"

        # Mock worldbuilding link
        wb_link = MagicMock()
        wb_link.worldbuilding_entry_id = "w1"

        wb_entry = MagicMock()
        wb_entry.id = "w1"
        wb_entry.title = "Kingdom"
        wb_entry.dimension = "geography"

        # Set up query chain
        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            if model.__name__ == "Chapter":
                q.first.return_value = ch
            elif model.__name__ == "OutlineNode":
                q.first.return_value = node
            elif model.__name__ == "ChapterCharacter":
                q.all.return_value = [char_link]
            elif model.__name__ == "Character":
                q.first.return_value = char
            elif model.__name__ == "ChapterWorldbuilding":
                q.all.return_value = [wb_link]
            elif model.__name__ == "WorldbuildingEntry":
                q.first.return_value = wb_entry
            else:
                q.first.return_value = None
                q.all.return_value = []
            return q

        db = MagicMock()
        db.query.side_effect = query_side_effect

        result = read_resource(db, "moshu://projects/p1/chapters/ch1")
        self.assertIsNotNone(result)
        data = json.loads(result.text)

        # Verify linked metadata
        self.assertIn("summary", data)
        self.assertEqual(data["summary"]["text"], "A summary")
        self.assertIn("outline_node", data)
        self.assertEqual(data["outline_node"]["title"], "Act 1")
        self.assertIn("characters", data)
        self.assertEqual(data["characters"][0]["name"], "Hero")
        self.assertIn("worldbuilding", data)
        self.assertEqual(data["worldbuilding"][0]["title"], "Kingdom")

    def test_character_detail(self):
        char = MagicMock()
        char.id = "c1"
        char.name = "Hero"
        char.appearance = "Tall"
        char.personality = "Brave"
        char.background = "Orphan"
        char.abilities = '["sword"]'
        char.role_type = "protagonist"
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = char
        db.query.return_value = query_mock
        result = read_resource(db, "moshu://projects/p1/characters/c1")
        self.assertIsNotNone(result)
        data = json.loads(result.text)
        self.assertEqual(data["name"], "Hero")

    def test_not_found_returns_error_json(self):
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock
        result = read_resource(db, "moshu://projects/p1/chapters/nonexistent")
        self.assertIsNotNone(result)
        data = json.loads(result.text)
        self.assertIn("error", data)

    def test_rag_search_missing_query_returns_error(self):
        db = MagicMock()
        result = read_resource(db, "moshu://projects/p1/rag/search")
        self.assertIsNotNone(result)
        data = json.loads(result.text)
        self.assertIn("error", data)

    def test_rag_search_with_query(self):
        mock_chunk = MagicMock()
        mock_chunk.chunk_id = "chunk1"
        mock_chunk.source_type = "chapter"
        mock_chunk.source_id = "ch1"
        mock_chunk.title = "Chapter 1"
        mock_chunk.content = "Some content"
        mock_chunk.score = 0.95
        mock_chunk.reason = "keyword match"

        with patch("app.services.rag.retriever.search_chunks", return_value=[mock_chunk]), \
             patch("app.services.rag.indexer.project_has_chunks", return_value=True):
            db = MagicMock()
            result = read_resource(db, "moshu://projects/p1/rag/search?q=test&limit=5")
            self.assertIsNotNone(result)
            data = json.loads(result.text)
            self.assertEqual(data["query"], "test")
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["results"][0]["chunk_id"], "chunk1")

    def test_rag_search_uri_parses_query_params(self):
        r = parse_uri("moshu://projects/p1/rag/search?q=hello&limit=10")
        self.assertIsNotNone(r)
        self.assertEqual(r.resource_type, "rag_search")
        self.assertEqual(r.project_id, "p1")
        self.assertIsNotNone(r.query_params)
        self.assertEqual(r.query_params["q"], "hello")
        self.assertEqual(r.query_params["limit"], "10")


if __name__ == "__main__":
    unittest.main()
