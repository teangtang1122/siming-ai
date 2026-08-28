"""Contract and round-trip tests for strict Siming project packages."""

from __future__ import annotations

import hashlib
import io
import json
import stat
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.bootstrap.composition import configure_application_services
from app.core.exceptions import AppException, app_exception_handler
from app.database.models import (
    AssistantConversation,
    Chapter,
    ChapterDraft,
    ChapterGovernanceReview,
    ChapterSnapshot,
    ChapterSummary,
    Character,
    CharacterAIConfig,
    CharacterAlias,
    CharacterRelationship,
    ContentSyncJob,
    NarrativeCheckpoint,
    NovelCreationArtifactVersion,
    NovelCreationMaterialImport,
    NovelCreationSession,
    NovelCreationStageRun,
    OutlineNode,
    Project,
    ProjectPackageImportReceipt,
    RagChunk,
    RagDocument,
    ScheduledTask,
    WorldbuildingEntry,
    WorldbuildingRelation,
)
from app.database.session import Base, get_db
from app.routers.project_package import router
from app.services import project_package_service as package_service
from app.services import project_package_validation as package_validation
from app.services.project_package_service import (
    ERROR_ASSET,
    ERROR_INVALID,
    ERROR_LIMIT,
    ERROR_VERSION,
    PACKAGE_EXTENSION,
    PACKAGE_FORMAT,
    PACKAGE_ID_NAMESPACE,
    PACKAGE_MEDIA_TYPE,
    ProjectPackageError,
    ProjectPackageExporter,
    ProjectPackageImporter,
    ProjectPackageValidator,
)

CHAPTER_SENTINEL = "CHAPTER_BODY_SENTINEL_56_DO_NOT_LEAK"
DRAFT_SENTINEL = "UNSAVED_DRAFT_SENTINEL_56_DO_NOT_LEAK"
SNAPSHOT_SENTINEL = "SNAPSHOT_SENTINEL_56_DO_NOT_LEAK"
SUMMARY_SENTINEL = "SUMMARY_SENTINEL_56_DO_NOT_LEAK"
MATERIAL_SENTINEL = "MATERIAL_SENTINEL_56_DO_NOT_LEAK"
RAG_SENTINEL = "RAG_RUNTIME_SENTINEL_56_DO_NOT_EXPORT"
CONVERSATION_SENTINEL = "CONVERSATION_SENTINEL_56_DO_NOT_EXPORT"
TASK_SENTINEL = "TASK_SENTINEL_56_DO_NOT_EXPORT"
MODEL_OVERRIDE_SENTINEL = "MODEL_OVERRIDE_SENTINEL_56_DO_NOT_EXPORT"


