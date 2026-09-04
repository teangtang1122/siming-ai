"""Durable RunStep boundary for one workspace-scoped Direct-MCP call.

The temporary category audit is useful telemetry, but the local Agent process
can see its temporary directory.  A workspace write is therefore proven only
by the server-side RunStep created here around the actual MCP dispatch.  The
bound run/project/conversation guard is revalidated independently before a
business handler can run.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.architecture.uow import commit_session
from app.database.models import AssistantConversation, AssistantRun, AssistantRunStep
from app.modules.operations.infrastructure.models import OperationRun
from app.services.tool_category_state import read_tool_category_state

from .idempotency import generate_idempotency_key
from .run_log import start_run_step
from .run_step_payloads import UnrecoverableStepRequest, deserialize_step_request

DIRECT_MCP_CALL_KEY = "_siming_direct_mcp_call_key"
_EXTERNAL_MCP_ROUTE = "external_mcp"
_MIN_LEASE_TOKEN_CHARS = 32


class WorkspaceDirectMcpRunLogError(ValueError):
    """Raised before dispatch when a scoped MCP call cannot be durably logged."""


@dataclass(frozen=True)
class WorkspaceDirectMcpStepStart:
    step: AssistantRunStep
    replay_result: dict[str, Any] | None = None

    @property
    def replayed(self) -> bool:
        return self.replay_result is not None


def _lease_hash(lease_token: str) -> str:
    token = str(lease_token or "").strip()
    if len(token) < _MIN_LEASE_TOKEN_CHARS:
        raise WorkspaceDirectMcpRunLogError("Direct MCP lease 无效")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_workspace_direct_mcp_lease(
    db: Session,
    run: AssistantRun,
    *,
    iteration: int,
) -> str:
    """Rotate one opaque, DB-owned lease before starting an MCP subprocess."""

    if not run.conversation_id:
        raise WorkspaceDirectMcpRunLogError("Direct MCP 回合已失效")
    normalized_iteration = max(0, int(iteration))
    token = secrets.token_urlsafe(32)
    operation_updated = (
        db.query(OperationRun)
        .filter(
            OperationRun.id == run.operation_id,
            OperationRun.source_kind == "assistant",
            OperationRun.source_id == run.id,
            OperationRun.project_id == run.project_id,
            OperationRun.status == "running",
        )
        .update(
            {OperationRun.updated_at: datetime.utcnow()},
            synchronize_session=False,
        )
    )
    if operation_updated != 1:
        db.rollback()
        raise WorkspaceDirectMcpRunLogError("Direct MCP operation 已失效")
    updated = (
        db.query(AssistantRun)
        .filter(
            AssistantRun.id == run.id,
            AssistantRun.project_id == run.project_id,
            AssistantRun.conversation_id == run.conversation_id,
            AssistantRun.conversation_id.in_(
                db.query(AssistantConversation.id).filter(
                    AssistantConversation.project_id == run.project_id,
                    AssistantConversation.scope == "project",
                )
            ),
            AssistantRun.status == "running",
        )
        .update(
            {
                AssistantRun.direct_mcp_lease_hash: _lease_hash(token),
                AssistantRun.direct_mcp_lease_iteration: normalized_iteration,
                AssistantRun.current_iteration: normalized_iteration,
                AssistantRun.updated_at: datetime.utcnow(),
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.rollback()
        raise WorkspaceDirectMcpRunLogError("Direct MCP 回合已失效")
    commit_session(db)
    db.refresh(run)
    return token


def resolve_workspace_direct_mcp_lease(
    db: Session,
    *,
    project_id: str,
    lease_token: str,
) -> tuple[AssistantRun, int]:
    """Resolve a lease only from immutable server scope plus DB state."""

    digest = _lease_hash(lease_token)
    run = (
        db.query(AssistantRun)
        .join(
            AssistantConversation,
            AssistantConversation.id == AssistantRun.conversation_id,
        )
        .join(OperationRun, OperationRun.id == AssistantRun.operation_id)
        .filter(
            AssistantRun.project_id == str(project_id or ""),
            AssistantRun.status == "running",
            AssistantRun.direct_mcp_lease_hash == digest,
            AssistantRun.direct_mcp_lease_iteration.isnot(None),
            AssistantRun.current_iteration == AssistantRun.direct_mcp_lease_iteration,
            AssistantConversation.project_id == AssistantRun.project_id,
            AssistantConversation.scope == "project",
            OperationRun.source_kind == "assistant",
            OperationRun.source_id == AssistantRun.id,
            OperationRun.project_id == AssistantRun.project_id,
            OperationRun.status == "running",
        )
        .one_or_none()
    )
    if run is None:
        raise WorkspaceDirectMcpRunLogError("Direct MCP lease 已失效")
    return run, int(run.direct_mcp_lease_iteration or 0)


def cas_workspace_direct_mcp_lease(
    db: Session,
    *,
    project_id: str,
    run_id: str,
    step_id: str,
    iteration: int,
    lease_token: str,
) -> bool:
    """Acquire the completion boundary while the exact run lease is active."""

    digest = _lease_hash(lease_token)
    operation_id = (
        db.query(AssistantRun.operation_id)
        .join(
            AssistantConversation,
            AssistantConversation.id == AssistantRun.conversation_id,
        )
        .filter(
            AssistantRun.id == run_id,
            AssistantRun.project_id == project_id,
            AssistantRun.status == "running",
            AssistantRun.direct_mcp_lease_hash == digest,
            AssistantRun.direct_mcp_lease_iteration == iteration,
            AssistantRun.current_iteration == iteration,
            AssistantConversation.project_id == AssistantRun.project_id,
            AssistantConversation.scope == "project",
        )
        .scalar()
    )
    if not operation_id:
        return False
    operation_updated = (
        db.query(OperationRun)
        .filter(
            OperationRun.id == operation_id,
            OperationRun.source_kind == "assistant",
            OperationRun.source_id == run_id,
            OperationRun.project_id == project_id,
            OperationRun.status == "running",
        )
        .update(
            {OperationRun.updated_at: datetime.utcnow()},
            synchronize_session=False,
        )
    )
    if operation_updated != 1:
        return False
    run_updated = (
        db.query(AssistantRun)
        .filter(
            AssistantRun.id == run_id,
            AssistantRun.project_id == project_id,
            AssistantRun.status == "running",
            AssistantRun.direct_mcp_lease_hash == digest,
            AssistantRun.direct_mcp_lease_iteration == iteration,
            AssistantRun.current_iteration == iteration,
            AssistantRun.conversation_id.in_(
                db.query(AssistantConversation.id).filter(
                    AssistantConversation.project_id == project_id,
                    AssistantConversation.scope == "project",
                )
            ),
        )
        .update(
            {AssistantRun.updated_at: datetime.utcnow()},
            synchronize_session=False,
        )
    )
    if run_updated != 1:
        return False
    step_updated = (
        db.query(AssistantRunStep)
        .filter(
            AssistantRunStep.id == step_id,
            AssistantRunStep.run_id == run_id,
            AssistantRunStep.project_id == project_id,
            AssistantRunStep.iteration == iteration,
            AssistantRunStep.status == "running",
            AssistantRunStep.direct_mcp_call_key.isnot(None),
        )
        .update(
            {AssistantRunStep.updated_at: datetime.utcnow()},
            synchronize_session=False,
        )
    )
    return step_updated == 1


def _workspace_scope(
    db: Session,
    *,
    state_file: str,
    project_id: str,
    lease_token: str,
) -> tuple[AssistantRun, int]:
    try:
        state = read_tool_category_state(state_file)
    except ValueError as exc:
        raise WorkspaceDirectMcpRunLogError("Direct MCP 回合状态不可验证") from exc
    guard = state.get("turn_guard")
    if not isinstance(guard, dict) or str(guard.get("kind") or "") != "workspace":
        raise WorkspaceDirectMcpRunLogError("Direct MCP 缺少 workspace 回合绑定")
    return resolve_workspace_direct_mcp_lease(
        db,
        project_id=project_id,
        lease_token=lease_token,
    )


def _call_key(run_id: str, iteration: int, call_id: Any) -> str:
    if isinstance(call_id, bool) or not isinstance(call_id, (int, str)):
        raise WorkspaceDirectMcpRunLogError("Direct MCP 工具调用缺少稳定 JSON-RPC id")
    encoded = json.dumps(call_id, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
    return f"direct_mcp:{run_id}:{iteration}:{digest}"


def _stored_result(step: AssistantRunStep) -> dict[str, Any]:
    try:
        value = json.loads(step.result_json or "")
    except (TypeError, json.JSONDecodeError):
        value = None
    if isinstance(value, dict) and str(step.status or "").lower() not in {
        "running",
        "pending",
        "queued",
        "in_progress",
    }:
        return value
    return {
        "tool": str(step.tool or ""),
        "status": "error",
        "detail": "同一 Direct MCP 调用已有未闭合的持久步骤；为避免重复写入，未自动重放。",
        "data": {
            "reason": "direct_mcp_call_already_started",
            "step_id": str(step.id),
        },
    }


def _existing_call_step(
    db: Session,
    *,
    run_id: str,
    iteration: int,
    call_key: str,
    tool_name: str,
    expected_request: dict[str, Any],
) -> AssistantRunStep | None:
    step = (
        db.query(AssistantRunStep)
        .filter(AssistantRunStep.direct_mcp_call_key == call_key)
        .one_or_none()
    )
    if step is None:
        return None
    try:
        request = deserialize_step_request(step.request_json)
    except UnrecoverableStepRequest as exc:
        raise WorkspaceDirectMcpRunLogError(
            "Direct MCP 持久请求不可恢复"
        ) from exc
    if step.run_id != run_id or int(step.iteration or 0) != iteration:
        raise WorkspaceDirectMcpRunLogError("Direct MCP 调用身份范围冲突")
    if str(step.tool or "") != tool_name:
        raise WorkspaceDirectMcpRunLogError("Direct MCP JSON-RPC id 被复用于不同工具")
    if request != expected_request:
        raise WorkspaceDirectMcpRunLogError("Direct MCP JSON-RPC id 被复用于不同参数")
    return step


def begin_workspace_direct_mcp_step(
    db: Session,
    *,
    state_file: str,
    project_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    call_id: Any,
    is_write: bool,
    lease_token: str,
) -> WorkspaceDirectMcpStepStart:
    """Create or recover the one durable step for a scoped MCP dispatch."""

    run, iteration = _workspace_scope(
        db,
        state_file=state_file,
        project_id=project_id,
        lease_token=lease_token,
    )
    supplied_project_id = str(arguments.get("project_id") or "").strip()
    if supplied_project_id and supplied_project_id != str(run.project_id):
        raise WorkspaceDirectMcpRunLogError("Direct MCP 参数试图越出当前作品范围")
    supplied_run_id = str(arguments.get("run_id") or "").strip()
    if supplied_run_id and supplied_run_id != str(run.id):
        raise WorkspaceDirectMcpRunLogError("Direct MCP 参数试图绑定其他运行记录")
    arguments["project_id"] = str(run.project_id)
    if tool_name in {"prepare_task_context", "prepare_external_writing_context"} and run.model:
        # A managed external writer is the pinned assistant model. Its context
        # budget must not depend on an omitted or model-invented identity.
        arguments["model"] = str(run.model)
    call_key = _call_key(str(run.id), iteration, call_id)
    natural_request = {
        **arguments,
        "project_id": str(run.project_id),
        "_context_execution_route": _EXTERNAL_MCP_ROUTE,
    }
    request = {
        **natural_request,
        DIRECT_MCP_CALL_KEY: call_key,
    }
    existing = _existing_call_step(
        db,
        run_id=str(run.id),
        iteration=iteration,
        call_key=call_key,
        tool_name=tool_name,
        expected_request=request,
    )
    if existing is not None:
        return WorkspaceDirectMcpStepStart(existing, _stored_result(existing))

    natural_key = (
        generate_idempotency_key(db, tool_name, str(run.project_id), natural_request)
        if is_write
        else None
    )
    try:
        step = start_run_step(
            db,
            run,
            step_type="write" if is_write else "search",
            tool=tool_name,
            iteration=iteration,
            request=request,
            detail="Direct MCP 工具调用已进入服务端持久执行边界",
            idempotency_key=natural_key,
            direct_mcp_call_key=call_key,
            emit_operation_signal=False,
        )
    except IntegrityError:
        db.rollback()
        winner = _existing_call_step(
            db,
            run_id=str(run.id),
            iteration=iteration,
            call_key=call_key,
            tool_name=tool_name,
            expected_request=request,
        )
        if winner is None:
            raise WorkspaceDirectMcpRunLogError(
                "Direct MCP 调用身份冲突但缺少持久 winner"
            ) from None
        return WorkspaceDirectMcpStepStart(winner, _stored_result(winner))
    if step is None:
        raise WorkspaceDirectMcpRunLogError("Direct MCP 工具步骤无法持久化")
    return WorkspaceDirectMcpStepStart(step)


def claim_workspace_direct_mcp_step(
    db: Session,
    expected: AssistantRunStep,
) -> tuple[AssistantRunStep | None, bool]:
    """Claim one running Direct step, or reload its concurrent terminal winner."""

    claimed = (
        db.query(AssistantRunStep)
        .filter(
            AssistantRunStep.id == expected.id,
            AssistantRunStep.run_id == expected.run_id,
            AssistantRunStep.project_id == expected.project_id,
            AssistantRunStep.iteration == expected.iteration,
            AssistantRunStep.status == "running",
            AssistantRunStep.direct_mcp_call_key.isnot(None),
        )
        .update(
            {AssistantRunStep.updated_at: AssistantRunStep.updated_at},
            synchronize_session=False,
        )
    )
    if claimed == 1:
        return db.get(AssistantRunStep, expected.id), True
    db.rollback()
    winner = (
        db.query(AssistantRunStep)
        .filter(AssistantRunStep.id == expected.id)
        .populate_existing()
        .one_or_none()
    )
    return winner, False


__all__ = [
    "DIRECT_MCP_CALL_KEY",
    "WorkspaceDirectMcpRunLogError",
    "WorkspaceDirectMcpStepStart",
    "begin_workspace_direct_mcp_step",
    "cas_workspace_direct_mcp_lease",
    "claim_workspace_direct_mcp_step",
    "issue_workspace_direct_mcp_lease",
    "resolve_workspace_direct_mcp_lease",
]
