"""Per-user-turn category state shared with temporary MCP processes."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from app.architecture.tool_categories import (
    TOOL_CATEGORY_CONTROLLER,
    TOOL_CATEGORY_METADATA,
    normalize_tool_categories,
)
from app.modules.creation.interfaces.agent_scope import (
    CREATION_AGENT_WRITE_TOOL_NAMES,
    CREATION_TURN_MAX_FAILED_WRITES,
    CREATION_TURN_MAX_SUCCESSFUL_WRITES,
    CREATION_WRITE_SUCCESS_STATUSES,
    creation_turn_write_denial,
    creation_turn_writes_closed,
)

TOOL_CATEGORY_STATE_SCHEMA = "tool_categories.v2"
_STATE_DIR_PREFIX = "siming-tool-categories-"
_STATE_FILE_NAME = "state.json"
_EVENTS_FILE_NAME = "events.ndjson"
_AUDIT_FILE_NAME = "audit.ndjson"
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
_OPEN_COMMON_FLAGS = (
    getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_NOINHERIT", 0)
)
_NOFOLLOW_FLAG = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY_FLAG = getattr(os, "O_DIRECTORY", 0)
_DIR_FD_SUPPORTED = all(
    function in os.supports_dir_fd
    for function in (os.open, os.rename, os.stat, os.unlink)
)


def _is_link_or_reparse(value: os.stat_result) -> bool:
    attributes = int(getattr(value, "st_file_attributes", 0) or 0)
    return stat.S_ISLNK(value.st_mode) or bool(attributes & _REPARSE_POINT)


def _identity(value: os.stat_result) -> tuple[int, int]:
    return int(value.st_dev), int(value.st_ino)


def _safe_directory_stat(path: Path) -> os.stat_result:
    value = os.lstat(path)
    if _is_link_or_reparse(value) or not stat.S_ISDIR(value.st_mode):
        raise ValueError("工具类别状态目录包含不安全的链接或重解析点")
    return value


def _safe_regular_stat(value: os.stat_result) -> os.stat_result:
    if (
        _is_link_or_reparse(value)
        or not stat.S_ISREG(value.st_mode)
        or int(value.st_nlink) != 1
    ):
        raise ValueError("工具类别状态文件包含不安全的链接或重解析点")
    return value


def _open_state_directory(path: Path) -> tuple[int | None, os.stat_result]:
    before = _safe_directory_stat(path.parent)
    if not _DIR_FD_SUPPORTED:
        return None, before
    descriptor = os.open(
        path.parent,
        os.O_RDONLY | _DIRECTORY_FLAG | _NOFOLLOW_FLAG | _OPEN_COMMON_FLAGS,
    )
    try:
        opened = _safe_directory_stat_from_descriptor(descriptor)
        if _identity(opened) != _identity(before):
            raise ValueError("工具类别状态目录在打开期间发生变化")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, opened


def _safe_directory_stat_from_descriptor(descriptor: int) -> os.stat_result:
    value = os.fstat(descriptor)
    if _is_link_or_reparse(value) or not stat.S_ISDIR(value.st_mode):
        raise ValueError("工具类别状态目录包含不安全的链接或重解析点")
    return value


def _verify_state_directory(
    path: Path,
    expected: os.stat_result,
    descriptor: int | None,
) -> None:
    if descriptor is not None:
        opened = _safe_directory_stat_from_descriptor(descriptor)
        if _identity(opened) != _identity(expected):
            raise ValueError("工具类别状态目录在操作期间发生变化")
    current = _safe_directory_stat(path.parent)
    if _identity(current) != _identity(expected):
        raise ValueError("工具类别状态目录在操作期间发生变化")


def _sibling_stat(
    path: Path,
    name: str,
    directory_descriptor: int | None,
) -> os.stat_result:
    if directory_descriptor is not None:
        return os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    return os.lstat(path.parent / name)


def _open_sibling(
    path: Path,
    name: str,
    flags: int,
    directory_descriptor: int | None,
    *,
    mode: int | None = None,
) -> int:
    target: str | Path = name if directory_descriptor is not None else path.parent / name
    kwargs = {"dir_fd": directory_descriptor} if directory_descriptor is not None else {}
    if mode is None:
        return os.open(target, flags | _NOFOLLOW_FLAG | _OPEN_COMMON_FLAGS, **kwargs)
    return os.open(
        target,
        flags | _NOFOLLOW_FLAG | _OPEN_COMMON_FLAGS,
        mode,
        **kwargs,
    )


def _unlink_sibling(
    path: Path,
    name: str,
    directory_descriptor: int | None,
) -> None:
    try:
        if directory_descriptor is not None:
            os.unlink(name, dir_fd=directory_descriptor)
        else:
            os.unlink(path.parent / name)
    except FileNotFoundError:
        pass


def _replace_sibling(
    path: Path,
    source_name: str,
    target_name: str,
    directory_descriptor: int | None,
) -> None:
    if directory_descriptor is not None:
        os.rename(
            source_name,
            target_name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        return
    os.replace(path.parent / source_name, path.parent / target_name)


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("工具类别状态文件写入失败")
        view = view[written:]


def _safe_existing_sibling_stat(
    path: Path,
    name: str,
    directory_descriptor: int | None,
) -> os.stat_result:
    return _safe_regular_stat(_sibling_stat(path, name, directory_descriptor))


def _read_sibling_text(path: Path, name: str) -> str:
    directory_descriptor, directory_stat = _open_state_directory(path)
    file_descriptor: int | None = None
    try:
        before = _safe_existing_sibling_stat(path, name, directory_descriptor)
        file_descriptor = _open_sibling(
            path,
            name,
            os.O_RDONLY,
            directory_descriptor,
        )
        opened = _safe_regular_stat(os.fstat(file_descriptor))
        if _identity(opened) != _identity(before):
            raise ValueError("工具类别状态文件在打开期间发生变化")
        with os.fdopen(file_descriptor, "r", encoding="utf-8") as handle:
            file_descriptor = None
            result = handle.read()
        after = _safe_existing_sibling_stat(path, name, directory_descriptor)
        if _identity(after) != _identity(opened):
            raise ValueError("工具类别状态文件在读取期间发生变化")
        _verify_state_directory(path, directory_stat, directory_descriptor)
        return result
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if directory_descriptor is not None:
            os.close(directory_descriptor)


def _append_sibling_text(path: Path, name: str, payload: str) -> None:
    directory_descriptor, directory_stat = _open_state_directory(path)
    file_descriptor: int | None = None
    try:
        before = _safe_existing_sibling_stat(path, name, directory_descriptor)
        file_descriptor = _open_sibling(
            path,
            name,
            os.O_WRONLY | os.O_APPEND,
            directory_descriptor,
        )
        opened = _safe_regular_stat(os.fstat(file_descriptor))
        if _identity(opened) != _identity(before):
            raise ValueError("工具类别状态文件在打开期间发生变化")
        _write_all(file_descriptor, payload.encode("utf-8"))
        after = _safe_existing_sibling_stat(path, name, directory_descriptor)
        if _identity(after) != _identity(opened):
            raise ValueError("工具类别状态文件在追加期间发生变化")
        _verify_state_directory(path, directory_stat, directory_descriptor)
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if directory_descriptor is not None:
            os.close(directory_descriptor)


def _create_empty_sibling(path: Path, name: str) -> None:
    directory_descriptor, directory_stat = _open_state_directory(path)
    file_descriptor: int | None = None
    try:
        file_descriptor = _open_sibling(
            path,
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            directory_descriptor,
            mode=0o600,
        )
        opened = _safe_regular_stat(os.fstat(file_descriptor))
        after = _safe_existing_sibling_stat(path, name, directory_descriptor)
        if _identity(after) != _identity(opened):
            raise ValueError("工具类别状态文件在创建期间发生变化")
        _verify_state_directory(path, directory_stat, directory_descriptor)
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if directory_descriptor is not None:
            os.close(directory_descriptor)


def create_tool_category_state() -> str:
    root = Path(tempfile.mkdtemp(prefix=_STATE_DIR_PREFIX))
    path = root / _STATE_FILE_NAME
    try:
        _write_state(path, {
            "schema": TOOL_CATEGORY_STATE_SCHEMA,
            "version": 0,
            "active_version": 0,
            "active_categories": [],
            "requested_categories": [],
            "creation_turn": {
                "successful_writes": 0,
                "failed_writes": 0,
                "write_limit": CREATION_TURN_MAX_SUCCESSFUL_WRITES,
                "failed_write_limit": CREATION_TURN_MAX_FAILED_WRITES,
                "last_write_tool": "",
                "last_write_status": "",
            },
        })
        _create_empty_sibling(path, _EVENTS_FILE_NAME)
        _create_empty_sibling(path, _AUDIT_FILE_NAME)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return str(path)


def bind_tool_category_turn_guard(path: str, guard: dict[str, Any]) -> dict[str, Any]:
    """Bind a temporary MCP surface to one durable running Agent turn."""

    state_path = _validated_path(path)
    if state_path is None:
        raise ValueError("工具类别状态文件无效")
    state = read_tool_category_state(path)
    kind = str(guard.get("kind") or "").strip()
    normalized = {
        str(key): str(value).strip()
        for key, value in guard.items()
        if value is not None and str(value).strip()
    }
    if kind == "workspace":
        required = {"kind", "project_id", "conversation_id", "run_id"}
    elif kind == "creation":
        required = {
            "kind",
            "session_id",
            "conversation_id",
            "assistant_message_id",
        }
    else:
        raise ValueError("工具类别 turn guard 类型无效")
    if not required.issubset(normalized):
        raise ValueError("工具类别 turn guard 缺少必要身份")
    state["turn_guard"] = normalized
    _write_state(state_path, state)
    return normalized


def _validated_path(path: str) -> Path | None:
    raw = str(path or "").strip()
    if not raw:
        return None
    candidate = Path(os.path.abspath(raw))
    if (
        candidate.name != _STATE_FILE_NAME
        or not candidate.parent.name.startswith(_STATE_DIR_PREFIX)
    ):
        return None
    temp_root = Path(os.path.abspath(tempfile.gettempdir()))
    if os.path.normcase(str(candidate.parent.parent)) != os.path.normcase(str(temp_root)):
        return None
    try:
        _safe_directory_stat(candidate.parent)
    except (OSError, ValueError):
        return None
    return candidate


def _write_state(path: Path, state: dict[str, Any]) -> None:
    state_path = _validated_path(str(path))
    if state_path is None:
        raise ValueError("工具类别状态文件无效")
    payload = json.dumps(state, ensure_ascii=False).encode("utf-8")
    directory_descriptor, directory_stat = _open_state_directory(state_path)
    temporary_name = f".state-{secrets.token_hex(16)}.tmp"
    temporary_descriptor: int | None = None
    replaced = False
    try:
        with suppress(FileNotFoundError):
            _safe_existing_sibling_stat(
                state_path,
                _STATE_FILE_NAME,
                directory_descriptor,
            )
        temporary_descriptor = _open_sibling(
            state_path,
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            directory_descriptor,
            mode=0o600,
        )
        temporary_stat = _safe_regular_stat(os.fstat(temporary_descriptor))
        _write_all(temporary_descriptor, payload)
        os.fsync(temporary_descriptor)
        temporary_path_stat = _safe_existing_sibling_stat(
            state_path,
            temporary_name,
            directory_descriptor,
        )
        if _identity(temporary_path_stat) != _identity(temporary_stat):
            raise ValueError("工具类别临时状态文件在写入期间发生变化")
        _verify_state_directory(state_path, directory_stat, directory_descriptor)
        with suppress(FileNotFoundError):
            _safe_existing_sibling_stat(
                state_path,
                _STATE_FILE_NAME,
                directory_descriptor,
            )

        # Windows cannot reliably replace an open file through the CRT. The
        # random O_EXCL name plus pre/post reparse checks keeps that path
        # fail-closed there; POSIX retains the descriptor across rename.
        if os.name == "nt":
            os.close(temporary_descriptor)
            temporary_descriptor = None
            temporary_path_stat = _safe_existing_sibling_stat(
                state_path,
                temporary_name,
                directory_descriptor,
            )
            if _identity(temporary_path_stat) != _identity(temporary_stat):
                raise ValueError("工具类别临时状态文件在替换前发生变化")

        _replace_sibling(
            state_path,
            temporary_name,
            _STATE_FILE_NAME,
            directory_descriptor,
        )
        replaced = True
        final_stat = _safe_existing_sibling_stat(
            state_path,
            _STATE_FILE_NAME,
            directory_descriptor,
        )
        if _identity(final_stat) != _identity(temporary_stat):
            raise ValueError("工具类别状态文件在原子替换期间发生变化")
        _verify_state_directory(state_path, directory_stat, directory_descriptor)
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        if not replaced:
            _unlink_sibling(state_path, temporary_name, directory_descriptor)
        if directory_descriptor is not None:
            os.close(directory_descriptor)


def read_tool_category_state(path: str) -> dict[str, Any]:
    state_path = _validated_path(path)
    if state_path is None:
        raise ValueError("工具类别状态文件无效")
    try:
        value = json.loads(_read_sibling_text(state_path, _STATE_FILE_NAME))
    except FileNotFoundError as exc:
        raise ValueError("工具类别状态文件无效") from exc
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("工具类别状态文件不可读") from exc
    if not isinstance(value, dict) or value.get("schema") != TOOL_CATEGORY_STATE_SCHEMA:
        raise ValueError("工具类别状态文件契约不匹配")
    value["active_categories"] = list(
        normalize_tool_categories(value.get("active_categories") or [])
    )
    value["requested_categories"] = list(
        normalize_tool_categories(
            value.get("requested_categories", value.get("active_categories")) or [],
        )
    )
    value["version"] = int(value.get("version") or 0)
    value["active_version"] = int(value.get("active_version") or 0)
    raw_turn = value.get("creation_turn")
    turn = dict(raw_turn) if isinstance(raw_turn, dict) else {}
    turn["successful_writes"] = max(0, int(turn.get("successful_writes") or 0))
    turn["failed_writes"] = max(0, int(turn.get("failed_writes") or 0))
    turn["write_limit"] = CREATION_TURN_MAX_SUCCESSFUL_WRITES
    turn["failed_write_limit"] = CREATION_TURN_MAX_FAILED_WRITES
    turn["last_write_tool"] = str(turn.get("last_write_tool") or "")
    turn["last_write_status"] = str(turn.get("last_write_status") or "")
    value["creation_turn"] = turn
    raw_guard = value.get("turn_guard")
    value["turn_guard"] = dict(raw_guard) if isinstance(raw_guard, dict) else None
    return value


def creation_turn_write_denial_for_state(
    path: str,
    tool_name: str,
) -> dict[str, Any] | None:
    """Reject a creation mutation after this user turn's budget is closed."""

    state = read_tool_category_state(path)
    turn = state["creation_turn"]
    return creation_turn_write_denial(
        tool_name,
        successful_writes=int(turn["successful_writes"]),
        failed_writes=int(turn["failed_writes"]),
    )


