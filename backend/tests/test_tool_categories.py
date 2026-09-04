from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.architecture.tool_categories import (
    TOOL_CATEGORY_BY_NAME,
    TOOL_CATEGORY_CONTROLLER,
    TOOL_CATEGORY_METADATA,
    TOOL_NAMES_BY_CATEGORY,
    normalize_tool_categories,
    tool_names_for_categories,
)
from app.mcp.schemas import make_text_result
from app.mcp.server import handle_message
from app.modules.creation.interfaces.agent_progress import (
    creation_tool_completed_event,
    creation_tool_started_event,
)
from app.services.novel_creation_agent import _domain_tool_schemas, _tool_schemas
from app.services.tool_category_state import (
    activate_tool_categories,
    append_tool_category_audit,
    append_tool_category_event,
    create_tool_category_state,
    read_tool_category_audits,
    read_tool_category_events,
    read_tool_category_state,
    remove_tool_category_state,
    replace_tool_categories,
)
from app.services.workspace.registry import registry


def _schema_names(schemas: list[dict]) -> set[str]:
    return {str(item["function"]["name"]) for item in schemas}


def _mcp_names(state_file: str) -> set[str]:
    response = json.loads(handle_message(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}),
        permission_pack="creation_session",
        tool_category_state_file=state_file,
    ))
    return {item["name"] for item in response["result"]["tools"]}


def test_global_category_catalog_covers_registry_once_and_is_balanced():
    registered = set(registry.all_names())
    categorized = [name for names in TOOL_NAMES_BY_CATEGORY.values() for name in names]

    assert set(TOOL_CATEGORY_METADATA) == set(TOOL_NAMES_BY_CATEGORY)
    assert set(categorized) == registered
    assert len(categorized) == len(set(categorized)) == len(TOOL_CATEGORY_BY_NAME)
    sizes = [len(names) for names in TOOL_NAMES_BY_CATEGORY.values()]
    assert min(sizes) >= 16
    assert max(sizes) - min(sizes) <= 13


def test_category_selection_is_deduplicated_unbounded_replacement():
    assert normalize_tool_categories([
        "creation_data", "creation_data", "creation_flow", "story_knowledge",
    ]) == ("creation_data", "creation_flow", "story_knowledge")
    assert "patch_creation_entity" in tool_names_for_categories(["creation_data"])
    assert "finalize_creation_session" not in tool_names_for_categories(["creation_data"])
    assert "finalize_creation_session" in tool_names_for_categories(["creation_flow"])
    with pytest.raises(ValueError, match="未知工具类别"):
        normalize_tool_categories(["unknown"])
    with pytest.raises(ValueError, match="必须是数组"):
        normalize_tool_categories("creation_data")


def test_creation_agent_uses_global_category_intersection():
    domain_names = _schema_names(_domain_tool_schemas())
    assert _schema_names(_tool_schemas()) == {TOOL_CATEGORY_CONTROLLER}

    data_names = _schema_names(_tool_schemas(("creation_data",)))
    flow_names = _schema_names(_tool_schemas(("creation_flow",)))
    assert "get_creation_snapshot" in data_names
    assert "patch_creation_entity" in data_names
    assert "finalize_creation_session" not in data_names
    assert "finalize_creation_session" in flow_names
    assert "patch_creation_entity" not in flow_names
    assert data_names | flow_names <= domain_names | {TOOL_CATEGORY_CONTROLLER}


