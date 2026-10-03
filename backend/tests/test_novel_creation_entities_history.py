"""Objectized creation entities and full artifact history."""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.novel_creation_entities import (
    get_creation_entity,
    list_creation_entities,
)
from app.services.novel_creation_actions import delete_creation_entity, patch_creation_entity, restore_artifact_version
from app.services.novel_creation_versions import (
    artifact_version_diff,
    list_artifact_versions,
)
from app.services.novel_creation_workspace import (
    patch_creation_artifact,
    set_creation_artifact_locks,
)
from tests.test_novel_creation_workspace_v2 import _db, _ready_session


def test_artifact_history_is_immutable_and_not_limited_to_three_checkpoints():
    db = _db()
    session = _ready_session(db)
    for index in range(6):
        patch_creation_artifact(session, "macro_outline", [{
            "path": "/volumes/0/title",
            "action": "replace",
            "value": f"第{index + 1}版卷名",
        }], source="assistant")
        db.commit()

    history = list_artifact_versions(
        db,
        session_id=session.id,
        artifact="macro_outline",
        limit=100,
    )
    assert len(history) >= 7
    assert len(session.checkpoints_json["macro_outline"]) == 3
    assert history[0].snapshot_json["volumes"][0]["title"] == "第6版卷名"
    assert len({item.id for item in history}) == len(history)


def test_version_diff_and_restore_keep_the_newer_state_in_history():
    db = _db()
    session = _ready_session(db)
    original = deepcopy(session.draft_json["stages"]["macro_outline"]["data"])
    patch_creation_artifact(session, "macro_outline", [{
        "path": "/volumes/0/title",
        "action": "replace",
        "value": "修改后的卷名",
    }], source="assistant")
    db.commit()
    history = list_artifact_versions(db, session_id=session.id, artifact="macro_outline")
    latest = history[0]
    baseline = next(item for item in history if item.snapshot_json == original)

    diff = artifact_version_diff(db, latest, against_version_id=baseline.id)
    assert diff["change_count"] >= 1
    assert any(item["path"] == "/volumes/0/title" for item in diff["changes"])

    before_restore_revision = int(session.revision or 0)
    result = restore_artifact_version(
        session,
        baseline,
        expected_revision=before_restore_revision,
    )
    db.commit()
    assert result["artifact"]["data"] == original
    restored_history = list_artifact_versions(db, session_id=session.id, artifact="macro_outline")
    assert restored_history[0].restored_from_version_id == baseline.id
    assert any(item.snapshot_json["volumes"][0]["title"] == "修改后的卷名" for item in restored_history)


def test_saved_artifacts_project_to_independent_entities_with_stable_ids():
    db = _db()
    session = _ready_session(db)
    entities = list_creation_entities(session)
    types = {item["entity_type"] for item in entities}
    assert {"character", "relationship", "location", "faction", "volume", "chapter_outline", "scene_outline"} <= types

    protagonist = next(item for item in entities if item["entity_type"] == "character" and item["data"]["name"] == "林七")
    entity_id = protagonist["id"]
    patch_creation_entity(
        session,
        get_creation_entity(db, entity_id),
        [{"path": "/goal", "action": "replace", "value": "救回母亲并公开病毒真相"}],
        expected_revision=int(session.revision or 0),
        source="assistant",
    )
    db.commit()
    refreshed = get_creation_entity(db, entity_id)
    assert refreshed.id == entity_id
    assert refreshed.data_json["goal"] == "救回母亲并公开病毒真相"
    assert session.draft_json["stages"]["characters"]["data"]["characters"][1]["name"] == "周渡"
    assert session.draft_json["stages"]["macro_outline"]["status"] == "stale"


def test_entity_delete_is_soft_and_locked_entity_patch_is_rejected():
    db = _db()
    session = _ready_session(db)
    entities = list_creation_entities(session)
    faction_data = next(item for item in entities if item["entity_type"] == "faction")
    faction = get_creation_entity(db, faction_data["id"])
    delete_creation_entity(
        session,
        faction,
        expected_revision=int(session.revision or 0),
    )
    db.commit()
    assert get_creation_entity(db, faction.id).status == "deleted"
    assert faction.id not in {item["id"] for item in list_creation_entities(session)}
    assert faction.id in {item["id"] for item in list_creation_entities(session, include_deleted=True)}

    protagonist_data = next(item for item in list_creation_entities(session) if item["entity_type"] == "character")
    protagonist = get_creation_entity(db, protagonist_data["id"])
    set_creation_artifact_locks(session, "characters", ["/characters/0/goal"], locked=True)
    db.commit()
    with pytest.raises(ValueError, match="字段已锁定"):
        patch_creation_entity(
            session,
            protagonist,
            [{"path": "/goal", "action": "replace", "value": "不能覆盖"}],
            expected_revision=int(session.revision or 0),
        )
