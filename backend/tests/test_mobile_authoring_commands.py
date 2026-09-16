"""Offline mobile commands retain desktop validation and atomic sync effects."""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import models as _models  # noqa: F401
from app.database.models import ChapterDraft
from app.database.session import Base
from app.modules.continuity.domain.governance_lifecycle import validate_transition
from app.modules.gateway.application.contracts import SyncMutation
from app.modules.gateway.infrastructure.models import SyncEntityState
from app.modules.gateway.infrastructure.mutation_service import GatewayMutationApplier
from app.modules.story.infrastructure.chapters import SqlAlchemyChapterWorkspace
from app.modules.story.infrastructure.entities import (
    Chapter,
    Character,
    CharacterRelationship,
    Project,
)


def test_chapter_diff_matches_the_shared_android_fixtures():
    from app.services.chapter_service import diff_snapshots

    fixtures = json.loads(
        (Path(__file__).parents[2] / "contracts/fixtures/mobile-chapter-diff-v1.json").read_text(
            encoding="utf-8"
        )
    )
    for fixture in fixtures["cases"]:

        def snapshot(content):
            return SimpleNamespace(
                id="snapshot",
                chapter_id="chapter",
                content=content,
                version_number=1,
                word_count=len(content),
                trigger_type="manual_save",
                created_at=datetime(2026, 9, 16),
            )

        result = diff_snapshots(snapshot(fixture["from"]), snapshot(fixture["to"]))
        assert result["changes"] == fixture["changes"]
        assert result["total_changes"] == fixture["total_changes"]


def test_local_input_bounds_match_the_shared_desktop_schema_cases():
    from pydantic import ValidationError as SchemaError

    from app.schemas.chapter import ChapterCreate
    from app.schemas.character import CharacterAIConfigUpdate, CharacterCreate
    from app.schemas.outline import OutlineNodeCreate
    from app.schemas.project import ProjectCreate
    from app.schemas.worldbuilding import WorldbuildingEntryCreate

    schemas = {"project": ProjectCreate, "chapter": ChapterCreate, "character": CharacterCreate,
               "character_ai_config": CharacterAIConfigUpdate, "outline": OutlineNodeCreate, "world": WorldbuildingEntryCreate}
    fixtures = json.loads((Path(__file__).parents[2] / "contracts/fixtures/mobile-authoring-input-v1.json").read_text(encoding="utf-8"))
    for case in fixtures["cases"]:
        try:
            schemas[case["type"]].model_validate(case["payload"])
            valid = True
        except SchemaError:
            valid = False
        assert valid == case["valid"], case["name"]