def test_process_scoped_mcp_activates_categories_at_next_step_boundary():
    state_file = create_tool_category_state()
    try:
        assert _mcp_names(state_file) == {TOOL_CATEGORY_CONTROLLER}
        controller = json.loads(handle_message(
            json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": TOOL_CATEGORY_CONTROLLER,
                    "arguments": {"enabled_categories": ["creation_data"]},
                },
            }),
            permission_pack="creation_session",
            tool_category_state_file=state_file,
        ))
        assert controller["result"]["isError"] is False
        state = read_tool_category_state(state_file)
        assert state["active_categories"] == []
        assert state["requested_categories"] == ["creation_data"]
        assert "patch_creation_session" not in _mcp_names(state_file)

        hidden = json.loads(handle_message(
            json.dumps({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "patch_creation_session", "arguments": {}},
            }),
            permission_pack="creation_session",
            tool_category_state_file=state_file,
        ))
        assert hidden["result"]["isError"] is True
        audits = read_tool_category_audits(state_file)
        assert [record["tool"] for record in audits] == [
            TOOL_CATEGORY_CONTROLLER,
            "patch_creation_session",
        ]
        assert audits[0]["arguments"] == {"enabled_categories": ["creation_data"]}
        assert audits[1]["status"] == "denied"

        activate_tool_categories(state_file)
        names = _mcp_names(state_file)
        assert "patch_creation_session" in names
        assert "finalize_creation_session" not in names
    finally:
        remove_tool_category_state(state_file)


def test_reselecting_active_categories_does_not_restart_the_model_step():
    state_file = create_tool_category_state()
    try:
        replace_tool_categories(state_file, ["story_knowledge", "writing_context"])
        activated = activate_tool_categories(state_file)
        version = activated["version"]

        repeated = json.loads(handle_message(
            json.dumps({
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": TOOL_CATEGORY_CONTROLLER,
                    "arguments": {
                        "enabled_categories": ["story_knowledge", "writing_context"]
                    },
                },
            }),
            permission_pack="creation_session",
            tool_category_state_file=state_file,
        ))

        assert repeated["result"]["isError"] is True
        assert "不要重复选择" in json.dumps(repeated, ensure_ascii=False)
        state = read_tool_category_state(state_file)
        assert state["version"] == version
        assert state["active_version"] == version
        assert state["active_categories"] == ["story_knowledge", "writing_context"]
    finally:
        remove_tool_category_state(state_file)


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable on this platform: {exc}")


def test_state_rewrite_does_not_follow_fixed_temporary_symlink(tmp_path: Path):
    state_file = create_tool_category_state()
    state_path = Path(state_file)
    legacy_temporary = state_path.with_suffix(".tmp")
    target = tmp_path / "do-not-overwrite.txt"
    original = b"author-private-content"
    target.write_bytes(original)
    _symlink_or_skip(legacy_temporary, target)
    target_path = target.resolve()
    try:
        activate_tool_categories(state_file)

        assert target.read_bytes() == original
        assert target.resolve() == target_path
        assert legacy_temporary.is_symlink()
        assert legacy_temporary.resolve() == target_path
    finally:
        legacy_temporary.unlink(missing_ok=True)
        remove_tool_category_state(state_file)


@pytest.mark.parametrize(
    ("name", "append"),
    [
        ("events.ndjson", lambda state: append_tool_category_event(
            state, {"type": "status", "message": "must-not-escape"}
        )),
        ("audit.ndjson", lambda state: append_tool_category_audit(
            state, {"tool": "probe", "status": "ok", "result": "must-not-escape"}
        )),
    ],
)
def test_state_mirror_append_does_not_follow_symlink(
    tmp_path: Path,
    name: str,
    append,
):
    state_file = create_tool_category_state()
    mirror_path = Path(state_file).parent / name
    target = tmp_path / f"{name}.target"
    original = b"author-private-content\n"
    target.write_bytes(original)
    mirror_path.unlink()
    _symlink_or_skip(mirror_path, target)
    target_path = target.resolve()
    try:
        append(state_file)

        assert target.read_bytes() == original
        assert target.resolve() == target_path
        assert mirror_path.is_symlink()
        assert mirror_path.resolve() == target_path
    finally:
        mirror_path.unlink(missing_ok=True)
        mirror_path.touch(mode=0o600)
        remove_tool_category_state(state_file)