def creation_turn_write_tools_closed(state: dict[str, Any]) -> bool:
    turn = state.get("creation_turn") if isinstance(state.get("creation_turn"), dict) else {}
    return creation_turn_writes_closed(
        successful_writes=max(0, int(turn.get("successful_writes") or 0)),
        failed_writes=max(0, int(turn.get("failed_writes") or 0)),
    )


def record_creation_turn_write_result(
    path: str,
    tool_name: str,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist one executed creation-write outcome and return a stop event."""

    if tool_name not in CREATION_AGENT_WRITE_TOOL_NAMES:
        return None
    state_path = _validated_path(path)
    if state_path is None:
        raise ValueError("工具类别状态文件无效")
    state = read_tool_category_state(path)
    turn = state["creation_turn"]
    status = str(result.get("status") or "error")
    if status in CREATION_WRITE_SUCCESS_STATUSES:
        turn["successful_writes"] = int(turn["successful_writes"]) + 1
    else:
        turn["failed_writes"] = int(turn["failed_writes"]) + 1
    turn["last_write_tool"] = tool_name
    turn["last_write_status"] = status
    state["creation_turn"] = turn
    _write_state(state_path, state)

    failed_writes = int(turn["failed_writes"])
    if (
        status not in CREATION_WRITE_SUCCESS_STATUSES
        and failed_writes >= CREATION_TURN_MAX_FAILED_WRITES
    ):
        return {
            "type": "tool_completed",
            "message": "写入连续失败已达上限，本轮已停止自动重试",
            "data": {
                "tool": tool_name,
                "status": "denied",
                "turn_boundary": "failed_write_limit",
                "failed_writes": failed_writes,
            },
        }
    return None


def remove_tool_category_state(path: str) -> None:
    state_path = _validated_path(path)
    if state_path is None:
        return
    try:
        read_tool_category_state(path)
    except ValueError:
        return
    shutil.rmtree(state_path.parent, ignore_errors=True)


def replace_tool_categories(path: str, value: Any) -> dict[str, Any]:
    state_path = _validated_path(path)
    if state_path is None:
        raise ValueError("工具类别状态文件无效")
    state = read_tool_category_state(path)
    categories = normalize_tool_categories(value)
    active_categories = normalize_tool_categories(state.get("active_categories") or [])
    requested_categories = normalize_tool_categories(
        state.get("requested_categories") or []
    )
    category_change_pending = int(state.get("active_version") or 0) < int(
        state.get("version") or 0
    )
    if categories == active_categories and not category_change_pending:
        labels = [TOOL_CATEGORY_METADATA[category]["label"] for category in categories]
        return {
            "tool": TOOL_CATEGORY_CONTROLLER,
            "status": "skipped",
            "detail": (
                f"{'、'.join(labels)}能力已经开放；不要重复选择，"
                "请直接调用当前业务工具完成任务"
                if labels
                else "业务工具已经关闭；如无需工具，请直接回复作者"
            ),
            "data": {
                "enabled_categories": list(categories),
                "labels": labels,
                "reason": "categories_already_active",
            },
        }
    if categories == requested_categories and category_change_pending:
        labels = [TOOL_CATEGORY_METADATA[category]["label"] for category in categories]
        return {
            "tool": TOOL_CATEGORY_CONTROLLER,
            "status": "ok",
            "detail": f"已准备{'、'.join(labels)}能力" if labels else "已关闭全部业务工具",
            "data": {"enabled_categories": list(categories), "labels": labels},
        }
    state["version"] = int(state.get("version") or 0) + 1
    state["requested_categories"] = list(categories)
    _write_state(state_path, state)
    labels = [TOOL_CATEGORY_METADATA[category]["label"] for category in categories]
    detail = f"已准备{'、'.join(labels)}能力" if labels else "已关闭全部业务工具"
    event = {
        "type": "tool_categories_changed",
        "message": detail,
        "data": {"enabled_categories": list(categories), "labels": labels},
    }
    append_tool_category_event(path, event)
    return {
        "tool": TOOL_CATEGORY_CONTROLLER,
        "status": "ok",
        "detail": detail,
        "data": event["data"],
    }


def activate_tool_categories(path: str) -> dict[str, Any]:
    state_path = _validated_path(path)
    if state_path is None:
        raise ValueError("工具类别状态文件无效")
    state = read_tool_category_state(path)
    state["active_categories"] = list(normalize_tool_categories(
        state.get("requested_categories") or [],
    ))
    state["active_version"] = int(state.get("version") or 0)
    _write_state(state_path, state)
    return state


def append_tool_category_event(path: str, event: dict[str, Any]) -> None:
    state_path = _validated_path(path)
    if state_path is None:
        return
    try:
        read_tool_category_state(path)
    except ValueError:
        return
    try:
        _append_sibling_text(
            state_path,
            _EVENTS_FILE_NAME,
            json.dumps(event, ensure_ascii=False, default=str) + "\n",
        )
    except (OSError, ValueError):
        return


def append_tool_category_audit(path: str, record: dict[str, Any]) -> None:
    state_path = _validated_path(path)
    if state_path is None:
        return
    try:
        read_tool_category_state(path)
    except ValueError:
        return
    encoded = json.dumps(record, ensure_ascii=False, default=str)
    if len(encoded) > 500_000:
        encoded = json.dumps({
            "tool": record.get("tool"),
            "status": record.get("status"),
            "truncated": True,
            "encoded_preview": encoded[:500_000],
        }, ensure_ascii=False)
    try:
        _append_sibling_text(state_path, _AUDIT_FILE_NAME, encoded + "\n")
    except (OSError, ValueError):
        return


def _read_ndjson(path: str, name: str) -> list[dict[str, Any]]:
    state_path = _validated_path(path)
    if state_path is None:
        return []
    try:
        rows = _read_sibling_text(state_path, name).splitlines()
    except (OSError, UnicodeError, ValueError):
        return []
    values: list[dict[str, Any]] = []
    for row in rows:
        try:
            value = json.loads(row)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


def read_tool_category_events(path: str, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    state_path = _validated_path(path)
    if state_path is None:
        return [], offset
    try:
        encoded = _read_sibling_text(state_path, _EVENTS_FILE_NAME).encode("utf-8")
        safe_offset = max(offset, 0)
        rows = encoded[safe_offset:].decode("utf-8").splitlines()
        next_offset = max(safe_offset, len(encoded))
    except (OSError, UnicodeError, ValueError):
        return [], offset
    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            value = json.loads(row)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("type"), str):
            events.append(value)
    return events, next_offset


def read_tool_category_audits(path: str) -> list[dict[str, Any]]:
    return [row for row in _read_ndjson(path, "audit.ndjson") if isinstance(row.get("tool"), str)]


__all__ = [
    "TOOL_CATEGORY_STATE_SCHEMA",
    "activate_tool_categories",
    "append_tool_category_audit",
    "append_tool_category_event",
    "creation_turn_write_denial_for_state",
    "creation_turn_write_tools_closed",
    "create_tool_category_state",
    "read_tool_category_audits",
    "read_tool_category_events",
    "read_tool_category_state",
    "remove_tool_category_state",
    "replace_tool_categories",
    "record_creation_turn_write_result",
]
