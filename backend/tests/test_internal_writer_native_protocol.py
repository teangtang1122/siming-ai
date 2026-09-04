"""Internal structured generators must never downgrade tool calls to text JSON."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base,
    Character,
    OutlineDraft,
    OutlineNode,
    Project,
    WorldbuildingEntry,
)
from app.prompts.character_writer_prompts import CHARACTER_WRITER_SYSTEM_BASE
from app.prompts.outline_writer_prompts import OUTLINE_WRITER_SYSTEM
from app.prompts.plot_prompts import PLOT_DESIGN_SYSTEM
from app.prompts.worldbuilding_writer_prompts import WORLDBUILDING_WRITER_SYSTEM_BASE
from app.services.workspace.tools.character_writer import character_writer
from app.services.workspace.tools.native_structured_output import (
    NativeStructuredOutputError,
    required_tool_arguments,
)
from app.services.workspace.tools.plot import design_plot
from app.services.workspace.tools.worldbuilding_writer import worldbuilding_writer


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Project(id="p1", title="Native protocol project"))
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("tool_calls", "reason"),
    [
        ([], "required_native_tool_call_missing_or_ambiguous"),
        (
            [{"function": {"name": "expected", "arguments": "{}"}}],
            "native_tool_call_id_missing",
        ),
        (
            [{"id": "call-1", "function": {"arguments": "{}"}}],
            "native_tool_name_missing",
        ),
        (
            [
                {
                    "id": "call-1",
                    "function": {"name": "expected", "arguments": "not-json"},
                }
            ],
            "native_tool_arguments_invalid_json",
        ),
        (
            [
                {
                    "id": "call-1",
                    "function": {"name": "expected", "arguments": "[]"},
                }
            ],
            "native_tool_arguments_not_object",
        ),
    ],
)
def test_required_tool_arguments_rejects_incomplete_native_calls(
    tool_calls,
    reason: str,
) -> None:
    with pytest.raises(NativeStructuredOutputError, match=reason) as exc_info:
        required_tool_arguments(
            {"content": '{"looks":"valid"}', "tool_calls": tool_calls},
            expected_name="expected",
        )

    assert exc_info.value.reason == reason


@pytest.mark.parametrize(
    ("prompt", "required_tool"),
    [
        (CHARACTER_WRITER_SYSTEM_BASE, "create_character"),
        (WORLDBUILDING_WRITER_SYSTEM_BASE, "create_worldbuilding_entry"),
        (OUTLINE_WRITER_SYSTEM, "propose_outline_nodes"),
        (PLOT_DESIGN_SYSTEM, "design_plot_output"),
    ],
)
def test_internal_writer_prompts_require_native_tools_without_text_json_fallback(
    prompt: str,
    required_tool: str,
) -> None:
    assert required_tool in prompt
    for forbidden in (
        "无法使用函数调用",
        "不能调用函数",
        "只输出严格 JSON",
        "只输出 JSON 对象",
    ):
        assert forbidden not in prompt


@pytest.mark.parametrize(
    ("module", "writer", "arguments", "content", "tool_name"),
    [
        (
            "character_writer",
            character_writer,
            {},
            {
                "name": "Text-only character",
                "appearance": "appearance",
                "personality": "personality",
                "background": "background",
                "abilities": [],
                "role_type": "other",
                "design_notes": "ordinary content",
            },
            "character_writer",
        ),
        (
            "worldbuilding_writer",
            worldbuilding_writer,
            {},
            {
                "title": "Text-only world",
                "content": "content",
                "dimension": "culture",
                "plot_usage": "usage",
                "design_notes": "ordinary content",
            },
            "worldbuilding_writer",
        ),
        (
            "plot",
            design_plot,
            {},
            {
                "scenes": [],
                "conflicts": {"type": "inner", "description": "conflict"},
                "emotional_arc": {"start": "low", "end": "high"},
                "engagement_assessment": {},
                "summary": "Text-only plot",
            },
            "design_plot",
        ),
    ],
)
def test_internal_writer_rejects_json_content_without_native_call_or_writes(
    db,
    module: str,
    writer,
    arguments: dict,
    content: dict,
    tool_name: str,
) -> None:
    before = {
        "characters": db.query(Character).count(),
        "worldbuilding": db.query(WorldbuildingEntry).count(),
        "outlines": db.query(OutlineNode).count(),
        "outline_drafts": db.query(OutlineDraft).count(),
    }
    with patch(
        f"app.services.workspace.tools.{module}.LLMGateway.chat_completion",
        new_callable=AsyncMock,
    ) as completion:
        completion.return_value = {
            "content": json.dumps(content),
            "tool_calls": [],
        }
        result = asyncio.run(writer(db, "p1", arguments))

    assert result["tool"] == tool_name
    assert result["status"] == "error"
    assert (
        result["data"]["protocol_error"]
        == "required_native_tool_call_missing_or_ambiguous"
    )
    assert db.query(Character).count() == before["characters"]
    assert db.query(WorldbuildingEntry).count() == before["worldbuilding"]
    assert db.query(OutlineNode).count() == before["outlines"]
    assert db.query(OutlineDraft).count() == before["outline_drafts"]
    assert not db.new
    assert not db.dirty
