"""Tests for project package export / import (approved-book project snapshot).

Covers the regression that previously made project export crash on
``NovelCreationSession.project_id`` and verifies a full export → import
roundtrip keeps outline / settings / creation-brief data intact with fresh
IDs in a brand-new project.
"""

from __future__ import annotations

import io
import json
import zipfile

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.models import (
    Character,
    NovelCreationArtifactVersion,
    NovelCreationImportChunk,
    NovelCreationMaterialImport,
    NovelCreationSession,
    NovelCreationStageRun,
    OutlineNode,
    Project,
    WorldbuildingEntry,
)
from app.database.session import Base
from app.routers.project_package import _strip_chapter_entries
from app.services.project_backup_service import (
    ProjectBackupBuilder,
    restore_project_backup,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return engine, Session(engine)


def _seed_project(db: Session, *, project_id: str = "old-project") -> None:
    """Create a small but representative project with creation data."""
    project = Project(
        id=project_id,
        title="万象归墟",
        description="凡人以记录对抗世界遗忘。",
        tags='["东方幻想"]',
    )
    creation = NovelCreationSession(
        id="old-creation",
        created_project_id=project.id,
        status="completed",
        revision=9,
        draft_json={
            "form": {
                "brief": "一部长篇东方幻想小说",
                "genre": "东方幻想",
                "target_words": 2_500_000,
                "target_chapters": 1000,
            },
            "selected_concept_id": "concept-1",
            "stages": {
                "constraints": {"status": "confirmed", "data": {}},
                "concepts": {"status": "confirmed", "data": {"options": [{"id": "concept-1"}]}},
                "characters": {"status": "generated", "data": {}},
            },
        },
    )
    outline = OutlineNode(
        id="old-outline",
        project_id=project.id,
        node_type="volume",
        title="第一卷",
        summary="开篇",
        status="in_progress",
    )
    character = Character(id="old-character", project_id=project.id, name="沈青梧")
    world = WorldbuildingEntry(
        id="old-world",
        project_id=project.id,
        dimension="location",
        title="万象阁",
        content="记录世间万物的楼阁。",
    )
    run = NovelCreationStageRun(
        id="old-run",
        session_id=creation.id,
        stage="concepts",
        status="completed",
        result_json={"ok": True},
    )
    version = NovelCreationArtifactVersion(
        id="old-version",
        session_id=creation.id,
        artifact_key="concepts",
        revision=1,
        snapshot_json={"options": [{"id": "concept-1"}]},
    )
    material_import = NovelCreationMaterialImport(
        id="old-import",
        session_id=creation.id,
        filename="素材.txt",
        stored_path="/tmp/素材.txt",
        file_sha256="a" * 64,
        size_bytes=1024,
        input_revision=9,
    )
    chunk = NovelCreationImportChunk(
        id="old-chunk",
        import_run_id=material_import.id,
        chunk_index=0,
        char_start=0,
        char_end=10,
        content_hash="b" * 64,
        text="设定片段",
    )
    db.add_all(
        [project, creation, outline, character, world, run, version, material_import, chunk]
    )
    db.commit()


def test_build_archive_with_creation_data_does_not_crash():
    """Regression: export must not reference the non-existent project_id column."""
    engine, db = _db()
    try:
        _seed_project(db)
        buf = ProjectBackupBuilder(db, "old-project").build_archive()
        assert buf.getvalue()
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_export_import_roundtrip_keeps_project_intact():
    engine, db = _db()
    try:
        _seed_project(db)
        archive = ProjectBackupBuilder(db, "old-project").build_archive().getvalue()

        engine2, db2 = _db()
        try:
            result = restore_project_backup(db2, archive)
            db2.commit()

            new_project_id = result["project_id"]
            assert result["project_title"] == "万象归墟"
            assert new_project_id != "old-project"

            # Project settings restored.
            project = db2.get(Project, new_project_id)
            assert project is not None
            assert project.title == "万象归墟"
            assert "东方幻想" in (project.tags or "")

            # Outline / characters / worldbuilding restored under the new project.
            outline_rows = (
                db2.query(OutlineNode).filter(OutlineNode.project_id == new_project_id).all()
            )
            assert len(outline_rows) == 1
            assert outline_rows[0].title == "第一卷"
            assert outline_rows[0].id != "old-outline"

            chars = db2.query(Character).filter(Character.project_id == new_project_id).all()
            assert len(chars) == 1
            assert chars[0].name == "沈青梧"
            assert chars[0].id != "old-character"

            worlds = (
                db2.query(WorldbuildingEntry)
                .filter(WorldbuildingEntry.project_id == new_project_id)
                .all()
            )
            assert len(worlds) == 1
            assert worlds[0].title == "万象阁"
            assert worlds[0].id != "old-world"

            # Creation brief is authoritative and re-linked to the new project.
            creation = (
                db2.query(NovelCreationSession)
                .filter(NovelCreationSession.created_project_id == new_project_id)
                .first()
            )
            assert creation is not None
            assert creation.id != "old-creation"
            assert creation.draft_json["form"]["genre"] == "东方幻想"
            assert creation.revision == 9

            # Runs / versions / material imports follow their parent rows.
            run = db2.query(NovelCreationStageRun).filter(
                NovelCreationStageRun.session_id == creation.id
            ).first()
            assert run is not None
            assert run.id != "old-run"

            version = db2.query(NovelCreationArtifactVersion).filter(
                NovelCreationArtifactVersion.session_id == creation.id
            ).first()
            assert version is not None
            assert version.id != "old-version"
            assert version.artifact_key == "concepts"
            assert version.snapshot_json["options"][0]["id"] == "concept-1"

            material = db2.query(NovelCreationMaterialImport).filter(
                NovelCreationMaterialImport.session_id == creation.id
            ).first()
            assert material is not None
            assert material.id != "old-import"

            chunk = db2.query(NovelCreationImportChunk).filter(
                NovelCreationImportChunk.import_run_id == material.id
            ).first()
            assert chunk is not None
            assert chunk.text == "设定片段"

            # The imported project is not the source project.
            assert db2.get(Project, "old-project") is None
        finally:
            db2.close()
            Base.metadata.drop_all(bind=engine2)
            engine2.dispose()
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_strip_chapter_entries_produces_valid_structure_only_package():
    engine, db = _db()
    try:
        _seed_project(db)
        buf = ProjectBackupBuilder(db, "old-project").build_archive()
        stripped = _strip_chapter_entries(buf)

        with zipfile.ZipFile(io.BytesIO(stripped.getvalue()), "r") as zf:
            names = set(zf.namelist())
            assert "chapters.json" not in names
            assert "outline.json" in names
            assert "characters.json" in names
            assert "worldbuilding.json" in names
            assert "novel_creation_sessions.json" in names
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            assert manifest["content"]["chapters"] == 0

        # The stripped package must still be restorable.
        engine2, db2 = _db()
        try:
            result = restore_project_backup(db2, stripped.getvalue())
            assert result["counts"]["chapters"] == 0
            assert result["counts"]["outline_nodes"] == 1
            assert result["counts"]["characters"] == 1
        finally:
            db2.close()
            Base.metadata.drop_all(bind=engine2)
            engine2.dispose()
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
