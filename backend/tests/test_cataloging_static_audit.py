"""Deterministic cataloging failure reproductions; no provider calls."""
import asyncio
import json

import pytest

from app.database.models import CatalogingFact, CatalogingJob, Chapter
from app.services.cataloging import launcher, orchestrator
from app.services.cataloging.job_control import cancel_job, pause_job, resume_job
from app.services.cataloging.local_cli_result import handle_cli_turn_exception, handle_cli_turn_result
from tests.test_cataloging_progress import native_step, catalog as catalog_fixture, model_rows

catalog = catalog_fixture








@pytest.mark.parametrize("change", [cancel_job, pause_job, resume_job])
def test_completed_job_cannot_be_reopened_by_control_transport(catalog, change):
    _, sessions, job_id = catalog
    with sessions() as db:
        job = db.get(CatalogingJob, job_id)
        job.status = "completed"
        db.commit()
        change(job)
        assert job.status == "completed"


@pytest.mark.parametrize("exception", [False, True])
def test_late_cli_exit_cannot_undo_committed_completion(catalog, exception):
    _, sessions, job_id = catalog
    with sessions() as db:
        job = db.get(CatalogingJob, job_id)
        job.status = "completed"
        run = job.chapter_runs[0]
        run.status = "completed"
        run_id = run.id
        db.commit()
    args = dict(job_id=job_id, chapter_run_id=run_id, agent_run_id="unused",
                stage="apply", session_factory=sessions)
    if exception:
        handle_cli_turn_exception(**args, exc=RuntimeError("进程退出异常"))
    else:
        asyncio.run(handle_cli_turn_result(**args, chapter_title="核验", returncode=1,
                    stdout="", stderr="进程退出异常", tool_events_before=0, no_save_attempts={}))
    with sessions() as db:
        job = db.get(CatalogingJob, job_id)
        assert job.status == "completed"
        assert job.chapter_runs[0].status == "completed"


def test_stale_chapter_version_is_rejected_before_spending_model_calls(catalog, monkeypatch):
    _, sessions, job_id = catalog
    calls = []

    async def model(**kwargs):
        calls.append(kwargs)
        yield '{}'

    monkeypatch.setattr(orchestrator.LLMGateway, "stream_chat_completion_with_tools", model)
    with sessions() as db:
        job = db.get(CatalogingJob, job_id)
        job.status, job.chapter_runs[0].status = "queued", "pending"
        db.get(Chapter, "c").current_version = 2
        db.commit()
    asyncio.run(launcher.run_cataloging_job(job_id))
    assert not calls


def test_provider_stop_requires_outer_commit_and_is_discarded_on_rollback(catalog, monkeypatch):
    _, sessions, job_id = catalog
    stopped = []
    monkeypatch.setattr(launcher, "cancel_cataloging_runtime", lambda ids, **kw: stopped.append((ids, kw)))
    with sessions() as db:
        job = db.get(CatalogingJob, job_id)
        job.status = "running"
        db.commit()
        pause_job(job)
        with db.begin_nested():
            db.flush()
        assert not stopped
        db.rollback()
        assert not stopped
        assert job.status == "running"
        pause_job(job)
        db.commit()
    assert stopped == [([job_id], {"terminal": False})]


def test_pause_interrupts_waiting_provider_and_resume_starts_a_fresh_worker(catalog, monkeypatch):
    import httpx
    app, sessions, job_id = catalog
    from app.services.cataloging import local_cli_agent
    monkeypatch.setattr(local_cli_agent, "SessionLocal", sessions)

    async def check():
        entered = asyncio.Event()
        resumed = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def model(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                entered.set()
                await asyncio.Event().wait()
            if len(calls) == 2:
                resumed.set()
                await release.wait()
            async for event in native_step(kwargs["messages"], calls[1:], job_id):
                yield event


        monkeypatch.setattr(orchestrator.LLMGateway, "stream_chat_completion_with_tools", model)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await client.post(f"/projects/p/cataloging/{job_id}/retry-current")
            await asyncio.wait_for(entered.wait(), 3)
            first = launcher._LAUNCH_TASKS[job_id]
            response = await client.post(f"/projects/p/cataloging/{job_id}/pause")
            assert response.status_code == 200
            await asyncio.wait_for(asyncio.gather(first, return_exceptions=True), 3)
            with sessions() as db:
                assert db.get(CatalogingJob, job_id).status == "paused"
            response = await client.post(f"/projects/p/cataloging/{job_id}/resume")
            assert response.status_code == 200
            await asyncio.wait_for(resumed.wait(), 3)
            second = launcher._LAUNCH_TASKS[job_id]
            assert second is not first
            release.set()
            await asyncio.wait_for(second, 5)
        with sessions() as db:
            assert db.get(CatalogingJob, job_id).status == "completed"
        assert len(calls) == 5

    asyncio.run(check())


@pytest.mark.parametrize("stage", ["first_step", "after_read"])
@pytest.mark.parametrize("status", [401, 403, 413, 422])
def test_provider_request_rejection_does_not_repeat_identical_calls(catalog, monkeypatch, stage, status):
    _, sessions, job_id = catalog
    calls = []

    class RejectedRequest(Exception):
        status_code = status

    async def model(**kwargs):
        calls.append(kwargs)
        if stage == "after_read" and len(calls) == 1:
            yield {"type": "tool_call_delta", "index": 0, "id": "read", "name": "get_next_external_cataloging_chapter",
                   "arguments_delta": json.dumps({"job_id": job_id})}
            return
        raise RejectedRequest("provider rejected request")

    monkeypatch.setattr(orchestrator.LLMGateway, "stream_chat_completion_with_tools", model)
    with sessions() as db:
        job = db.get(CatalogingJob, job_id)
        job.status, job.chapter_runs[0].status = "queued", "pending"
        db.commit()
    asyncio.run(launcher.run_cataloging_job(job_id))
    assert len(calls) == (1 if stage == "first_step" else 2)
    with sessions() as db:
        assert db.get(CatalogingJob, job_id).status == "paused_on_failure"


def test_worker_launch_waits_for_owning_transaction(catalog, monkeypatch):
    from app.architecture.uow import defer_session_commits
    _, sessions, job_id = catalog
    launched = []
    monkeypatch.setattr(launcher, "queue_cataloging_job", launched.append)
    with sessions() as db:
        job = db.get(CatalogingJob, job_id)
        with defer_session_commits(db):
            assert launcher.queue_managed_cataloging_job(job)
            with db.begin_nested():
                db.flush()
            assert not launched
        db.rollback()
        assert not launched
        with defer_session_commits(db):
            assert launcher.queue_managed_cataloging_job(job)
            assert not launched
        db.commit()
    assert launched == [job_id]
