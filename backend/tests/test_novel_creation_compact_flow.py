"""Regression tests for lightweight concepts and staged new-book creation."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import NovelCreationSession
from app.database.session import Base
from app.routers.novel_creation import NovelCreationStageRunRequest, start_creation_stage_run
from app.services.novel_creation_workspace import (
    STAGE_ORDER,
    build_apply_blueprint,
    derive_stage,
    initialize_session_draft,
    patch_session,
    save_compact_concepts,
    save_stage,
)
from app.services.workspace.tools.novel_creation import advance_novel_creation_interview, apply_novel_blueprint
from app.services.workspace.tools.novel_creation_v2 import generate_novel_creation_stage


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _session(db):
    session = NovelCreationSession(
        mode="internal_llm",
        status="drafting",
        user_brief="A thriller that shocks readers with a devastating reversal.",
        genre="suspense",
    )
    db.add(session)
    initialize_session_draft(session, {"preset_id": "suspense", "target_chapters": 240})
    db.commit()
    return session


def _concepts():
    return [
        {
            "title": f"Concept {index}",
            "subtitle": "High-concept suspense",
            "logline": f"A witness follows clue {index} and discovers the case has rewritten their past.",
            "protagonist_seed": {
                "name": f"Lead {index}",
                "identity": "Forensic archivist",
                "goal": "Expose the hidden trial",
                "lack": "Cannot trust their own memories",
            },
            "world_hook": "Every verified memory can be sold, but each sale changes the buyer's future.",
            "core_conflict": "The closer the lead gets to the truth, the less evidence they can trust.",
            "story_engine": "Each recovered record opens a clue and erases one reliable relationship.",
            "opening_hook": "The victim's final recording is spoken in the lead's own voice.",
            "differentiators": ["Memory evidence", f"Reversal route {index}"],
            "risks": ["Keep the memory rules observable"],
        }
        for index in range(1, 4)
    ]


def test_interview_ready_state_never_calls_full_blueprint_generation():
    db = _db()
    session = _session(db)
    with patch(
        "app.services.workspace.tools.novel_creation._evaluate_answers",
        new=AsyncMock(return_value={"action": "generate", "reason": "enough context"}),
    ):
        result = asyncio.run(advance_novel_creation_interview(db, "", {
            "session_id": session.id,
            "user_brief": session.user_brief,
            "qa_history": [{"question": "What should shock readers?", "answer": "A devastating reversal."}],
            "model": "openai:test",
        }))

    assert result["status"] == "ok"
    assert result["data"]["state"] == "ready"
    assert session.blueprint_json is None
    assert session.draft_json["interview"]["status"] == "completed"


def test_skip_interview_preserves_history_without_model_call():
    db = _db()
    session = _session(db)
    with patch("app.services.workspace.tools.novel_creation._evaluate_answers", new=AsyncMock()) as evaluate:
        result = asyncio.run(advance_novel_creation_interview(db, "", {
            "session_id": session.id,
            "qa_history": [{"question": "What should shock readers?", "answer": "A devastating reversal."}],
            "skip_questions": True,
        }))

    assert result["data"]["state"] == "ready"
    assert result["data"]["skipped"] is True
    assert session.draft_json["interview"]["history"]
    evaluate.assert_not_awaited()


def test_compact_concept_run_limits_output_and_keeps_legacy_blueprints_empty():
    db = _db()
    session = _session(db)
    content = json.dumps({"concepts": _concepts()})
    with patch(
        "app.services.workspace.tools.novel_creation_v2.LLMGateway.chat_completion",
        new=AsyncMock(return_value={"content": content}),
    ) as completion:
        result = asyncio.run(generate_novel_creation_stage(db, "", {
            "session_id": session.id,
            "stage": "concepts",
            "model": "openai:test",
            "use_model": True,
        }))

    assert result["status"] == "ok"
    assert completion.await_args.kwargs["max_tokens"] == 3200
    assert completion.await_args.kwargs["retry"] == 0
    assert session.blueprint_json is None
    assert len(session.draft_json["concepts"]) == 3
    assert len(session.draft_json["concept_seeds"]) == 3
    assert result["data"]["run"]["status"] == "completed"


def test_invalid_concepts_fail_then_a_retry_can_succeed():
    db = _db()
    session = _session(db)
    invalid = json.dumps({"concepts": _concepts()[:2]})
    with patch(
        "app.services.workspace.tools.novel_creation_v2.LLMGateway.chat_completion",
        new=AsyncMock(return_value={"content": invalid}),
    ):
        failed = asyncio.run(generate_novel_creation_stage(db, "", {
            "session_id": session.id,
            "stage": "concepts",
            "model": "openai:test",
            "use_model": True,
        }))
    assert failed["status"] == "error"
    assert failed["data"]["run"]["status"] == "failed"

    valid = json.dumps({"concepts": _concepts()})
    with patch(
        "app.services.workspace.tools.novel_creation_v2.LLMGateway.chat_completion",
        new=AsyncMock(return_value={"content": valid}),
    ):
        retried = asyncio.run(generate_novel_creation_stage(db, "", {
            "session_id": session.id,
            "stage": "concepts",
            "model": "openai:test",
            "use_model": True,
        }))
    assert retried["status"] == "ok"
    assert retried["data"]["run"]["id"] != failed["data"]["run"]["id"]


def test_compact_seed_can_drive_stages_and_final_apply_blueprint():
    db = _db()
    session = _session(db)
    save_compact_concepts(session, _concepts())
    patch_session(session, {"selected_concept_id": "concept-1"})
    save_stage(session, "constraints", session.draft_json["form"], confirm=True)
    save_stage(session, "concepts", {"options": session.draft_json["concepts"], "selected_concept_id": "concept-1"}, confirm=True)
    for stage in STAGE_ORDER[2:]:
        save_stage(session, stage, derive_stage(session, stage), confirm=stage != "final_review")

    blueprint = build_apply_blueprint(session)
    assert blueprint["title"] == "Concept 1"
    assert blueprint["protagonist"]["name"] == "Lead 1"
    assert len(blueprint["outline"]) >= 15
    with patch("app.services.workspace.tools.novel_creation._is_real_session", return_value=False):
        applied = asyncio.run(apply_novel_blueprint(db, "", {"session_id": session.id, "mode": "auto"}))
    assert applied["status"] == "ok"


def test_duplicate_running_concept_request_reuses_existing_run():
    db = _db()
    session = _session(db)
    payload = NovelCreationStageRunRequest(stage="concepts", model="openai:test", operation="generate_concepts")

    def capture_task(coro):
        coro.close()
        return MagicMock()

    with patch("app.routers.novel_creation.asyncio.create_task", side_effect=capture_task) as create_task:
        first = asyncio.run(start_creation_stage_run(session.id, payload, db))
        second = asyncio.run(start_creation_stage_run(session.id, payload, db))

    assert first.data["run"]["id"] == second.data["run"]["id"]
    assert create_task.call_count == 1
