"""Tests for persisted system assistant conversations."""
from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.session import Base
from app.modules.assistant.infrastructure.system_conversations import (
    SqlAlchemySystemConversationStore,
)
from app.routers.system_assistant import (
    SystemConversationCreate,
    SystemTurnCreate,
    append_system_turn,
    create_system_conversation,
    get_system_conversation,
    list_system_conversations,
)


def _db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_system_conversation_persists_messages_and_blueprint_state():
    db = _db_session()
    conversations = SqlAlchemySystemConversationStore(db)
    created = asyncio.run(create_system_conversation(
        SystemConversationCreate(title="克苏鲁新书"),
        conversations,
    ))
    conversation_id = created.data["conversation"]["id"]

    blueprints = [{
        "title": "规则怪谈：别替旧神签收",
        "protagonist": {"name": "林雾白"},
    }]
    asyncio.run(append_system_turn(
        conversation_id,
        SystemTurnCreate(
            user_content="帮我创建一本克苏鲁规则怪谈",
            assistant_content="已生成三个方案",
            creation_session_id="session-1",
            user_brief="克苏鲁+规则怪谈",
            blueprints=blueprints,
        ),
        conversations,
    ))

    detail = asyncio.run(get_system_conversation(conversation_id, conversations))
    assert detail.data["conversation"]["creation_session_id"] == "session-1"
    assert detail.data["conversation"]["blueprints"] == blueprints
    assert [item["role"] for item in detail.data["messages"]] == ["user", "assistant"]
    assert detail.data["messages"][1]["content"] == "已生成三个方案"

    listing = asyncio.run(list_system_conversations(conversations))
    assert listing.data["total"] == 1
    assert listing.data["items"][0]["message_count"] == 2
