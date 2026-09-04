"""Connection-independent execution for workspace-assistant SSE requests."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import AssistantMessage, AssistantRun
from ...database.session import SessionLocal
from ..operation_runtime import (
    activate_operation,
    heartbeat_loop,
    register_operation_actions,
    unregister_operation_actions,
)
from .assistant_public_errors import public_server_failure
from .run_log import mark_assistant_run

_BACKGROUND_ASSISTANT_TASKS: set[asyncio.Task[Any]] = set()
_EXPLICIT_CANCELLATIONS: set[asyncio.Task[Any]] = set()
_END = object()
_CREATE_TASK = asyncio.create_task
logger = logging.getLogger(__name__)


def assistant_cancel_was_explicit() -> bool:
    task = asyncio.current_task()
    return bool(task and task in _EXPLICIT_CANCELLATIONS)


def _payload_from_sse(chunk: str) -> dict[str, Any] | None:
    for line in str(chunk or "").splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            return None
        with suppress(json.JSONDecodeError):
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
    return None


def _mark_cancelled(run_id: str | None) -> None:
    if not run_id:
        return
    db = SessionLocal()
    try:
        run = db.query(AssistantRun).filter(AssistantRun.id == run_id).first()
        if not run or run.status not in {"running", "queued"}:
            return
        message = (
            db.query(AssistantMessage)
            .filter(AssistantMessage.id == run.assistant_message_id)
            .first()
            if run.assistant_message_id
            else None
        )
        if message and message.status == "running":
            message.status = "aborted"
            message.content = "任务已取消，本轮不会再写入章节。"
        mark_assistant_run(
            db,
            run,
            status="cancelled",
            phase="cancelled",
            error="用户取消了任务",
            final_reply="任务已取消，本轮不会再写入章节。",
        )
    finally:
        db.close()


async def detached_assistant_stream(
    source_factory: Callable[[Session], AsyncIterator[str]],
    *,
    operation_id_hint: str | None = None,
    run_id_hint: str | None = None,
) -> AsyncIterator[str]:
    """Stream events while allowing the producer to survive a client disconnect.

    The producer owns its database session. The HTTP stream is only a subscriber;
    closing it never implies task cancellation. Explicit Operation cancellation
    cancels the producer and projects a durable cancelled run state.
    """

    # Keep the subscriber buffer lossless while it is connected.  The initial
    # ``run`` event carries the durable run/operation identifiers and terminal
    # events close the HTTP response; dropping either can make cancellation
    # target the wrong task or leave the browser waiting forever.  Once the
    # subscriber disconnects ``publish`` becomes a no-op, so a detached task
    # does not continue accumulating events in memory.
    queue: asyncio.Queue[str | object] = asyncio.Queue()
    consumer_active = True
    run_id = str(run_id_hint or "").strip() or None
    operation_id = str(operation_id_hint or "").strip() or None
    registered_operation_id: str | None = None
    heartbeat_task: asyncio.Task[Any] | None = None

    async def publish(chunk: str) -> None:
        if not consumer_active:
            return
        queue.put_nowait(chunk)

    async def produce() -> None:
        nonlocal run_id, operation_id, registered_operation_id, heartbeat_task
        db = SessionLocal()
        source: AsyncIterator[str] | None = None

        async def attach_operation(next_operation_id: str) -> None:
            nonlocal operation_id, registered_operation_id, heartbeat_task
            next_operation_id = str(next_operation_id or "").strip()
            if not next_operation_id or next_operation_id == registered_operation_id:
                operation_id = next_operation_id or operation_id
                return
            if heartbeat_task:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
                heartbeat_task = None
            if registered_operation_id:
                unregister_operation_actions(registered_operation_id)
            operation_id = next_operation_id

            def request_cancel() -> None:
                _EXPLICIT_CANCELLATIONS.add(producer_task)
                producer_task.cancel()

            register_operation_actions(operation_id, cancel=request_cancel)
            registered_operation_id = operation_id
            heartbeat_task = _CREATE_TASK(
                heartbeat_loop(operation_id),
                name=f"assistant-heartbeat-{run_id or operation_id}",
            )

        try:
            if operation_id:
                await attach_operation(operation_id)
            source = source_factory(db)
            while True:
                try:
                    if operation_id:
                        with activate_operation(operation_id):
                            chunk = await source.__anext__()
                    else:
                        chunk = await source.__anext__()
                except StopAsyncIteration:
                    break

                payload = _payload_from_sse(chunk)
                if payload and payload.get("type") == "run":
                    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
                    run_id = str(run.get("id") or "").strip() or run_id
                    next_operation_id = str(run.get("operation_id") or "").strip() or None
                    if next_operation_id:
                        await attach_operation(next_operation_id)
                await publish(chunk)
        except asyncio.CancelledError:
            if assistant_cancel_was_explicit():
                _mark_cancelled(run_id)
            raise
        except Exception as exc:
            error_id = uuid.uuid4().hex
            failure = public_server_failure(error_id)
            logger.exception(
                "Detached workspace stream failed error_id=%s run=%s type=%s",
                error_id,
                run_id,
                type(exc).__name__,
            )
            if run_id:
                error_db = SessionLocal()
                try:
                    run = error_db.query(AssistantRun).filter(AssistantRun.id == run_id).first()
                    if run and run.status == "running":
                        mark_assistant_run(
                            error_db,
                            run,
                            status="error",
                            phase="stream_runtime_error",
                            error=failure.persisted_error,
                        )
                finally:
                    error_db.close()
            await publish(
                "data: "
                + json.dumps(
                    {"type": "error", **failure.to_dict()},
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            await publish("data: [DONE]\n\n")
        finally:
            if source is not None:
                with suppress(Exception):
                    await source.aclose()
            if heartbeat_task:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            if registered_operation_id:
                unregister_operation_actions(registered_operation_id)
            _EXPLICIT_CANCELLATIONS.discard(producer_task)
            db.close()
            if consumer_active:
                queue.put_nowait(_END)

    producer_task = _CREATE_TASK(produce(), name="workspace-assistant")
    _BACKGROUND_ASSISTANT_TASKS.add(producer_task)
    producer_task.add_done_callback(_BACKGROUND_ASSISTANT_TASKS.discard)

    try:
        while True:
            item = await queue.get()
            if item is _END:
                break
            yield str(item)
    finally:
        # A disconnected client stops receiving events but the producer remains
        # alive. It can only be stopped through the Operation cancel action.
        consumer_active = False


__all__ = ["assistant_cancel_was_explicit", "detached_assistant_stream"]