def test_creation_mcp_allows_only_one_successful_write_per_user_turn():
    state_file = create_tool_category_state()
    executor = AsyncMock(return_value=make_text_result(json.dumps({
        "tool": "patch_creation_session",
        "status": "ok",
        "detail": "Creation session patched",
        "data": {"revision": 2},
    })))
    try:
        replace_response = json.loads(handle_message(
            json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": TOOL_CATEGORY_CONTROLLER,
                    "arguments": {"enabled_categories": ["creation_data"]},
                },
            }),
            permission_pack="creation_session",
            tool_category_state_file=state_file,
        ))
        assert replace_response["result"]["isError"] is False
        activate_tool_categories(state_file)

        with patch("app.mcp.server.execute_tool", new=executor):
            first = json.loads(handle_message(
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "patch_creation_session", "arguments": {"changes": {"genre": "玄幻"}}},
                }),
                db=object(),
                permission_pack="creation_session",
                creation_session_id="session-1",
                tool_category_state_file=state_file,
            ))
            second = json.loads(handle_message(
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "patch_creation_artifact", "arguments": {"artifact": "characters"}},
                }),
                db=object(),
                permission_pack="creation_session",
                creation_session_id="session-1",
                tool_category_state_file=state_file,
            ))

        assert first["result"]["isError"] is False
        assert second["result"]["isError"] is True
        assert "已经成功写入一次" in json.dumps(second, ensure_ascii=False)
        assert executor.await_count == 1
        state = read_tool_category_state(state_file)
        assert state["creation_turn"]["successful_writes"] == 1
        assert "patch_creation_session" not in _mcp_names(state_file)
        assert "get_creation_snapshot" in _mcp_names(state_file)
        audits = read_tool_category_audits(state_file)
        assert [row["status"] for row in audits[-2:]] == ["ok", "denied"]
        events, _ = read_tool_category_events(state_file)
        assert any(
            event.get("data", {}).get("turn_boundary") == "successful_write_limit"
            for event in events
        )
    finally:
        remove_tool_category_state(state_file)


def test_creation_mcp_stops_after_three_failed_write_attempts():
    state_file = create_tool_category_state()
    failed_result = make_text_result(json.dumps({
        "tool": "patch_creation_artifact",
        "status": "error",
        "detail": "invalid artifact payload",
    }), is_error=True)
    executor = AsyncMock(side_effect=[failed_result, failed_result, failed_result])
    try:
        handle_message(
            json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": TOOL_CATEGORY_CONTROLLER,
                    "arguments": {"enabled_categories": ["creation_data"]},
                },
            }),
            permission_pack="creation_session",
            tool_category_state_file=state_file,
        )
        activate_tool_categories(state_file)
        responses = []
        with patch("app.mcp.server.execute_tool", new=executor):
            for call_id in range(2, 6):
                responses.append(json.loads(handle_message(
                    json.dumps({
                        "jsonrpc": "2.0",
                        "id": call_id,
                        "method": "tools/call",
                        "params": {
                            "name": "patch_creation_artifact",
                            "arguments": {"artifact": "characters", "attempt": call_id},
                        },
                    }),
                    db=object(),
                    permission_pack="creation_session",
                    creation_session_id="session-1",
                    tool_category_state_file=state_file,
                )))

        assert executor.await_count == 3
        assert all(response["result"]["isError"] is True for response in responses)
        assert "写入已失败 3 次" in json.dumps(responses[-1], ensure_ascii=False)
        state = read_tool_category_state(state_file)
        assert state["creation_turn"]["failed_writes"] == 3
        events, _ = read_tool_category_events(state_file)
        assert any(
            event.get("data", {}).get("turn_boundary") == "failed_write_limit"
            for event in events
        )
    finally:
        remove_tool_category_state(state_file)


def test_safe_progress_projection_does_not_copy_raw_arguments_or_secrets():
    arguments = {
        "artifact": "characters",
        "instruction": "hidden internal prompt",
        "api_key": "sk-secret-value",
        "expected_revision": 12,
    }
    started = creation_tool_started_event("patch_creation_artifact", arguments)
    completed = creation_tool_completed_event(
        "patch_creation_artifact",
        arguments,
        {"status": "ok", "detail": "角色资料已更新", "data": {"revision": 13}},
    )
    wire = json.dumps([started, completed], ensure_ascii=False)
    assert "hidden internal prompt" not in wire
    assert "sk-secret-value" not in wire
    assert completed["data"]["revision_before"] == 12
    assert completed["data"]["revision_after"] == 13
