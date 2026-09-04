"""Tests for persisted system assistant conversations."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.architecture.uow import SqlAlchemyUnitOfWork
from app.database.session import Base
from app.database.write_coordination import install_sqlite_write_coordination
from app.modules.assistant.infrastructure.system_conversations import (
    SqlAlchemySystemConversationStore,
)
from app.modules.assistant.interfaces.system_conversation_dependencies import (
    get_system_conversation_store,
)
from app.routers.system_assistant import (
    SystemConversationCreate,
    SystemConversationScopePatch,
    SystemTurnCreate,
    SystemTurnFinish,
    append_system_turn,
    create_system_conversation,
    finish_system_turn,
    get_system_conversation,
    list_system_conversations,
    set_system_conversation_scope,
    start_system_turn,
)
from app.routers.system_assistant import (
    router as system_assistant_router,
)


def _db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_system_turn_accepts_large_creation_text_with_an_explicit_safety_limit():
    payload = SystemTurnCreate(user_content="设定" * 50_000)
    assert len(payload.user_content) == 100_000
    with pytest.raises(ValidationError):
        SystemTurnCreate(user_content="设" * 1_000_001)


@pytest.mark.parametrize(
    ("payload_type", "payload"),
    [
        (SystemConversationCreate, {"scope_type": "creation"}),
        (SystemConversationScopePatch, {"scope_type": "project"}),
        (SystemTurnCreate, {"user_content": "继续", "scope_type": "creation"}),
        (SystemTurnFinish, {"scope_type": "project"}),
    ],
)
def test_every_conversation_scope_requires_an_identifier(payload_type, payload):
    with pytest.raises(ValidationError):
        payload_type(**payload)


def test_concurrent_conversation_writes_do_not_deadlock_the_async_server(tmp_path):
    database = tmp_path / "system-assistant-concurrency.db"
    database_url = f"sqlite:///{database.as_posix()}"
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
        pool_size=20,
        max_overflow=0,
    )
    install_sqlite_write_coordination(engine, database_url, timeout=2.0)

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _record):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=500")

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(system_assistant_router, prefix="/api/v1")

    def override_store():
        db = factory()
        try:
            with SqlAlchemyUnitOfWork.from_session(db) as uow:
                yield SqlAlchemySystemConversationStore(db)
                uow.commit()
        finally:
            db.close()

    app.dependency_overrides[get_system_conversation_store] = override_store

    async def exercise():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await asyncio.gather(*(
                client.post(
                    "/api/v1/ai/assistant/conversations",
                    json={
                        "title": f"并发会话 {index}",
                        "scope_type": "creation",
                        "scope_id": f"creation-{index}",
                    },
                )
                for index in range(12)
            ))

    responses = asyncio.run(exercise())
    assert [response.status_code for response in responses] == [200] * 12
    with factory() as db:
        assert SqlAlchemySystemConversationStore(db).list()["total"] == 12


def test_system_conversation_persists_messages_and_creation_scope():
    db = _db_session()
    conversations = SqlAlchemySystemConversationStore(db)
    created = asyncio.run(create_system_conversation(
        SystemConversationCreate(
            title="克苏鲁新书",
            scope_type="creation",
            scope_id="session-1",
        ),
        conversations,
    ))
    conversation_id = created.data["conversation"]["id"]

    asyncio.run(append_system_turn(
        conversation_id,
        SystemTurnCreate(
            user_content="帮我创建一本克苏鲁规则怪谈",
            assistant_content="已保存本轮立项资料",
            creation_session_id="session-1",
            user_brief="克苏鲁+规则怪谈",
        ),
        conversations,
    ))

    detail = asyncio.run(get_system_conversation(conversation_id, conversations))
    assert detail.data["conversation"]["creation_session_id"] == "session-1"
    assert [item["role"] for item in detail.data["messages"]] == ["user", "assistant"]
    assert [item["sequence_no"] for item in detail.data["messages"]] == [1, 2]
    assert detail.data["conversation"]["created_at"].endswith("+00:00")
    assert all(item["created_at"].endswith("+00:00") for item in detail.data["messages"])
    listing = asyncio.run(list_system_conversations(conversations))
    assert listing.data["total"] == 1
    assert listing.data["items"][0]["message_count"] == 2


def test_system_conversation_assigns_contiguous_sequences_across_turns():
    db = _db_session()
    conversations = SqlAlchemySystemConversationStore(db)
    conversation_id = conversations.create(
        "立项顺序",
        scope_type="creation",
        scope_id="session-1",
    )["conversation"]["id"]

    conversations.append_turn(conversation_id, {
        "user_content": "第一轮",
        "assistant_content": "第一轮完成",
        "scope_type": "creation",
        "scope_id": "session-1",
    })
    conversations.start_turn(conversation_id, {
        "user_content": "第二轮",
        "scope_type": "creation",
        "scope_id": "session-1",
    })

    detail = conversations.get(conversation_id)

    assert [message["sequence_no"] for message in detail["messages"]] == [1, 2, 3, 4]
    assert [message["role"] for message in detail["messages"]] == [
        "user", "assistant", "user", "assistant",
    ]


def test_system_turn_persists_running_placeholder_before_completion():
    db = _db_session()
    conversations = SqlAlchemySystemConversationStore(db)
    created = asyncio.run(create_system_conversation(
        SystemConversationCreate(title="", scope_type="creation", scope_id="session-1"),
        conversations,
    ))
    conversation_id = created.data["conversation"]["id"]

    started = asyncio.run(start_system_turn(
        conversation_id,
        SystemTurnCreate(user_content="先帮我整理人物设定"),
        conversations,
    ))
    assistant_message = started.data["messages"][1]
    assert started.data["messages"][0]["content"] == "先帮我整理人物设定"
    assert assistant_message["status"] == "running"

    finished = asyncio.run(finish_system_turn(
        conversation_id,
        assistant_message["id"],
        SystemTurnFinish(assistant_content="已整理为角色卡", status="completed"),
        conversations,
    ))
    assert finished.data["message"]["status"] == "completed"
    detail = asyncio.run(get_system_conversation(conversation_id, conversations))
    assert [message["status"] for message in detail.data["messages"]] == ["completed", "completed"]


def test_finishing_an_operation_preserves_creation_agent_protocol_payload():
    db = _db_session()
    conversations = SqlAlchemySystemConversationStore(db)
    created = conversations.create(
        "立项工具回合",
        scope_type="creation",
        scope_id="session-1",
    )
    conversation_id = created["conversation"]["id"]
    started = conversations.start_turn(conversation_id, {
        "user_content": "补充世界观",
        "creation_session_id": "session-1",
        "scope_type": "creation",
        "scope_id": "session-1",
    })
    assistant_id = started["messages"][1]["id"]
    trace = {"schema": "creation_agent_turn.v1", "replayable": True}

    conversations.finish_turn(conversation_id, assistant_id, {
        "assistant_content": "后台任务已开始",
        "status": "running",
        "payload": {"creation_agent_turn": trace, "run": {"status": "running"}},
    })
    finished = conversations.finish_turn(conversation_id, assistant_id, {
        "assistant_content": "后台任务已完成",
        "status": "completed",
        "payload": {"run": {"status": "completed"}},
    })

    assert finished["message"]["payload"]["creation_agent_turn"] == trace
    assert finished["message"]["payload"]["run"]["status"] == "completed"


def test_running_system_message_is_interrupted_after_restart():
    db = _db_session()
    conversations = SqlAlchemySystemConversationStore(db)
    created = asyncio.run(create_system_conversation(
        SystemConversationCreate(title="", scope_type="creation", scope_id="session-1"),
        conversations,
    ))
    conversation_id = created.data["conversation"]["id"]

    started = asyncio.run(start_system_turn(
        conversation_id,
        SystemTurnCreate(
            user_content="生成角色",
            run_id="creation-run-1",
            message_type="operation",
        ),
        conversations,
    ))
    assert started.data["messages"][1]["run_id"] == "creation-run-1"
    assert conversations.interrupt_running_messages() == 1

    detail = asyncio.run(get_system_conversation(conversation_id, conversations))
    assistant = detail.data["messages"][1]
    assert assistant["status"] == "interrupted"
    assert assistant["message_type"] == "operation"
    assert assistant["payload"]["retryable"] is True


def test_conversation_scope_can_follow_creation_and_project_contexts():
    db = _db_session()
    conversations = SqlAlchemySystemConversationStore(db)
    created = asyncio.run(create_system_conversation(
        SystemConversationCreate(title="立项讨论", scope_type="creation", scope_id="creation-1"),
        conversations,
    ))
    conversation_id = created.data["conversation"]["id"]
    assert created.data["conversation"]["scope_type"] == "creation"
    assert created.data["conversation"]["scope_id"] == "creation-1"

    changed = asyncio.run(set_system_conversation_scope(
        conversation_id,
        SystemConversationScopePatch(scope_type="project", scope_id="project-1"),
        conversations,
    ))
    assert changed.data["conversation"]["scope_type"] == "project"
    assert changed.data["conversation"]["scope_id"] == "project-1"
    assert changed.data["conversation"]["creation_session_id"] is None
    assert changed.data["conversation"]["project_id"] == "project-1"
    listing = asyncio.run(list_system_conversations(
        conversations,
        scope_type="project",
        scope_id="project-1",
    ))
    assert [item["id"] for item in listing.data["items"]] == [conversation_id]
