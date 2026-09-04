"""Archive preservation and explicit target boundaries during cataloging."""
import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, CatalogingCandidate, Chapter, Character, Project
from app.services.cataloging.applier import apply_candidate
from app.services.cataloging.candidate_store import create_candidate_from_raw
from app.services.cataloging.character_ops import apply_character_create, apply_character_state, apply_character_update
from app.services.cataloging.orchestrator import create_cataloging_job
from app.services.cataloging.snapshots import character_snapshot
from app.services.workspace.tools.external_cataloging import save_external_cataloging_candidates


@pytest.fixture
def archive():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        project = Project(id="project", title="资料保全")
        other = Project(id="other", title="另一作品")
        chapter = Chapter(id="chapter", project_id=project.id, title="对读", content="主角核对旧资料。")
        character = Character(id="known", project_id=project.id, name="主角", age="32", appearance="短发",
                              items_or_assets="旧证物、旧回执",
                              background="调查员，长期负责核对原始记录。",
                              profile_json={"reveal_chapter": 12, "voice": "情绪激动时声音变小"})
        foreign = Character(id="foreign", project_id=other.id, name="别的主角", age="18")
        db.add_all([project, other, chapter, character, foreign])
        db.commit()
        job = create_cataloging_job(db, project.id, "auto", "test:model", [chapter.id])
        yield db, chapter, character, job, job.chapter_runs[0]
    engine.dispose()


def staged(archive, item_type, payload):
    db, chapter, _, job, run = archive
    row = CatalogingCandidate(job_id=job.id, chapter_run_id=run.id, project_id=chapter.project_id,
                             chapter_id=chapter.id, item_type=item_type, raw_payload=json.dumps(payload))
    db.add(row)
    db.flush()
    return row


def test_create_cannot_overwrite_an_existing_character(archive):
    db, chapter, character, _, _ = archive
    before = character_snapshot(character)
    payload = {"name": character.name, "age": "不详", "profile": {"reveal_chapter": 1}}
    with pytest.raises(ValueError, match="已存在"):
        apply_character_create(db, staged(archive, "character_create", payload), chapter, payload)
    assert character_snapshot(character) == before
    assert db.query(Character).count() == 2


@pytest.mark.parametrize("target", [None, "missing", "foreign"], ids=["missing-id", "unknown-id", "foreign-id"])
def test_update_never_falls_back_to_name_or_creation(archive, target):
    db, chapter, character, _, _ = archive
    before = character_snapshot(character)
    payload = {"name": character.name, "age": "青年"}
    if target is not None:
        payload["id"] = target
    with pytest.raises(ValueError):
        apply_candidate(db, staged(archive, "character_update", payload))
    assert character_snapshot(character) == before
    assert db.query(Character).count() == 2


def test_update_by_real_id_preserves_unsupplied_profile_fields(archive):
    db, chapter, character, _, _ = archive
    payload = {"id": character.id, "profile": {"core_belief": "核对原始证据"}}
    apply_character_update(db, staged(archive, "character_update", payload), chapter, payload)
    assert character.age == "32"
    assert character.appearance == "短发"
    assert character.profile_json == {
        "reveal_chapter": 12, "voice": "情绪激动时声音变小", "core_belief": "核对原始证据",
    }


def test_state_invalid_id_does_not_fall_back_to_matching_name(archive):
    db, chapter, character, _, _ = archive
    payload = {"id": "foreign", "name": character.name, "age": "青年"}
    with pytest.raises(ValueError, match="角色不存在"):
        apply_character_state(db, staged(archive, "character_state_update", payload), chapter, payload)
    assert character.age == "32"


def test_explicit_aliases_do_not_choose_a_create_target(archive):
    db, chapter, character, _, _ = archive
    payload = {"name": "甲/乙", "aliases": [character.name], "age": "19"}
    result = apply_character_create(db, staged(archive, "character_create", payload), chapter, payload)
    assert result["target_id"] != character.id
    assert db.query(Character).filter_by(id=result["target_id"]).one().name == "甲/乙"
    assert character.age == "32"


def test_native_candidate_batch_rejects_collision_before_any_staging(archive):
    db, chapter, character, job, run = archive
    run.status = "facts_saved"
    db.flush()
    result = asyncio.run(save_external_cataloging_candidates(db, chapter.project_id, {
        "job_id": job.id, "chapter_id": chapter.id,
        "candidates": [
            {"type": "chapter_summary", "summary_text": "主角核对证据。"},
            {"type": "character_create", "name": character.name, "age": "不详"},
        ],
    }))
    assert result["status"] == "skipped"
    assert "character_update" in result["data"]["validation_errors"][0]
    assert db.query(CatalogingCandidate).count() == 0
    assert character.age == "32"


