"""SQLAlchemy implementation of first-run model configuration."""

from __future__ import annotations

from ....architecture.uow import SqlAlchemyUnitOfWork
from ....core.crypto import encrypt
from ..application.getting_started import ConfiguredOpenCode, GettingStartedModelState
from ..domain.policy import local_runtime_disabled
from .models import APIConfig
from .readiness import READINESS_READY, is_model_config_usable, mark_model_unverified


class SqlAlchemyGettingStartedConfiguration:
    def state(self, session) -> GettingStartedModelState:
        opencode = session.query(APIConfig).filter(APIConfig.provider == "opencode_cli").first()
        global_config = (
            session.query(APIConfig).filter(APIConfig.is_global_default == True).first()  # noqa: E712
        )
        ready = [
            config
            for config in session.query(APIConfig)
            .filter(APIConfig.readiness_status == READINESS_READY)
            .all()
            if not local_runtime_disabled(config.provider)
        ]
        detected = (
            session.query(APIConfig).filter(APIConfig.readiness_status == "detected").first()
        )
        usable_global = (
            global_config
            if is_model_config_usable(global_config)
            and not local_runtime_disabled(global_config.provider)
            else None
        )
        return GettingStartedModelState(
            opencode_command=opencode.cli_command if opencode else None,
            configured_model=opencode.default_model if opencode else None,
            configured=bool(opencode),
            opencode_is_global=bool(
                opencode and opencode.is_global_default and is_model_config_usable(opencode)
            ),
            has_any_model=bool(session.query(APIConfig.id).first()),
            has_detected_models=bool(detected),
            has_usable_models=bool(ready),
            global_provider=usable_global.provider if usable_global else None,
            global_model=usable_global.default_model if usable_global else None,
        )

    def configure_opencode(
        self,
        session,
        *,
        command: str,
        model: str,
        cli_args: str,
    ) -> ConfiguredOpenCode:
        with SqlAlchemyUnitOfWork.from_session(session) as uow:
            config = (
                session.query(APIConfig).filter(APIConfig.provider == "opencode_cli").first()
            )
            if config:
                config.api_key_encrypted = encrypt("__local_cli__")
                config.default_model = model
                config.provider_type = "local_cli"
                config.cli_command = command
                config.cli_args = cli_args
                mark_model_unverified(config, source="getting_started_configure")
            else:
                config = APIConfig(
                    provider="opencode_cli",
                    api_key_encrypted=encrypt("__local_cli__"),
                    default_model=model,
                    is_global_default=False,
                    provider_type="local_cli",
                    cli_command=command,
                    cli_args=cli_args,
                    readiness_status="unverified",
                    readiness_json='{"source":"getting_started_configure"}',
                )
                session.add(config)
            uow.commit()
            session.refresh(config)
        return ConfiguredOpenCode(
            provider=config.provider,
            model=config.default_model,
            command=config.cli_command,
            cli_args=config.cli_args,
        )


__all__ = ["SqlAlchemyGettingStartedConfiguration"]