def _database(path: Path):
    engine = create_engine(
        f"sqlite:///{path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed_project(db: Session, root: Path, *, project_id: str = "source-project") -> Path:
    material_path = root / "source-material.txt"
    material_path.write_text(MATERIAL_SENTINEL, encoding="utf-8")
    material_bytes = material_path.read_bytes()
    project = Project(
        id=project_id,
        title="万象归墟",
        description="凡人以记录对抗世界遗忘。",
        tags='["东方幻想"]',
        narrative_perspective="third_person",
        custom_style_prompt="克制、清晰",
    )
    outline = OutlineNode(
        id="outline-1",
        project_id=project_id,
        node_type="chapter",
        title="第一章",
        summary="开篇结构",
        actual_summary=SUMMARY_SENTINEL,
        status="in_progress",
    )
    chapter = Chapter(
        id="chapter-1",
        project_id=project_id,
        outline_node_id=outline.id,
        title="第一章",
        content=CHAPTER_SENTINEL,
        word_count=len(CHAPTER_SENTINEL),
        current_version=2,
        cataloging_required=True,
        sort_order=1000,
    )
    outline.source_chapter_id = chapter.id
    snapshot = ChapterSnapshot(
        id="snapshot-1",
        chapter_id=chapter.id,
        version_number=1,
        content=SNAPSHOT_SENTINEL,
        word_count=len(SNAPSHOT_SENTINEL),
        trigger_type="manual",
    )
    summary = ChapterSummary(
        id="summary-1",
        chapter_id=chapter.id,
        summary_text=SUMMARY_SENTINEL,
        key_events="关键事件",
    )
    draft = ChapterDraft(
        id="draft-1",
        project_id=project_id,
        outline_node_id=outline.id,
        saved_chapter_id=None,
        status="pending",
        title="第二章草稿",
        content=DRAFT_SENTINEL,
    )
    character_a = Character(
        id="character-a",
        project_id=project_id,
        name="沈青梧",
        current_goal="守住万象阁",
        last_seen_chapter_id=chapter.id,
        last_updated_chapter_id=chapter.id,
    )
    character_b = Character(id="character-b", project_id=project_id, name="陆沉")
    voice = CharacterAIConfig(
        id="voice-a",
        character_id=character_a.id,
        tone_style="冷静",
        catchphrases="且慢",
        verbosity="concise",
        emotion_tendency="restrained",
        model_override=MODEL_OVERRIDE_SENTINEL,
        custom_system_prompt=MODEL_OVERRIDE_SENTINEL,
    )
    alias = CharacterAlias(
        id="alias-a",
        project_id=project_id,
        character_id=character_a.id,
        alias="阁主",
        source_chapter_id=chapter.id,
    )
    character_relation = CharacterRelationship(
        id="character-relation-1",
        project_id=project_id,
        character_a_id=character_a.id,
        character_b_id=character_b.id,
        relationship_type="盟友",
    )
    world_a = WorldbuildingEntry(
        id="world-a",
        project_id=project_id,
        dimension="location",
        title="万象阁",
        content="记录世间万物的楼阁。",
        first_seen_chapter_id=chapter.id,
        last_updated_chapter_id=chapter.id,
    )
    world_b = WorldbuildingEntry(
        id="world-b",
        project_id=project_id,
        dimension="faction",
        title="守录人",
        content="维护记录秩序的组织。",
    )
    world_relation = WorldbuildingRelation(
        id="world-relation-1",
        project_id=project_id,
        source_entry_id=world_a.id,
        target_entry_id=world_b.id,
        relation_type="owned_by",
    )
    creation = NovelCreationSession(
        id="creation-1",
        source_project_id=project_id,
        created_project_id=project_id,
        status="completed",
        mode="internal_llm",
        user_brief="一部长篇东方幻想小说",
        genre="东方幻想",
        schema_version=2,
        revision=9,
        draft_json={
            "form": {"brief": "一部长篇东方幻想小说", "genre": "东方幻想"},
            "stages": {"constraints": {"status": "confirmed", "data": {}}},
        },
        checkpoints_json={"constraints": [{"revision": 9}]},
    )
    creation_version = NovelCreationArtifactVersion(
        id="creation-version-1",
        session_id=creation.id,
        artifact_key="constraints",
        revision=9,
        status="confirmed",
        source="author",
        snapshot_json={"genre": "东方幻想"},
    )
    creation_run = NovelCreationStageRun(
        id="creation-run-1",
        session_id=creation.id,
        stage="constraints",
        status="completed",
        result_json={"runtime": "must not export"},
    )
    material = NovelCreationMaterialImport(
        id="material-1",
        session_id=creation.id,
        filename="素材.txt",
        stored_path=str(material_path),
        media_type="txt",
        file_sha256=hashlib.sha256(material_bytes).hexdigest(),
        size_bytes=len(material_bytes),
        status="completed",
        input_revision=9,
        text_length=len(MATERIAL_SENTINEL),
        chunk_count=1,
        processed_chunks=1,
    )
    checkpoint = NarrativeCheckpoint(
        id="checkpoint-1",
        project_id=project_id,
        chapter_id=chapter.id,
        chapter_snapshot_id=snapshot.id,
        sequence=1,
        label="第一章完成",
        state_json={"chapter_id": chapter.id, "author_applied": True},
    )
    review = ChapterGovernanceReview(
        id="review-1",
        project_id=project_id,
        chapter_id=chapter.id,
        chapter_version=2,
        status="verified",
        source="author",
        findings_count=0,
        evidence="作者已确认",
    )
    rag_document = RagDocument(
        id="rag-document-1",
        project_id=project_id,
        source_type="chapter",
        source_id=chapter.id,
        content_hash="a" * 64,
        chunk_count=1,
    )
    rag_chunk = RagChunk(
        id="rag-chunk-1",
        document_id=rag_document.id,
        project_id=project_id,
        source_type="chapter",
        source_id=chapter.id,
        chunk_index=0,
        title="运行时索引",
        content=RAG_SENTINEL,
    )
    conversation = AssistantConversation(
        id="conversation-1",
        project_id=project_id,
        title=CONVERSATION_SENTINEL,
    )
    task = ScheduledTask(
        id="task-1",
        project_id=project_id,
        name="到期任务",
        prompt=TASK_SENTINEL,
        interval_minutes=60,
        status="active",
        next_run_at=datetime.utcnow() - timedelta(minutes=1),
    )
    db.add_all(
        [
            project,
            outline,
            chapter,
            snapshot,
            summary,
            draft,
            character_a,
            character_b,
            voice,
            alias,
            character_relation,
            world_a,
            world_b,
            world_relation,
            creation,
            creation_version,
            creation_run,
            material,
            checkpoint,
            review,
            rag_document,
            rag_chunk,
            conversation,
            task,
        ]
    )
    db.commit()
    return material_path


def _export_bytes(db: Session, project_id: str, profile: str) -> bytes:
    exported = ProjectPackageExporter(db, project_id, profile).build()
    try:
        return exported.path.read_bytes()
    finally:
        exported.cleanup()


def _write_package(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


def _archive_entries(payload: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def _repack(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return output.getvalue()


def _update_manifest_entry(entries: dict[str, bytes], path: str) -> None:
    manifest = json.loads(entries["manifest.json"])
    for entry in manifest["entries"]:
        if entry["path"] == path:
            entry["size"] = len(entries[path])
            entry["sha256"] = hashlib.sha256(entries[path]).hexdigest()
            break
    entries["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode()


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    content = tmp_path / "content"
    content.mkdir()
    monkeypatch.setattr(package_service, "content_root", lambda: content)
    monkeypatch.setattr(package_validation, "content_root", lambda: content)
    engine, factory = _database(tmp_path / "source.db")
    db = factory()
    _seed_project(db, tmp_path)
    try:
        yield db, factory, content
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_shared_android_protocol_fixture_matches_backend_contract() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "fixtures"
        / "project-package-v1-interop.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture["format"] == PACKAGE_FORMAT
    assert fixture["format_version"] == package_service.PACKAGE_FORMAT_VERSION
    assert uuid.UUID(fixture["uuid_namespace"]) == PACKAGE_ID_NAMESPACE
    expected_id = uuid.uuid5(
        PACKAGE_ID_NAMESPACE,
        f"{fixture['idempotency_key']}:project:{fixture['source_project_id']}",
    )
    assert str(expected_id) == fixture["expected_project_id"]
    assert fixture["structure_paths"] == [
        spec.path for spec in package_service.COLLECTION_SPECS if "structure" in spec.profiles
    ]


def test_full_and_structure_packages_have_strict_author_data_boundaries(seeded):
    db, _factory, _content = seeded
    full = _export_bytes(db, "source-project", "full")
    structure = _export_bytes(db, "source-project", "structure")

    full_entries = _archive_entries(full)
    with zipfile.ZipFile(io.BytesIO(full), "r") as archive:
        assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
    manifest = json.loads(full_entries["manifest.json"])
    assert manifest["format"] == PACKAGE_FORMAT
    assert manifest["format_version"] == 1
    assert manifest["profile"] == "full"
    assert any(name.startswith("assets/materials/") for name in full_entries)
    full_plaintext = b"\n".join(full_entries.values()).decode("utf-8", errors="ignore")
    assert CHAPTER_SENTINEL in full_plaintext
    assert DRAFT_SENTINEL in full_plaintext
    assert MATERIAL_SENTINEL in full_plaintext
    for excluded in (RAG_SENTINEL, CONVERSATION_SENTINEL, TASK_SENTINEL, MODEL_OVERRIDE_SENTINEL):
        assert excluded not in full_plaintext

    structure_entries = _archive_entries(structure)
    structure_plaintext = b"\n".join(structure_entries.values()).decode("utf-8", errors="ignore")
    for excluded in (
        CHAPTER_SENTINEL,
        DRAFT_SENTINEL,
        SNAPSHOT_SENTINEL,
        SUMMARY_SENTINEL,
        MATERIAL_SENTINEL,
        RAG_SENTINEL,
        CONVERSATION_SENTINEL,
        TASK_SENTINEL,
        MODEL_OVERRIDE_SENTINEL,
    ):
        assert excluded not in structure_plaintext
    assert not any(name.startswith("assets/") for name in structure_entries)
    assert "data/chapters.jsonl" not in structure_entries
    assert "data/chapter_drafts.jsonl" not in structure_entries


def test_full_roundtrip_cross_database_restores_author_data_and_rebuilds_indexes(
    seeded,
    tmp_path: Path,
):
    source_db, _factory, content = seeded
    payload = _export_bytes(source_db, "source-project", "full")
    source_path = _write_package(tmp_path / f"book{PACKAGE_EXTENSION}", payload)

    destination_engine, destination_factory = _database(tmp_path / "destination.db")
    destination = destination_factory()
    key = uuid.uuid4()
    validated = ProjectPackageValidator(source_path).validate()
    try:
        outcome = ProjectPackageImporter(
            destination,
            validated,
            idempotency_key=key,
            new_title="导入副本",
        ).restore()
        destination.commit()
        project_id = outcome.result["project_id"]
        assert outcome.result["project_title"] == "导入副本"
        assert destination.get(Project, project_id) is not None
        chapter = destination.query(Chapter).filter_by(project_id=project_id).one()
        assert chapter.content == CHAPTER_SENTINEL
        assert chapter.cataloging_required is False
        assert chapter.id == str(uuid.uuid5(PACKAGE_ID_NAMESPACE, f"{key}:chapters:chapter-1"))
        assert destination.query(ChapterSnapshot).one().content == SNAPSHOT_SENTINEL
        assert destination.query(ChapterSummary).one().summary_text == SUMMARY_SENTINEL
        draft = destination.query(ChapterDraft).one()
        assert draft.content == DRAFT_SENTINEL
        assert draft.saved_chapter_id is None
        assert draft.status == "pending"
        assert destination.query(CharacterRelationship).count() == 1
        assert destination.query(WorldbuildingRelation).count() == 1
        assert destination.query(NovelCreationArtifactVersion).count() == 1
        assert destination.query(NarrativeCheckpoint).count() == 1
        voice = destination.query(CharacterAIConfig).one()
        assert voice.tone_style == "冷静"
        assert voice.model_override is None
        assert voice.custom_system_prompt is None
        material = destination.query(NovelCreationMaterialImport).one()
        restored_path = Path(material.stored_path)
        assert restored_path.is_file()
        assert restored_path.read_text(encoding="utf-8") == MATERIAL_SENTINEL
        assert content in restored_path.parents
        assert destination.query(RagDocument).filter_by(project_id=project_id).count() > 0
        assert destination.query(AssistantConversation).count() == 0
        assert destination.query(ScheduledTask).count() == 0
        assert destination.query(NovelCreationStageRun).count() == 0
        assert destination.query(ProjectPackageImportReceipt).count() == 1
    finally:
        validated.cleanup()
        destination.close()
        Base.metadata.drop_all(bind=destination_engine)
        destination_engine.dispose()


def _api_app(factory) -> FastAPI:
    configure_application_services()
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(router, prefix="/api/v1")

    def override_db():
        db = factory()
        db.info["siming_skip_content_sync_dispatch"] = True
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    return app


def test_real_fastapi_routes_stream_export_and_idempotently_import(seeded):
    db, factory, _content = seeded
    key = str(uuid.uuid4())
    with TestClient(_api_app(factory)) as client:
        exported = client.post(
            "/api/v1/projects/source-project/project-package/export?profile=full"
        )
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith(PACKAGE_MEDIA_TYPE)
        assert exported.content.startswith(b"PK")
        files = {
            "file": (f"book{PACKAGE_EXTENSION}", exported.content, PACKAGE_MEDIA_TYPE),
        }
        first = client.post(
            "/api/v1/projects/project-package/import",
            files=files,
            data={"new_title": "API 导入副本"},
            headers={"Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        project_id = first.json()["data"]["project_id"]
        replay = client.post(
            "/api/v1/projects/project-package/import",
            files=files,
            data={"new_title": "API 导入副本"},
            headers={"Idempotency-Key": key},
        )
        assert replay.status_code == 200
        assert replay.json()["data"]["project_id"] == project_id
        assert replay.json()["data"]["replayed"] is True
        conflict = client.post(
            "/api/v1/projects/project-package/import",
            files=files,
            data={"new_title": "不同标题"},
            headers={"Idempotency-Key": key},
        )
        assert conflict.status_code == 409
        wrong_entry = client.post(
            "/api/v1/projects/project-package/import",
            files={"file": ("novel.txt", b"plain text", "text/plain")},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert wrong_entry.status_code == 415

    db.expire_all()
    assert db.query(Project).filter(Project.title == "API 导入副本").count() == 1
    assert db.query(ScheduledTask).count() == 1
    assert db.query(ScheduledTask).filter(ScheduledTask.project_id == project_id).count() == 0
    sync = db.query(ContentSyncJob).filter_by(project_id=project_id).one()
    assert sync.target == "project"
    assert sync.source == "project_package_import"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("unknown_manifest_field", ERROR_INVALID),
        ("unsupported_version", ERROR_VERSION),
        ("hash_mismatch", ERROR_INVALID),
        ("zip_slip", ERROR_INVALID),
        ("unknown_entry", ERROR_INVALID),
        ("compression_bomb", ERROR_LIMIT),
        ("symlink", ERROR_INVALID),
    ],
)
def test_validator_rejects_malformed_or_unsafe_archives(
    seeded,
    tmp_path: Path,
    mutation: str,
    expected_code: int,
):
    db, _factory, _content = seeded
    entries = _archive_entries(_export_bytes(db, "source-project", "structure"))
    if mutation == "unknown_manifest_field":
        manifest = json.loads(entries["manifest.json"])
        manifest["unexpected"] = True
        entries["manifest.json"] = json.dumps(manifest).encode()
        payload = _repack(entries)
    elif mutation == "unsupported_version":
        manifest = json.loads(entries["manifest.json"])
        manifest["format_version"] = 999
        entries["manifest.json"] = json.dumps(manifest).encode()
        payload = _repack(entries)
    elif mutation == "hash_mismatch":
        entries["data/project.jsonl"] += b" "
        payload = _repack(entries)
    elif mutation == "zip_slip":
        entries["../escape.txt"] = b"escape"
        payload = _repack(entries)
    elif mutation == "unknown_entry":
        entries["unknown.bin"] = b"unknown"
        payload = _repack(entries)
    elif mutation == "compression_bomb":
        path = "data/character_aliases.jsonl"
        entries[path] = b"0" * (2 * 1024 * 1024)
        _update_manifest_entry(entries, path)
        payload = _repack(entries)
    else:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, value in entries.items():
                archive.writestr(name, value)
            link = zipfile.ZipInfo("assets/materials/fake/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, b"target")
        payload = output.getvalue()

    path = _write_package(tmp_path / f"unsafe-{mutation}{PACKAGE_EXTENSION}", payload)
    with pytest.raises(ProjectPackageError) as raised:
        ProjectPackageValidator(path).validate()
    assert raised.value.code == expected_code


def test_full_export_lists_missing_original_materials(seeded):
    db, _factory, _content = seeded
    material = db.query(NovelCreationMaterialImport).one()
    second = NovelCreationMaterialImport(
        id="material-missing-2",
        session_id=material.session_id,
        filename="另一份缺失素材.md",
        stored_path=str(Path(material.stored_path).with_name("missing-material.md")),
        media_type="text/markdown",
        file_sha256="0" * 64,
        size_bytes=10,
        status="completed",
        input_revision=material.input_revision,
        text_length=10,
        chunk_count=0,
        processed_chunks=0,
    )
    db.add(second)
    db.commit()
    Path(material.stored_path).unlink()
    with pytest.raises(ProjectPackageError) as raised:
        ProjectPackageExporter(db, "source-project", "full").build()
    assert raised.value.code == ERROR_ASSET
    assert material.filename in raised.value.message
    assert second.filename in raised.value.message
    assert _export_bytes(db, "source-project", "structure")


def test_import_failure_rolls_back_database_receipt_and_material_directory(
    seeded,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    db, _factory, _content = seeded
    payload = _export_bytes(db, "source-project", "full")
    path = _write_package(tmp_path / f"rollback{PACKAGE_EXTENSION}", payload)
    validated = ProjectPackageValidator(path).validate()
    importer = ProjectPackageImporter(db, validated, idempotency_key=uuid.uuid4())
    monkeypatch.setattr(
        package_service,
        "reindex_project",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced index failure")),
    )
    try:
        with pytest.raises(RuntimeError, match="forced index failure"):
            importer.restore()
        db.rollback()
        importer.cleanup_after_failure()
        assert db.query(Project).count() == 1
        assert db.query(ProjectPackageImportReceipt).count() == 0
        assert all(not path.exists() for path in importer.moved_asset_directories)
    finally:
        validated.cleanup()
