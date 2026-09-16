"""Phone plans use the PC applier, with real transactions and no model call."""

import copy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import models as m
from app.modules.continuity.domain.mobile_cataloging import MOBILE_CATALOGING_GUARD_FIELDS
from app.schemas.cataloging import MobileCatalogingCommit
from app.services.cataloging.mobile_commit import commit_mobile_cataloging
from app.services.gateway_legacy_replication import project_snapshots

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2] / "contracts/fixtures/mobile-cataloging-v1.json"
    ).read_text(encoding="utf-8")
)


@pytest.fixture
def archive(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'mobile.db').as_posix()}")
    m.Base.metadata.create_all(engine)
    with Session(engine) as db:
        for model, values in FIXTURE["seed"]:
            db.add(getattr(m, model)(**values))
            db.flush()
        db.commit()
        yield db
    engine.dispose()


def request(db):
    ids = FIXTURE["ids"]
    guards = []
    for spec, row, payload in project_snapshots(db, ids["project"]):
        if fields := MOBILE_CATALOGING_GUARD_FIELDS.get(spec.record_type):
            if spec.record_type == "character":
                from app.services.cataloging.snapshots import character_snapshot

                payload["ai_config"] = character_snapshot(row)["ai_config"]
            guards.append(
                dict(
                    entity_type=spec.entity_type,
                    record_type=spec.record_type,
                    id=row.id,
                    fields={key: payload.get(key) for key in fields},
                )
            )
    return MobileCatalogingCommit(
        request_id=f"local-cat-{uuid4()}",
        chapter_id=ids["chapter"],
        chapter_version=1,
        content_sha256=hashlib.sha256(
            db.get(m.Chapter, ids["chapter"]).content.encode()
        ).hexdigest(),
        candidates=copy.deepcopy(FIXTURE["candidates"]),
        archive_guards=guards,
        model="mock/deepseek-contract",
    )


def test_complete_plan_updates_archive_versions_links_and_governance_once(archive):
    db, ids = archive, FIXTURE["ids"]
    payload = request(db)
    result = commit_mobile_cataloging(db, ids["project"], payload)
    assert result["status"] == "completed"
    hero = db.get(m.Character, ids["hero"])
    assert hero.current_version == 4
    assert hero.background == "旧炉工坊学徒。开始核实炉底封印。"
    assert hero.current_goal == "修复炉体"
    assert hero.items_or_assets == "一柄旧锤。新增一枚铜票。"
    assert hero.profile_json["voice"] == "寡言谨慎"
    assert (
        "不可提前知晓"
        in db.query(m.CharacterAIConfig).filter_by(character_id=hero.id).one().custom_system_prompt
    )
    chapter = db.get(m.Chapter, ids["chapter"])
    assert chapter.cataloging_required is False
    assert chapter.outline_node_id == ids["outline"]
    node = db.get(m.OutlineNode, ids["outline"])
    assert node.parent_id == ids["volume"]
    assert node.planned_summary == "预定先修炉"
    assert node.status == "completed"
    assert {
        row.character_id
        for row in db.query(m.OutlineNodeCharacter).filter_by(outline_node_id=node.id)
    } == {ids["hero"], ids["mentor"]}
    assert {
        row.character_id for row in db.query(m.ChapterCharacter).filter_by(chapter_id=chapter.id)
    } == {ids["hero"], ids["mentor"]}
    assert db.get(m.OutlineNode, ids["scene1"]).parent_id == node.id
    assert db.get(m.OutlineNode, ids["scene2"]).parent_id == node.id
    assert db.query(m.Foreshadowing).one().status == "open"
    counts = {
        model: db.query(model).count()
        for model in (m.CharacterVersion, m.CatalogingApplyLog, m.NarrativeGovernanceEvent)
    }
    assert commit_mobile_cataloging(db, ids["project"], payload)["replayed"]
    assert {model: db.query(model).count() for model in counts} == counts


@pytest.mark.parametrize(
    "change", ["prose", "version", "archive", "foreign", "incomplete", "empty_guard"]
)
def test_invalid_or_stale_plan_never_partially_writes_domain(archive, change):
    db, ids = archive, FIXTURE["ids"]
    payload = request(db)
    if change == "prose":
        db.get(m.Chapter, ids["chapter"]).content += "作者又改了一句。"
    elif change == "version":
        db.get(m.Chapter, ids["chapter"]).current_version += 1
    elif change == "archive":
        db.get(m.Character, ids["hero"]).current_version += 1
    elif change == "foreign":
        payload.candidates[1]["id"] = str(uuid4())
    elif change == "incomplete":
        payload.candidates = [c for c in payload.candidates if c.get("scene_number") != 2]
    else:
        payload.archive_guards[0]["fields"] = {}
    db.commit()
    before = db.get(m.Character, ids["hero"]).current_version
    with pytest.raises(ValueError):
        commit_mobile_cataloging(db, ids["project"], payload)
    assert db.get(m.Character, ids["hero"]).current_version == before
    assert db.get(m.Character, ids["hero"]).background == "旧炉工坊学徒。"
    assert db.get(m.Chapter, ids["chapter"]).cataloging_required
    assert db.query(m.ChapterSummary).count() == 0
    assert db.query(m.CatalogingApplyLog).count() == 0
    assert db.query(m.CatalogingJob).one().status == "failed"


def test_idempotency_key_cannot_apply_different_plan(archive):
    payload = request(archive)
    commit_mobile_cataloging(archive, FIXTURE["ids"]["project"], payload)
    payload.candidates[0]["summary_text"] += "更改。"
    with pytest.raises(ValueError, match="不同内容"):
        commit_mobile_cataloging(archive, FIXTURE["ids"]["project"], payload)


def test_failed_receipt_can_retry_same_plan_after_precondition_is_restored(archive):
    db, ids = archive, FIXTURE["ids"]
    payload = request(db)
    character = db.get(m.Character, ids["hero"])
    character.current_version = 3
    db.commit()
    with pytest.raises(ValueError):
        commit_mobile_cataloging(db, ids["project"], payload)
    character.current_version = 2
    db.commit()
    assert commit_mobile_cataloging(db, ids["project"], payload)["status"] == "completed"
    assert db.query(m.CatalogingJob).count() == 1


def test_mobile_commit_route_uses_paired_device_and_shared_project_boundary(monkeypatch):
    from types import SimpleNamespace

    from app.bootstrap.http_security import GatewayAuthenticationMiddleware as Auth
    from app.modules.gateway.interfaces import project_access

    scope = dict(path="/api/v1/projects/project-1/cataloging/mobile-commit", method="POST")
    device = SimpleNamespace(role="editor", platform="android")
    monkeypatch.setattr(
        project_access, "is_project_shared", lambda identity: identity == "project-1"
    )
    assert Auth._authorize_remote_path(scope, device)
    assert not Auth._authorize_remote_path({**scope, "method": "DELETE"}, device)
    assert not Auth._authorize_remote_path(
        {**scope, "path": scope["path"].replace("project-1", "private")}, device
    )
