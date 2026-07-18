"""Application service for deterministic model selection."""

from __future__ import annotations

from typing import Any

from ....core.exceptions import NotFoundError
from ..domain.configuration import ModelProviderConfig, TaskModelSelection
from ..domain.policy import local_runtime_disabled, local_runtime_disabled_message
from .ports import ModelConfigurationPort


class ModelRuntime:
    def __init__(self, configurations: ModelConfigurationPort) -> None:
        self._configurations = configurations

    def parse_model(self, model: str | None) -> tuple[str, str]:
        if not model:
            config = self._configurations.global_default()
            if not config:
                raise NotFoundError("未配置可用的全局默认模型，请先在系统设置中测试并启用模型")
            self._ensure_provider_enabled(config.provider)
            return config.provider, config.default_model
        if ":" in model:
            provider, model_name = model.split(":", 1)
            return provider, model_name
        return self.resolve_provider(model)

    def resolve_provider(self, model_name: str) -> tuple[str, str]:
        for config in self._configurations.ready_providers():
            if config.default_model == model_name and not local_runtime_disabled(config.provider):
                return config.provider, config.default_model
        lowered = model_name.lower()
        if "claude" in lowered:
            return "anthropic", model_name
        if "deepseek" in lowered:
            return "deepseek", model_name
        if "qwen" in lowered or "qwq" in lowered:
            return "qwen", model_name
        if "gemini" in lowered:
            return "gemini", model_name
        return "openai", model_name

    def identity(self, model: str | None) -> tuple[str, str]:
        return self.parse_model(model)

    def provider_config(self, provider: str) -> ModelProviderConfig:
        self._ensure_provider_enabled(provider)
        config = self._configurations.provider(provider)
        if not config:
            raise NotFoundError(
                f"提供商 '{provider}' 尚未通过真实对话测试，请先在系统设置中点击测试并启用"
            )
        return config

    def select_for_task(
        self,
        *,
        task_type: str,
        model_override: str | None = None,
        extra_body: dict[str, Any] | None = None,
        prefer_task_model: bool = False,
    ) -> TaskModelSelection:
        task_type = str(task_type or "").strip()
        override = str(model_override or "").strip()
        if override:
            selection = self._selection_from_value(override, "explicit")
            self._apply_task_context_length(selection, task_type, extra_body)
            return selection

        if extra_body:
            prefer_task_model = prefer_task_model or bool(
                extra_body.get("moshu_prefer_task_model") or extra_body.get("moshu_use_task_model")
            )

        setting = self._configurations.task_setting(task_type) if task_type else None
        selected = ""
        source = ""
        if prefer_task_model and setting and not local_runtime_disabled():
            selected = f"local_llama_cpp:{setting.model_key}"
            source = "task_setting"
        if not selected:
            config = self._configurations.global_default()
            if config and not local_runtime_disabled(config.provider):
                selected = f"{config.provider}:{config.default_model}"
                source = "global_default"
        if not selected and setting and not local_runtime_disabled():
            selected = f"local_llama_cpp:{setting.model_key}"
            source = "task_setting_fallback"
        if not selected:
            return TaskModelSelection(model=None, source="unconfigured")

        selection = self._selection_from_value(selected, source)
        self._apply_task_context_length(selection, task_type, extra_body)
        return selection

    def record_failure(self, provider: str, error: BaseException | object) -> None:
        self._configurations.record_failure(provider, error)

    def _selection_from_value(self, value: str, source: str) -> TaskModelSelection:
        try:
            provider, model_name = self.parse_model(value)
        except Exception:
            provider, separator, model_name = value.partition(":")
            if not separator:
                provider = ""
                model_name = value
        normalized = f"{provider}:{model_name}" if provider else value
        return TaskModelSelection(
            model=normalized,
            source=source,
            provider=provider or None,
            model_name=model_name or None,
        )

    def _apply_task_context_length(
        self,
        selection: TaskModelSelection,
        task_type: str,
        extra_body: dict[str, Any] | None,
    ) -> None:
        if not extra_body or selection.provider != "local_llama_cpp" or not task_type:
            return
        if extra_body.get("moshu_context_length"):
            return
        setting = self._configurations.task_setting(task_type)
        if setting and setting.context_length and selection.model_name == setting.model_key:
            extra_body["moshu_context_length"] = setting.context_length

    @staticmethod
    def _ensure_provider_enabled(provider: str) -> None:
        if local_runtime_disabled(provider):
            raise NotFoundError(local_runtime_disabled_message())


_runtime: ModelRuntime | None = None


def configure_model_runtime(runtime: ModelRuntime) -> None:
    global _runtime
    _runtime = runtime


def get_model_runtime() -> ModelRuntime:
    if _runtime is None:
        raise RuntimeError("Model runtime has not been configured")
    return _runtime


def resolve_model_identity(model: str | None) -> tuple[str, str]:
    return get_model_runtime().identity(model)
