"""Read-only first-run model readiness, independent of any provider."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.model_runtime.application.getting_started import get_getting_started_configuration

router = APIRouter(tags=["getting-started"])


class GettingStartedModel(BaseModel):
    provider: str
    model: str


class GettingStartedStatus(BaseModel):
    """Persisted verification results; opening this page never runs a model."""

    has_any_model: bool
    has_usable_models: bool
    needs_setup: bool
    global_model: GettingStartedModel | None = None
    available_model: GettingStartedModel | None = None


@router.get("/config/getting-started", response_model=ApiResponse[GettingStartedStatus])
def get_getting_started_status(db: Session = Depends(get_db)):
    state = get_getting_started_configuration().state(db)
    usable = bool(state.usable_provider and state.usable_model)
    return ApiResponse.success(data={
        "has_any_model": state.has_any_model,
        "has_usable_models": usable,
        "needs_setup": not usable,
        "global_model": {
            "provider": state.global_provider,
            "model": state.global_model,
        } if state.global_provider and state.global_model else None,
        "available_model": {
            "provider": state.usable_provider,
            "model": state.usable_model,
        } if usable else None,
    }, message="模型配置状态已读取")