def test_state_assets_cannot_silently_replace_a_nonempty_archive(archive):
    db, chapter, character, job, run = archive
    unsafe = create_candidate_from_raw(
        db,
        job,
        run,
        {"type": "character_state_update", "name": character.name,
         "items_or_assets": "本章新证物"},
        0,
    )
    assert "bad_line" in unsafe
    assert "items_or_assets_before" in unsafe["error"]
    assert character.items_or_assets == "旧证物、旧回执"

    incomplete = create_candidate_from_raw(
        db,
        job,
        run,
        {"type": "character_state_update", "name": character.name,
         "items_or_assets_before": "旧证物、旧回执",
         "items_or_assets": "本章新证物"},
        1,
    )
    assert "bad_line" in incomplete
    assert "逐字保留" in incomplete["error"]

    safe = create_candidate_from_raw(
        db,
        job,
        run,
        {"type": "character_state_update", "name": character.name,
         "items_or_assets_before": "旧证物、旧回执",
         "items_or_assets": "旧证物、旧回执；本章新增：新证物"},
        2,
    )
    candidate = safe["candidate"]
    apply_candidate(db, candidate)
    assert character.items_or_assets == "旧证物、旧回执；本章新增：新证物"


def test_state_assets_reject_a_stale_prior_snapshot_at_apply_time(archive):
    db, chapter, character, _, _ = archive
    payload = {
        "name": character.name,
        "items_or_assets_before": "旧证物、旧回执",
        "items_or_assets": "旧证物、旧回执；本章新增：新证物",
    }
    candidate = staged(archive, "character_state_update", payload)
    character.items_or_assets = "作者刚刚改过的当前值"
    db.flush()
    with pytest.raises(ValueError, match="与当前档案不一致"):
        apply_character_state(db, candidate, chapter, payload)
    assert character.items_or_assets == "作者刚刚改过的当前值"


def test_profile_background_cannot_silently_replace_a_nonempty_archive(archive):
    db, _, character, job, run = archive
    unsafe = create_candidate_from_raw(
        db,
        job,
        run,
        {"type": "character_update", "id": character.id, "name": character.name,
         "background": "本章负责查验水样。"},
        0,
    )
    assert "bad_line" in unsafe
    assert "background_before" in unsafe["error"]
    assert character.background == "调查员，长期负责核对原始记录。"

    incomplete = create_candidate_from_raw(
        db,
        job,
        run,
        {"type": "character_update", "id": character.id, "name": character.name,
         "background_before": "调查员，长期负责核对原始记录。",
         "background": "本章负责查验水样。"},
        1,
    )
    assert "bad_line" in incomplete
    assert "逐字保留" in incomplete["error"]

    safe = create_candidate_from_raw(
        db,
        job,
        run,
        {"type": "character_update", "id": character.id, "name": character.name,
         "background_before": "调查员，长期负责核对原始记录。",
         "background": "调查员，长期负责核对原始记录。本章确认其也负责查验水样。"},
        2,
    )
    apply_candidate(db, safe["candidate"])
    assert character.background == "调查员，长期负责核对原始记录。本章确认其也负责查验水样。"


def test_profile_background_rejects_a_stale_prior_snapshot_at_apply_time(archive):
    db, chapter, character, _, _ = archive
    payload = {
        "id": character.id,
        "name": character.name,
        "background_before": "调查员，长期负责核对原始记录。",
        "background": "调查员，长期负责核对原始记录。本章确认其也负责查验水样。",
    }
    candidate = staged(archive, "character_update", payload)
    character.background = "作者刚刚补充的稳定背景。"
    db.flush()
    with pytest.raises(ValueError, match="background_before"):
        apply_character_update(db, candidate, chapter, payload)
    assert character.background == "作者刚刚补充的稳定背景。"


def test_state_appearance_change_requires_current_snapshot_and_verbatim_chapter_evidence(archive):
    db, chapter, character, job, run = archive
    chapter.content = "主角剪成了齐肩长发，随后继续核对旧资料。"
    db.flush()

    missing_guard = create_candidate_from_raw(
        db,
        job,
        run,
        {"type": "character_state_update", "name": character.name,
         "appearance": "齐肩长发"},
        0,
    )
    assert "bad_line" in missing_guard
    assert "appearance_before" in missing_guard["error"]

    invented_evidence = create_candidate_from_raw(
        db,
        job,
        run,
        {"type": "character_state_update", "name": character.name,
         "appearance_before": "短发", "appearance": "齐肩长发",
         "appearance_evidence": "主角换了新发型"},
        1,
    )
    assert "bad_line" in invented_evidence
    assert "逐字摘录" in invented_evidence["error"]

    safe = create_candidate_from_raw(
        db,
        job,
        run,
        {"type": "character_state_update", "name": character.name,
         "appearance_before": "短发", "appearance": "齐肩长发",
         "appearance_evidence": "主角剪成了齐肩长发"},
        2,
    )
    apply_candidate(db, safe["candidate"])
    assert character.appearance == "齐肩长发"


def test_unchanged_age_and_appearance_do_not_require_change_evidence(archive):
    db, _, character, job, run = archive
    result = create_candidate_from_raw(
        db,
        job,
        run,
        {"type": "character_state_update", "name": character.name,
         "age": "32", "appearance": "短发"},
        0,
    )
    assert result.get("candidate") is not None
