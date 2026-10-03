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
from app.services.cataloging.plan_validation import inspect_complete_plan
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
        from tests.test_cataloging_plan import plan_rows
        summary = plan_rows(character)[0]
        summary["coverage_manifest"]["characters"] = []
        run = job.chapter_runs[0]
        db.add(CatalogingCandidate(job_id=job.id, chapter_run_id=run.id, project_id=project.id,
            chapter_id=chapter.id, item_type="chapter_summary", raw_payload=json.dumps(summary)))
        db.commit()

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


def test_unresolved_state_is_rejected_with_real_targets_for_model_repair(archive):
    db, _, character, job, run = archive
    raw = {"type": "character_state_update", "name": "简称", "current_goal": "核对资料"}
    result = create_candidate_from_raw(db, job, run, raw, 0)
    assert "bad_line" in result
    assert "id" in result["error"]
    assert db.query(CatalogingCandidate).filter(CatalogingCandidate.item_type != "chapter_summary").count() == 0
    # The model selects the real ID; the application never resolves the alias.
    result = create_candidate_from_raw(db, job, run, {**raw, "id": character.id, "name": character.name}, 0)
    applied = apply_candidate(db, result["candidate"])
    assert applied["target_id"] == character.id
    assert character.current_goal == "核对资料"


def test_state_character_id_uses_same_target_for_validation_and_apply(archive):
    db, chapter, character, _, _ = archive
    payload = {"id": character.id, "name": "简称", "current_goal": "核对资料"}
    result = apply_character_state(db, staged(archive, "character_state_update", payload), chapter, payload)
    assert result["target_id"] == character.id


def test_incremental_merge_revalidates_guarded_fields_retained_from_previous_candidate(archive):
    db, _, character, job, run = archive
    previous = {"id": character.id, "background_before": character.background,
                "background": character.background + "已确认的补充。"}
    row = staged(archive, "character_update", previous)
    character.background = "作者刚刚改写的完整背景。"
    db.flush()
    result = create_candidate_from_raw(db, job, run,
                {"type": "character_update", "id": character.id, "personality": "严谨"}, 1)
    assert "bad_line" in result
    assert result["repair_context"]["expected_value"] == character.background
    assert json.loads(row.raw_payload) == previous


def test_model_incremental_repair_cannot_erase_author_candidate_edits(archive):
    db, _, character, job, run = archive
    original = {"id": character.id, "current_goal": "模型原值"}
    row = staged(archive, "character_state_update", original)
    row.edited_payload = json.dumps({**original, "current_goal": "作者修订"})
    row.status = "edited"
    db.flush()
    result = create_candidate_from_raw(db, job, run,
                {"type": "character_state_update", "id": character.id, "current_goal": "模型覆盖"}, 1)
    assert "bad_line" in result
    assert json.loads(row.edited_payload)["current_goal"] == "作者修订"


@pytest.mark.parametrize("resolution_only", [False, True])
def test_partial_apply_retry_keeps_written_candidates_and_rollback_log(archive, resolution_only):
    from app.database.models import CatalogingApplyLog, CatalogingFact
    from app.services.cataloging.applier import apply_candidates_for_run
    from app.services.cataloging.job_control import reset_run_for_retry, reset_run_for_plan_repair

    db, chapter, character, job, run = archive
    written = staged(archive, "character_state_update", {"id": character.id, "current_goal": "已完成目标"})
    from app.services.cataloging.applier import apply_candidate, _mark_applied
    _mark_applied(db, job, run, written, apply_candidate(db, written))
    db.commit()
    written_id = written.id
    log_id = db.query(CatalogingApplyLog).filter_by(candidate_id=written_id).one().id
    broken = staged(archive, "character_state_update", {"name": "不存在", "current_goal": "待修复"})
    broken.status = "apply_failed"
    fact = CatalogingFact(job_id=job.id, chapter_run_id=run.id, project_id=job.project_id,
                         chapter_id=chapter.id, fact_type="chapter_overview", raw_payload='{}')
    db.add(fact)
    run.status = "failed"
    job.status = "paused_on_failure"
    db.commit()
    fact_id = fact.id

    reset = reset_run_for_plan_repair if resolution_only else reset_run_for_retry
    reset(db, job, run)
    db.commit()
    assert run.status == "extracting"
    assert db.query(CatalogingCandidate).filter(CatalogingCandidate.item_type != "chapter_summary").count() == 2
    assert db.get(CatalogingCandidate, written_id).status == "applied"
    assert db.get(CatalogingApplyLog, log_id) is not None
    assert db.get(CatalogingFact, fact_id) is not None
    assert character.current_goal == "已完成目标"
    assert db.query(CatalogingApplyLog).filter_by(candidate_id=written_id).count() == 1




def test_explicit_aliases_do_not_choose_a_create_target(archive):
    db, chapter, character, _, _ = archive
    payload = {"name": "甲/乙", "aliases": [character.name], "age": "19"}
    result = apply_character_create(db, staged(archive, "character_create", payload), chapter, payload)
    assert result["target_id"] != character.id
    assert db.query(Character).filter_by(id=result["target_id"]).one().name == "甲/乙"
    assert character.age == "32"


