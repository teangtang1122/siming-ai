from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.schemas.context_governance import ContextSearchRequest
from app.services.context_orchestrator import ContextOrchestrator
from app.services.mobile_context_policy import portable_context_policy
from app.services.task_context_selection import (
    TASK_CONTEXT_SEARCH_EXCERPT_CHARS,
    TASK_CONTEXT_SEARCH_MAX_CURSOR,
    TASK_CONTEXT_SEARCH_PAGE_LIMIT,
    TASK_CONTEXT_SEARCH_SOURCE_TYPES,
)
from app.services.workspace.registry import registry
from app.services.workspace.tools.context_governance import search_task_context


def _search_page(total: int, *, cursor: int = 0) -> dict:
    manifest = SimpleNamespace(id="manifest-1", status="ready")
    candidates = [{"item_id": f"item-{index}"} for index in range(total)]
    orchestrator = MagicMock()
    orchestrator.get_manifest.return_value = manifest
    orchestrator.validate.return_value = (True, "")

    def retrieve(_manifest, *, limit, offset, include_next_probe, **_kwargs):
        assert limit == TASK_CONTEXT_SEARCH_PAGE_LIMIT
        assert 0 <= offset <= TASK_CONTEXT_SEARCH_MAX_CURSOR
        fetch_size = limit + int(include_next_probe)
        return candidates[offset : offset + fetch_size]

    orchestrator.search_task_context.side_effect = retrieve
    with patch(
        "app.services.workspace.tools.context_governance.ContextOrchestrator",
        return_value=orchestrator,
    ):
        return asyncio.run(
            search_task_context(
                MagicMock(),
                "project-1",
                {
                    "context_manifest_id": manifest.id,
                    "query": "需要哪些资料",
                    "limit": TASK_CONTEXT_SEARCH_PAGE_LIMIT,
                    "cursor": cursor,
                },
            )
        )


def test_exactly_twenty_results_stop_on_the_second_full_page() -> None:
    first = _search_page(20)
    second = _search_page(20, cursor=10)

    assert len(first["data"]["items"]) == 10
    assert first["data"]["page"] == {
        "cursor": 0,
        "limit": 10,
        "next_cursor": 10,
        "has_more": True,
    }
    assert len(second["data"]["items"]) == 10
    assert second["data"]["page"] == {
        "cursor": 10,
        "limit": 10,
        "next_cursor": None,
        "has_more": False,
    }


def test_twenty_one_results_expose_the_real_final_page() -> None:
    second = _search_page(21, cursor=10)
    final = _search_page(21, cursor=20)

    assert second["data"]["page"]["next_cursor"] == 20
    assert second["data"]["page"]["has_more"] is True
    assert [item["item_id"] for item in final["data"]["items"]] == ["item-20"]
    assert final["data"]["page"]["next_cursor"] is None
    assert final["data"]["page"]["has_more"] is False


def test_unsupported_source_types_fail_explicitly_instead_of_silently_narrowing() -> None:
    manifest = SimpleNamespace(id="manifest-1", status="ready")
    orchestrator = MagicMock()
    orchestrator.get_manifest.return_value = manifest
    orchestrator.validate.return_value = (True, "")
    with patch(
        "app.services.workspace.tools.context_governance.ContextOrchestrator",
        return_value=orchestrator,
    ):
        result = asyncio.run(
            search_task_context(
                MagicMock(),
                "project-1",
                {
                    "context_manifest_id": manifest.id,
                    "query": "罗建群",
                    "source_types": ["worldbuilding", "characters"],
                },
            )
        )

    assert result["status"] == "skipped"
    assert "characters" in result["detail"]
    assert result["data"]["supported_source_types"] == sorted(
        TASK_CONTEXT_SEARCH_SOURCE_TYPES
    )
    orchestrator.search_task_context.assert_not_called()


def test_task_context_search_contract_has_one_pc_authority() -> None:
    parameter = inspect.signature(ContextOrchestrator.search_task_context).parameters["limit"]
    assert parameter.default == TASK_CONTEXT_SEARCH_PAGE_LIMIT == 10
    assert TASK_CONTEXT_SEARCH_MAX_CURSOR == 20
    assert TASK_CONTEXT_SEARCH_EXCERPT_CHARS == 600

    schema = registry.get("search_task_context").input_schema
    assert schema["limit"] | {} == {
        "type": "integer",
        "minimum": 1,
        "maximum": 10,
        "default": 10,
        "description": "Maximum short candidates on this search page; default/max 10.",
    }
    assert schema["cursor"]["minimum"] == 0
    assert schema["cursor"]["maximum"] == 20
    assert schema["cursor"]["default"] == 0
    assert portable_context_policy("writing")["selection"]["search_page_limit"] == 10

    request = ContextSearchRequest(query="资料")
    assert request.limit == 10
    assert request.cursor == 0
    with pytest.raises(ValidationError):
        ContextSearchRequest(query="资料", limit=11)
