"""Model-runtime boundary for local coding-agent CLI execution.

The legacy adapter remains import-compatible while its process runner is
incrementally split. New model-runtime code depends on this boundary only.
"""

from app.ai.local_cli_adapter import (
    DEFAULT_CLI_MODELS,
    DEFAULT_LOCAL_CLI_TIMEOUT,
    LOCAL_CLI_TIMEOUT_GRACE_SECONDS,
    CLIPermissionRequiredError,
    LocalCLIAdapter,
    detect_cli_permission_request,
    detect_cli_quota_error,
    is_local_cli_provider,
    local_cli_model_options,
)

__all__ = [
    "DEFAULT_CLI_MODELS",
    "DEFAULT_LOCAL_CLI_TIMEOUT",
    "CLIPermissionRequiredError",
    "LOCAL_CLI_TIMEOUT_GRACE_SECONDS",
    "LocalCLIAdapter",
    "detect_cli_quota_error",
    "detect_cli_permission_request",
    "is_local_cli_provider",
    "local_cli_model_options",
]
