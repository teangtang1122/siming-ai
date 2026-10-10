"""Retired onboarding cannot resume, and existing user models/data stay intact."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.models import APIConfig, Base, OperationRun, Project


def test_upgrade_retires_active_jobs_and_preserves_history_and_models():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("""CREATE TABLE opencode_activation_jobs (
            id TEXT PRIMARY KEY, status TEXT, phase TEXT, message TEXT,
            next_action TEXT, updated_at DATETIME, completed_at DATETIME, selected_model TEXT
        )"""))
        connection.execute(text("""INSERT INTO opencode_activation_jobs (id,status,phase,selected_model)
            VALUES ('old-active','auth_required','auth_required',NULL),
                   ('old-ready','ready','ready','vendor/selected-model')"""))
    with sessionmaker(bind=engine)() as db:
        config = APIConfig(provider="opencode_cli", default_model="vendor/selected-model",
                           api_key_encrypted="saved-key", readiness_status="ready", is_global_default=True)
        project = Project(title="保留的作品")
        active = OperationRun(source_kind="opencode_activation", source_id="old-active", title="旧任务")
        complete = OperationRun(source_kind="opencode_activation", source_id="old-ready", title="已完成",
                                status="completed", result_json={"selected_model": "vendor/selected-model"})
        unrelated = OperationRun(source_kind="cataloging", source_id="cataloging-1", title="建档任务")
        db.add_all([config, project, active, complete, unrelated])
        db.commit()
        path = Path(__file__).resolve().parents[1] / "alembic/versions/300a39_retire_free_onboarding.py"
        spec = importlib.util.spec_from_file_location("retire_free_onboarding", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        for _ in range(2):
            with Operations.context(MigrationContext.configure(db.connection())):
                migration.upgrade()
            db.commit()
        db.expire_all()
        assert active.status == "cancelled" and active.can_retry is False
        assert active.resume_url == "/getting-started"
        assert complete.status == "completed"
        assert complete.result_json == {"selected_model": "vendor/selected-model"}
        assert complete.can_retry is False
        assert unrelated.status == "running" and unrelated.can_retry is True
        assert config.readiness_status == "ready" and config.is_global_default is True
        assert config.default_model == "vendor/selected-model" and config.api_key_encrypted == "saved-key"
        assert project.title == "保留的作品"
        rows = db.execute(text("SELECT id,status,selected_model FROM opencode_activation_jobs ORDER BY id")).all()
        assert rows == [("old-active", "cancelled", None), ("old-ready", "ready", "vendor/selected-model")]
    engine.dispose()
