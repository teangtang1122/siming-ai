"""SQLAlchemy projection of verified models for Quick Start."""
from __future__ import annotations

from ..application.getting_started import GettingStartedModelState
from ..domain.policy import local_runtime_disabled
from .models import APIConfig
from .readiness import is_model_config_usable


class SqlAlchemyGettingStartedConfiguration:
    def state(self, session) -> GettingStartedModelState:
        configs = session.query(APIConfig).order_by(APIConfig.provider, APIConfig.id).all()
        ready = [config for config in configs
                 if is_model_config_usable(config) and config.default_model
                 and not local_runtime_disabled(config.provider)]
        global_config = next((config for config in ready if config.is_global_default), None)
        available = global_config or next(iter(ready), None)
        return GettingStartedModelState(
            has_any_model=bool(configs),
            global_provider=global_config.provider if global_config else None,
            global_model=global_config.default_model if global_config else None,
            usable_provider=available.provider if available else None,
            usable_model=available.default_model if available else None,
        )
