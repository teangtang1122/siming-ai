"""Tests for old user data compatibility.

Ensures users who ran older exe builds can upgrade without losing data
or hitting missing-column errors.
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class OldDataCompatibilityTest(unittest.TestCase):
    """Verify new tables and columns don't break old databases."""

    def test_new_models_importable(self):
        """All new models should be importable without errors."""
        from app.database.models import (
            PublicPromptPack,
            MethodCard,
            NovelCreationSession,
            AgentRun,
            AgentRunEvent,
            ExternalAgentSettings,
        )
        self.assertIsNotNone(PublicPromptPack)
        self.assertIsNotNone(MethodCard)
        self.assertIsNotNone(NovelCreationSession)
        self.assertIsNotNone(AgentRun)
        self.assertIsNotNone(AgentRunEvent)
        self.assertIsNotNone(ExternalAgentSettings)

    def test_new_models_have_tablenames(self):
        """All new models should have valid table names."""
        from app.database.models import (
            PublicPromptPack,
            MethodCard,
            NovelCreationSession,
            AgentRun,
            AgentRunEvent,
            ExternalAgentSettings,
        )
        self.assertEqual(PublicPromptPack.__tablename__, "public_prompt_packs")
        self.assertEqual(MethodCard.__tablename__, "method_cards")
        self.assertEqual(NovelCreationSession.__tablename__, "novel_creation_sessions")
        self.assertEqual(AgentRun.__tablename__, "agent_runs")
        self.assertEqual(AgentRunEvent.__tablename__, "agent_run_events")
        self.assertEqual(ExternalAgentSettings.__tablename__, "external_agent_settings")

    def test_existing_models_unchanged(self):
        """Existing models should still be importable and have correct table names."""
        from app.database.models import (
            Project, Chapter, Character, WorldbuildingEntry,
            OutlineNode, CharacterRelationship,
        )
        self.assertEqual(Project.__tablename__, "projects")
        self.assertEqual(Chapter.__tablename__, "chapters")
        self.assertEqual(Character.__tablename__, "characters")
        self.assertEqual(WorldbuildingEntry.__tablename__, "worldbuilding_entries")
        self.assertEqual(OutlineNode.__tablename__, "outline_nodes")
        self.assertEqual(CharacterRelationship.__tablename__, "character_relationships")

    def test_project_has_new_relationships(self):
        """Project model should have relationships to new tables."""
        from app.database.models import Project
        # These relationships were added
        self.assertTrue(hasattr(Project, 'agent_runs'))
        self.assertTrue(hasattr(Project, 'external_agent_settings'))

    def test_new_schemas_importable(self):
        """All new schemas should be importable."""
        from app.schemas.prompt_pack import PublicPromptPackRead, PublicPromptPackCreate
        from app.schemas.novel_creation import NovelCreationSessionRead, NovelCreationSessionCreate
        from app.schemas.agent_run import AgentRunRead, AgentRunCreate
        from app.schemas.external_agent_settings import ExternalAgentSettingsRead, ExternalAgentSettingsUpdate

        self.assertIsNotNone(PublicPromptPackRead)
        self.assertIsNotNone(NovelCreationSessionRead)
        self.assertIsNotNone(AgentRunRead)
        self.assertIsNotNone(ExternalAgentSettingsRead)

    def test_new_tools_importable(self):
        """All new tool modules should be importable."""
        from app.services.workspace.tools.external_writing import (
            prepare_external_writing_context,
            save_external_chapter_draft,
            get_external_chapter_draft,
            record_external_quality_review,
        )
        from app.services.workspace.tools.external_story_updates import apply_external_story_updates
        from app.services.workspace.tools.novel_creation import (
            start_novel_creation_session,
            draft_novel_blueprint,
            review_novel_blueprint,
            apply_novel_blueprint,
        )
        from app.services.workspace.tools.prompt_packs import (
            list_prompt_packs,
            get_prompt_pack,
            get_tool_playbook,
            get_quality_rubric,
        )

        self.assertTrue(callable(prepare_external_writing_context))
        self.assertTrue(callable(save_external_chapter_draft))
        self.assertTrue(callable(apply_external_story_updates))
        self.assertTrue(callable(start_novel_creation_session))
        self.assertTrue(callable(apply_novel_blueprint))
        self.assertTrue(callable(list_prompt_packs))

    def test_prompt_pack_seed_importable(self):
        """Prompt pack seed module should be importable."""
        from app.services.prompt_packs.seed import BUILTIN_PACKS, seed_builtin_packs
        self.assertEqual(len(BUILTIN_PACKS), 13)
        self.assertTrue(callable(seed_builtin_packs))

    def test_seed_refreshes_existing_builtin_prompt_pack(self):
        """Existing built-in prompt packs in old user DBs should receive updated content."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.database.models import Base, PublicPromptPack
        from app.services.prompt_packs.seed import seed_builtin_packs

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            db.add(PublicPromptPack(
                pack_id="cataloging_external_no_api",
                version="0.0.1",
                scope="cataloging",
                title="Old External Cataloging",
                system_prompt="old prompt",
                enabled=True,
                is_builtin=True,
            ))
            db.commit()

            seed_builtin_packs(db)
            db.commit()

            pack = db.query(PublicPromptPack).filter(
                PublicPromptPack.pack_id == "cataloging_external_no_api",
            ).first()
            self.assertEqual(pack.version, "1.0.1")
            self.assertIn("中文小说必须用中文建档", pack.system_prompt)
            self.assertIn("不要改成英文或拼音", pack.system_prompt)
            self.assertTrue(pack.enabled)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