@pytest.fixture
def workspace(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'authoring.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([Project(id="p", title="手机作品"), Project(id="other", title="其他作品")])
        db.flush()
        chapters = SqlAlchemyChapterWorkspace(db)
        first = chapters.create("p", {"title": "第一章", "content": "旧正文\n留存"}).data["id"]
        second = chapters.create("p", {"title": "第二章", "content": "第二章"}).data["id"]
        db.add_all(
            [
                Character(id="a", project_id="p", name="甲"),
                Character(id="b", project_id="p", name="乙"),
                Character(id="foreign", project_id="other", name="丙"),
            ]
        )
        db.flush()
        yield db, chapters, first, second
    engine.dispose()


def command(db, name, args, expected, identity=None):
    identity = identity or str(uuid4())
    mutation = SyncMutation(
        mutation_id=identity,
        project_id="p",
        entity_type="authoring_command",
        entity_id=identity,
        operation="upsert",
        base_revision=0,
        payload={"command": name, "arguments": args, "expected": expected},
    )
    return GatewayMutationApplier(
        db, tombstone_retention_days=90, refresh_project_manifest=lambda _: None
    ).apply(mutation, device_id=None)


def test_reorder_is_complete_guarded_idempotent_and_captures_rows(workspace):
    db, _, first, second = workspace
    identity = str(uuid4())
    args = {"chapter_ids": [second, first]}
    expected = {"chapter_ids": [first, second]}
    assert command(db, "chapter_reorder", args, expected, identity).status == "applied"
    assert db.get(Chapter, second).sort_order == 1000
    assert db.get(Chapter, first).sort_order == 2000
    assert command(db, "chapter_reorder", args, expected, identity).status == "duplicate"
    assert command(db, "chapter_reorder", expected, args, identity).status == "rejected"
    assert command(db, "chapter_reorder", args, expected).status == "rejected"
    assert command(db, "chapter_reorder", {"chapter_ids": [first]}, args).status == "rejected"
    assert db.query(SyncEntityState).filter(SyncEntityState.entity_type == "chapter").count() == 2


def test_restore_creates_new_version_invalidates_suffix_and_rejects_stale(workspace):
    db, chapters, first, second = workspace
    snapshots = chapters.snapshots("p", first)["items"]
    selected = chapters.snapshot("p", first, snapshots[0]["id"])
    chapters.save("p", first, {"content": "新正文"})
    before = chapters.detail("p", first)
    db.get(Chapter, second).cataloging_required = False
    expected = {
        "current_version": before["current_version"],
        "content_hash": sha256("新正文".encode()).hexdigest(),
    }
    args = {
        "chapter_id": first,
        "snapshot_version": selected["version_number"],
        "snapshot_content": selected["content"],
    }
    assert command(db, "chapter_restore", args, expected).status == "applied"
    assert db.get(Chapter, first).content == selected["content"]
    assert db.get(Chapter, first).current_version == before["current_version"] + 1
    assert db.get(Chapter, second).cataloging_required
    assert command(db, "chapter_restore", args, expected).status == "rejected"


def test_relationship_replace_preserves_incoming_direction_and_rejects_foreign(workspace):
    db, _, _, _ = workspace
    db.add(
        CharacterRelationship(
            id="old",
            project_id="p",
            character_a_id="b",
            character_b_id="a",
            relationship_type="friend",
            description="旧",
        )
    )
    db.flush()
    expected = {
        "relationships": [
            {"from": "b", "to": "a", "relationship_type": "friend", "description": "旧"}
        ]
    }
    bad = {
        "character_id": "a",
        "relationships": [
            {
                "id": "new",
                "from": "a",
                "to": "foreign",
                "relationship_type": "friend",
                "description": "",
            }
        ],
    }
    assert command(db, "character_relationships", bad, expected).status == "rejected"
    assert db.get(CharacterRelationship, "old") is not None
    good = {
        "character_id": "a",
        "relationships": [
            {
                "id": "new",
                "from": "b",
                "to": "a",
                "relationship_type": "mentor",
                "description": "新",
            }
        ],
    }
    assert command(db, "character_relationships", good, expected).status == "applied"
    assert db.get(CharacterRelationship, "old") is None
    assert db.get(CharacterRelationship, "new").character_a_id == "b"
    assert db.query(SyncEntityState).filter(SyncEntityState.entity_id == "old").one().is_deleted


def test_shared_relationship_boundary_rejects_duplicates_before_deleting(workspace):
    from app.core.exceptions import ValidationError
    from app.services.mobile_authoring_commands import Edge
    from app.services.persistence.character_workspace import SqlAlchemyCharacterWorkspace

    db, _, _, _ = workspace
    db.add(
        CharacterRelationship(
            id="old",
            project_id="p",
            character_a_id="a",
            character_b_id="b",
            relationship_type="friend",
        )
    )
    db.flush()
    edge = Edge(id="new", **{"from": "a", "to": "b"}, relationship_type="friend")
    with pytest.raises(ValidationError):
        SqlAlchemyCharacterWorkspace(db).replace_relationships("p", "a", [edge, edge])
    assert db.get(CharacterRelationship, "old") is not None


def test_governance_lifecycle_accepts_same_cases_as_independent_phone():
    path = (
        Path(__file__).resolve().parents[2]
        / "contracts/fixtures/mobile-governance-transitions-v1.json"
    )
    for case in json.loads(path.read_text(encoding="utf-8"))["cases"]:
        for item_type in ("foreshadowings", "narrative-debts"):
            if case["valid"]:
                validate_transition(item_type, case["from"], case["to"], case["values"])
            else:
                with pytest.raises(ValueError):
                    validate_transition(item_type, case["from"], case["to"], case["values"])


def test_imported_draft_edits_and_discard_are_guarded_and_idempotent(workspace):
    db, chapters, first, _ = workspace
    db.add(ChapterDraft(id="draft", project_id="p", status="pending", title="草稿", content="初稿"))
    db.flush()
    guard = {"content_hash": sha256("初稿".encode()).hexdigest()}
    args = {
        "draft_id": "draft",
        "title": "修订草稿",
        "content": "手机修改",
        "outline_node_id": None,
    }
    assert command(db, "chapter_draft_edit", args, guard).status == "applied"
    assert command(db, "chapter_draft_edit", args, guard).status == "rejected"
    assert db.get(ChapterDraft, "draft").content == "手机修改"
    assert chapters.detail("p", first)["content"] == "旧正文\n留存"
    guard = {"content_hash": sha256("手机修改".encode()).hexdigest()}
    identity = str(uuid4())
    args = {"draft_id": "draft", "status": "discarded"}
    assert command(db, "chapter_draft_status", args, guard, identity).status == "applied"
    assert command(db, "chapter_draft_status", args, guard, identity).status == "duplicate"
    assert db.get(ChapterDraft, "draft").status == "discarded"


def test_saved_draft_receipt_requires_the_matching_formal_chapter(workspace):
    db, chapters, first, second = workspace
    db.add(
        ChapterDraft(
            id="draft",
            project_id="p",
            status="pending",
            title="修订",
            content="候选",
            draft_kind="revision",
            target_chapter_id=first,
            base_chapter_version=1,
        )
    )
    db.flush()
    guard = {"content_hash": sha256("候选".encode()).hexdigest()}
    args = {
        "draft_id": "draft",
        "status": "saved",
        "saved_chapter_id": second,
        "saved_content_hash": sha256("第二章".encode()).hexdigest(),
    }
    assert command(db, "chapter_draft_status", args, guard).status == "rejected"
    assert db.get(ChapterDraft, "draft").status == "pending"
    chapters.save("p", first, {"content": "作者定稿"})
    args.update(saved_chapter_id=first, saved_content_hash=sha256("作者定稿".encode()).hexdigest())
    assert command(db, "chapter_draft_status", args, guard).status == "applied"
    assert db.get(ChapterDraft, "draft").saved_chapter_id == first
