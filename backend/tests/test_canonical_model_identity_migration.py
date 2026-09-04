"""Upgrade coverage for legacy DeepSeek identities persisted by older builds."""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from alembic import command
from app.database.bootstrap import alembic_config, bootstrap_database
from app.database.models import (
    APIConfig,
    AssistantConversation,
    ModelContextProfile,
    ModelTaskSetting,
    Project,
)
from app.database.session import create_session_engine

_PRE_CANONICAL_REVISION = "300a33_legacy_message_integrity"
_HEAD = "300a36_outline_projection_identity"


def test_upgrade_canonicalizes_legacy_deepseek_bindings_without_overwriting_profile(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy-deepseek.db"
    url = f"sqlite:///{database_path.as_posix()}"
    engine = create_session_engine(url)
    try:
        initialized = bootstrap_database(engine, database_url=url)
        assert initialized.schema_revision == _HEAD
        config = alembic_config(url)
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, _PRE_CANONICAL_REVISION)

        with Session(engine) as session:
            project = Project(title="Legacy DeepSeek project")
            session.add(project)
            session.flush()
            session.add_all(
                [
                    APIConfig(
                        provider="deepseek",
                        api_key_encrypted="encrypted-placeholder",
                        default_model="deepseek-v3",
                    ),
                    ModelTaskSetting(
                        task_type="assistant",
                        provider="deepseek",
                        model_name="deepseek-v3",
                    ),
                    ModelContextProfile(
                        provider="deepseek",
                        model_name="deepseek-v3",
                        context_window_tokens=128_000,
                        max_output_tokens=8_000,
                        safety_margin_tokens=512,
                    ),
                    ModelContextProfile(
                        provider="deepseek",
                        model_name="deepseek-v4-flash",
                        context_window_tokens=1_000_000,
                        max_output_tokens=384_000,
                        safety_margin_tokens=512,
                    ),
                    AssistantConversation(
                        project_id=project.id,
                        title="Legacy assistant",
                        scope="project",
                        model="deepseek:deepseek-v3",
                    ),
                ]
            )
            session.commit()

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, _HEAD)

        with Session(engine) as session:
            api_config = session.scalars(select(APIConfig)).one()
            task_setting = session.scalars(select(ModelTaskSetting)).one()
            conversation = session.scalars(select(AssistantConversation)).one()
            profiles = session.scalars(select(ModelContextProfile)).all()

            assert api_config.default_model == "deepseek-v4-flash"
            assert task_setting.model_name == "deepseek-v4-flash"
            assert conversation.model == "deepseek:deepseek-v4-flash"
            assert len(profiles) == 1
            assert profiles[0].model_name == "deepseek-v4-flash"
            assert profiles[0].context_window_tokens == 1_000_000
            assert profiles[0].max_output_tokens == 384_000
    finally:
        engine.dispose()