def test_native_candidate_batch_rejects_collision_before_any_staging(archive):
    db, chapter, character, job, run = archive
    run.status = "extracting"
    db.flush()
    result = asyncio.run(save_external_cataloging_candidates(db, chapter.project_id, {
        "job_id": job.id, "chapter_id": chapter.id,
        "candidates": [
            {"type": "character_create", "client_id": "b9b4da89-f4a7-4c88-9b71-bba251015049", "name": character.name, "age": "不详"},
        ],
    }))
    assert result["status"] == "skipped"
    assert "ID" in result["data"]["candidate_errors"][0]["message"]
    assert db.query(CatalogingCandidate).filter(CatalogingCandidate.item_type != "chapter_summary").count() == 0
    assert character.age == "32"


def test_state_assets_cannot_silently_replace_a_nonempty_archive(archive):
    db, chapter, character, job, run = archive
    unsafe = create_candidate_from_raw(
        db,
        job,
        run,
        {"type": "character_state_update", "id": character.id, "name": character.name,
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
        {"type": "character_state_update", "id": character.id, "name": character.name,
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
        {"type": "character_state_update", "id": character.id, "name": character.name,
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
        "id": character.id,
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
        {"type": "character_state_update", "id": character.id, "name": character.name,
         "appearance": "齐肩长发"},
        0,
    )
    assert "bad_line" in missing_guard
    assert "appearance_before" in missing_guard["error"]

    invented_evidence = create_candidate_from_raw(
        db,
        job,
        run,
        {"type": "character_state_update", "id": character.id, "name": character.name,
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
        {"type": "character_state_update", "id": character.id, "name": character.name,
         "appearance_before": "短发", "appearance": "齐肩长发",
         "appearance_evidence": "主角剪成了齐肩长发"},
        2,
    )
    apply_candidate(db, safe["candidate"])
    assert character.appearance == "齐肩长发"


def test_new_character_state_requires_evidence_before_staging_or_finalization(archive):
    db, chapter, _, job, run = archive
    new_id = "00000000-0000-4000-8000-000000000001"
    chapter.content = "老陈剪去长发，露出一头灰发。"
    summary_row = db.query(CatalogingCandidate).filter_by(
        chapter_run_id=run.id, item_type="chapter_summary",
    ).one()
    summary = json.loads(summary_row.raw_payload)
    summary["character_bindings"].append({
        "name": "老陈", "id": new_id, "decision": "new",
        "source_labels": ["老陈"], "reason": "本章出现老陈",
    })
    summary["coverage_manifest"]["characters"].append("老陈")
    summary_row.raw_payload = json.dumps(summary, ensure_ascii=False)
    db.flush()

    created = create_candidate_from_raw(db, job, run, {
        "type": "character_create", "client_id": new_id, "name": "老陈",
        "appearance": "长发",
    }, 0)
    assert created.get("candidate") is not None, created

    state = {"type": "character_state_update", "id": new_id, "name": "老陈",
             "appearance_before": "长发", "appearance": "灰发"}
    missing_evidence = create_candidate_from_raw(db, job, run, state, 1)
    assert "bad_line" in missing_evidence
    assert "appearance_evidence" in missing_evidence["error"]
    assert db.query(CatalogingCandidate).filter_by(item_type="character_state_update").count() == 0
    assert db.get(Character, new_id) is None

    # A retained candidate from an older interrupted job must also be caught
    # before the formal chapter transaction starts.
    invalid_row = staged(archive, "character_state_update", {
        "id": new_id, "name": "老陈", "appearance_before": "长发", "appearance": "灰发",
    })
    report = inspect_complete_plan(db, run)
    assert any(error["candidate_id"] == invalid_row.id
               and "appearance_evidence" in error["message"]
               for error in report["candidate_errors"])
    repaired = create_candidate_from_raw(db, job, run, {
        **state, "appearance_evidence": "露出一头灰发",
    }, 2)
    assert repaired.get("candidate") is not None, repaired
    assert repaired["candidate"].id == invalid_row.id
    assert not any(error["candidate_id"] == invalid_row.id
                   for error in inspect_complete_plan(db, run)["candidate_errors"])
    apply_character_create(db, created["candidate"], chapter,
                           json.loads(created["candidate"].raw_payload))
    apply_character_state(db, repaired["candidate"], chapter,
                          json.loads(repaired["candidate"].raw_payload))
    assert db.get(Character, new_id).appearance == "灰发"


def test_unchanged_age_and_appearance_do_not_require_change_evidence(archive):
    db, _, character, job, run = archive
    result = create_candidate_from_raw(
        db,
        job,
        run,
        {"type": "character_state_update", "id": character.id, "name": character.name,
         "age": "32", "appearance": "短发"},
        0,
    )
    assert result.get("candidate") is not None
