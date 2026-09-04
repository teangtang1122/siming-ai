"""Owner-scoped REST contract for derived conversation checkpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..core.exceptions import AppException, NotFoundError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.assistant.interfaces.workspace_dependencies import assistant_workspace
from ..schemas.conversation_context import (
    ConversationCheckpointDetailResponse,
    ConversationCheckpointListResponse,
    ConversationContextStateResponse,
)
from ..services.conversation_context import runtime as context_runtime
from ..services.conversation_context.checkpoint_state import safe_public_error_detail
from ..services.conversation_context.errors import (
    ConversationContextError,
    ConversationContextErrorCode,
)
from ..services.conversation_context.public_api import (
    public_checkpoint_detail_payload,
    public_checkpoint_list_payload,
    public_context_state_payload,
)

router = APIRouter(tags=["ai-writer"])

_CONVERSATION_KIND = "workspace"
_BASE = (
    "/projects/{project_id}/ai/assistant/conversations/{conversation_id}"
)
DbSession = Annotated[Session, Depends(get_db)]


def _workspace_store(db: Session, project_id: str, conversation_id: str) -> Any:
    """Return the canonical store only after enforcing project ownership."""

    store = assistant_workspace(db)
    if store.conversation(project_id, conversation_id) is None:
        # Keep foreign-project and missing conversations indistinguishable.
        raise NotFoundError("助手对话不存在")
    return store


def _state_payload(store: Any, project_id: str, conversation_id: str) -> dict[str, Any]:
    return public_context_state_payload(
        store=store,
        conversation_kind=_CONVERSATION_KIND,
        conversation_id=conversation_id,
        owner_id=project_id,
    )


def _checkpoint_payload(
    store: Any,
    project_id: str,
    conversation_id: str,
    checkpoint_id: str,
) -> dict[str, Any]:
    if store.context_checkpoint(
        _CONVERSATION_KIND,
        conversation_id,
        checkpoint_id,
        owner_id=project_id,
    ) is None:
        raise NotFoundError("会话 checkpoint 不存在")
    try:
        return public_checkpoint_detail_payload(
            store=store,
            conversation_kind=_CONVERSATION_KIND,
            conversation_id=conversation_id,
            owner_id=project_id,
            checkpoint_id=checkpoint_id,
        )
    except ConversationContextError as exc:
        raise NotFoundError("会话 checkpoint 不存在") from exc


def _conflict(message: str) -> AppException:
    return AppException(code=409, message=message, status_code=409)


@router.get(
    f"{_BASE}/context-state",
    response_model=ApiResponse[ConversationContextStateResponse],
)
async def get_conversation_context_state(
    project_id: str,
    conversation_id: str,
    db: DbSession,
):
    """Return the active checkpoint/budget state without reading client history."""

    store = _workspace_store(db, project_id, conversation_id)
    return ApiResponse.success(data=_state_payload(store, project_id, conversation_id))


@router.get(
    f"{_BASE}/checkpoints",
    response_model=ApiResponse[ConversationCheckpointListResponse],
)
async def list_conversation_checkpoints(
    project_id: str,
    conversation_id: str,
    db: DbSession,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """List derived attempts newest first; full navigation stays in detail."""

    store = _workspace_store(db, project_id, conversation_id)
    return ApiResponse.success(
        data=public_checkpoint_list_payload(
            store=store,
            conversation_kind=_CONVERSATION_KIND,
            conversation_id=conversation_id,
            owner_id=project_id,
            offset=offset,
            limit=limit,
        )
    )


@router.get(
    f"{_BASE}/checkpoints/{{checkpoint_id}}",
    response_model=ApiResponse[ConversationCheckpointDetailResponse],
)
async def get_conversation_checkpoint(
    project_id: str,
    conversation_id: str,
    checkpoint_id: str,
    db: DbSession,
):
    """Return one safe, auditable checkpoint view."""

    store = _workspace_store(db, project_id, conversation_id)
    return ApiResponse.success(
        data=_checkpoint_payload(store, project_id, conversation_id, checkpoint_id)
    )


@router.post(f"{_BASE}/checkpoints/rebuild")
async def rebuild_conversation_checkpoint(
    project_id: str,
    conversation_id: str,
    db: DbSession,
):
    """Return 409: rebuilding is only safe inside a new model request."""

    _workspace_store(db, project_id, conversation_id)
    raise _conflict(
        "当前不能脱离模型请求安全重建上下文；请发送新消息触发按需重建。"
    )


@router.post(
    f"{_BASE}/checkpoints/{{checkpoint_id}}/cancel",
    response_model=ApiResponse[ConversationContextStateResponse],
)
async def cancel_conversation_checkpoint(
    project_id: str,
    conversation_id: str,
    checkpoint_id: str,
    db: DbSession,
):
    """Cancel an in-flight derived attempt without changing the transcript."""

    store = _workspace_store(db, project_id, conversation_id)
    if store.context_checkpoint(
        _CONVERSATION_KIND,
        conversation_id,
        checkpoint_id,
        owner_id=project_id,
    ) is None:
        raise NotFoundError("会话 checkpoint 不存在")
    try:
        context_runtime.cancel_checkpoint_attempt(
            store=store,
            conversation_kind=_CONVERSATION_KIND,
            conversation_id=conversation_id,
            owner_id=project_id,
            checkpoint_id=checkpoint_id,
        )
    except ConversationContextError as exc:
        if exc.code is ConversationContextErrorCode.SOURCE_CHANGED:
            raise NotFoundError("会话 checkpoint 不存在") from exc
        raise _conflict(
            safe_public_error_detail(exc.code)
            or "对话上下文处理失败，本次任务未执行。"
        ) from exc
    return ApiResponse.success(
        data=_state_payload(store, project_id, conversation_id),
        message="上下文整理取消请求已确认",
    )


@router.delete(f"{_BASE}/checkpoints/{{checkpoint_id}}")
async def delete_conversation_checkpoint(
    project_id: str,
    conversation_id: str,
    checkpoint_id: str,
    db: DbSession,
):
    """Return 409: checkpoint audit records are not deletable by this API."""

    store = _workspace_store(db, project_id, conversation_id)
    checkpoint = store.context_checkpoint(
        _CONVERSATION_KIND,
        conversation_id,
        checkpoint_id,
        owner_id=project_id,
    )
    if checkpoint is None:
        raise NotFoundError("会话 checkpoint 不存在")
    state = store.context_state(
        _CONVERSATION_KIND,
        conversation_id,
        owner_id=project_id,
    )
    active = state is not None and str(state.active_checkpoint_id or "") == checkpoint_id
    if active:
        raise _conflict("活动 checkpoint 不能直接删除；请新建对话或等待后续安全替换。")
    if str(checkpoint.status) not in {"failed", "cancelled", "superseded"}:
        raise _conflict("只能删除未被使用的 failed/cancelled/superseded checkpoint。")

    # The current repository delete operation clears child.parent on cascade.
    # A route-level reference scan cannot close the concurrent writer window,
    # so retain the DELETE contract but fail closed until persistence exposes
    # one atomic "delete only if still unreferenced" operation.
    raise _conflict("当前版本不能原子证明 checkpoint 未被并发引用，未执行删除。")


__all__ = ["router"]
