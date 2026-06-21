"""Model selection for project cataloging."""
from __future__ import annotations

from ...ai.gateway import LLMGateway
from ...ai.local_cli_adapter import is_local_cli_provider
from ...database.models import APIConfig
from ...database.session import SessionLocal
from .constants import CHEAP_MODEL_BY_PROVIDER


def default_cataloging_model(model_override: str | None = None) -> str | None:
    if model_override:
        return model_override
    db = SessionLocal()
    try:
        config = db.query(APIConfig).filter(APIConfig.is_global_default == True).first()
        if not config:
            return None
        model = CHEAP_MODEL_BY_PROVIDER.get(config.provider, config.default_model)
        return f"{config.provider}:{model}"
    finally:
        db.close()


def cataloging_extra_body(
    model: str | None,
    *,
    cwd: str | None = None,
    attachments: list[str] | None = None,
) -> dict | None:
    provider = (model or "").split(":", 1)[0].lower()
    base: dict | None = None
    if provider == "deepseek":
        base = {"thinking": {"type": "disabled"}}
    if is_local_cli_provider(provider):
        return LLMGateway.local_cli_extra_body(
            model,
            cwd=cwd,
            attachments=attachments,
            base=base,
        )
    return base
