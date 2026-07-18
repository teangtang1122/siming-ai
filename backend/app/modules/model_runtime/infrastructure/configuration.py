"""SQLAlchemy-backed model configuration adapter."""

from __future__ import annotations

from ....architecture.uow import SqlAlchemyUnitOfWork
from ....core.crypto import decrypt
from ....database.session import SessionLocal
from ..application.ports import ModelConfigurationPort
from ..domain.configuration import LocalTaskModelSetting, ModelProviderConfig
from ..domain.policy import local_runtime_disabled
from .models import APIConfig, LocalModelTaskSetting
from .readiness import is_model_config_usable, mark_model_failure


class SqlAlchemyModelConfiguration(ModelConfigurationPort):
    def global_default(self) -> ModelProviderConfig | None:
        with SessionLocal() as db:
            row = db.query(APIConfig).filter(APIConfig.is_global_default == True).first()  # noqa: E712
            return self._snapshot(row)

    def ready_providers(self) -> tuple[ModelProviderConfig, ...]:
        with SessionLocal() as db:
            return tuple(
                snapshot
                for row in db.query(APIConfig).all()
                if (snapshot := self._snapshot(row)) is not None
            )

    def provider(self, provider: str) -> ModelProviderConfig | None:
        with SessionLocal() as db:
            row = db.query(APIConfig).filter(APIConfig.provider == provider).first()
            return self._snapshot(row)

    def task_setting(self, task_type: str) -> LocalTaskModelSetting | None:
        with SessionLocal() as db:
            row = (
                db.query(LocalModelTaskSetting)
                .filter(LocalModelTaskSetting.task_type == task_type)
                .first()
            )
            if not row:
                return None
            return LocalTaskModelSetting(
                task_type=row.task_type,
                model_key=row.model_key,
                context_length=row.context_length,
            )

    def record_failure(self, provider: str, error: BaseException | object) -> None:
        try:
            with SqlAlchemyUnitOfWork(SessionLocal) as uow:
                row = uow.session.query(APIConfig).filter(APIConfig.provider == provider).first()
                if row and mark_model_failure(row, error, source="gateway"):
                    uow.commit()
        except Exception:
            return

    @staticmethod
    def _snapshot(row: APIConfig | None) -> ModelProviderConfig | None:
        if not row or local_runtime_disabled(row.provider) or not is_model_config_usable(row):
            return None
        return ModelProviderConfig(
            provider=row.provider,
            default_model=row.default_model,
            api_key=decrypt(row.api_key_encrypted) if row.api_key_encrypted else "",
            base_url=row.base_url_override,
            api_protocol=row.api_protocol or "chat_completions",
            provider_type=row.provider_type or "api",
            cli_command=row.cli_command,
            cli_args=row.cli_args,
        )
