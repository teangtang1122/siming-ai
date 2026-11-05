"""Global exception handling."""
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from .response import ApiResponse


class AppException(Exception):
    """Base application exception."""
    
    def __init__(self, code: int, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppException):
    """Resource not found."""
    
    def __init__(self, message: str = "Resource not found"):
        super().__init__(code=404, message=message, status_code=404)


class ValidationError(AppException):
    """Validation error."""
    
    def __init__(self, message: str = "Validation failed"):
        super().__init__(code=400, message=message, status_code=400)


class ConflictError(AppException):
    """The requested write was based on stale authoritative state."""

    def __init__(self, message: str = "Resource version conflict"):
        super().__init__(code=409, message=message, status_code=409)


class UnauthorizedError(AppException):
    """Unauthorized access."""
    
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(code=401, message=message, status_code=401)


class LLMError(AppException):
    """LLM API call error."""
    
    def __init__(self, message: str = "LLM service error"):
        super().__init__(code=502, message=message, status_code=502)


class LLMConnectionError(LLMError):
    """A provider SDK connection failure with a known transport phase."""

    def __init__(self, phase: str, *, request_not_sent: bool = False):
        self.phase = phase
        self.request_not_sent = request_not_sent
        self.connection_recovery_exhausted = False
        super().__init__(
            "连接模型服务失败，尚未收到响应；请检查代理和网络后重试。"
            if phase == "connect" else "模型网络连接中断，请检查代理和网络后重试。"
        )


class LLMTimeoutError(LLMError):
    """Transport phase survives SDK wrapping and safe public projection."""

    def __init__(self, phase: str, timeout_seconds: float | None = None, *, request_not_sent: bool = False):
        self.phase = phase
        self.request_not_sent = request_not_sent
        self.connection_recovery_exhausted = False
        self.timeout_seconds = timeout_seconds
        super().__init__(
            "连接模型服务超时，尚未收到响应；请检查代理和网络后重试。"
            if phase == "connect" else "等待模型响应超时，请稍后重试。"
        )


class LLMOutputLimitError(LLMError):
    """A model exhausted one response before completing a usable tool call."""

    def __init__(self, max_tokens: int | None):
        self.max_tokens = max_tokens
        super().__init__("模型单次输出额度已用尽，仍未形成完整工具调用")


class LLMToolArgumentsError(LLMError):
    """Completed native identities with invalid arguments; never executable."""

    def __init__(self, assistant_message: dict, validation_errors: list[dict]):
        self.assistant_message = assistant_message
        self.validation_errors = validation_errors
        super().__init__("模型返回的工具参数 JSON 无效，整批未执行：" +
                         "；".join(item["message"] for item in validation_errors))


class LLMIncompleteResponseError(LLMError):
    """HTTP succeeded but the provider did not complete the response."""

    def __init__(self, reason: str, max_tokens: int | None = None):
        self.reason = reason
        self.max_tokens = max_tokens
        super().__init__(f"模型响应未完成（{reason}），未交付正文或执行工具；请调整输出额度或检查模型状态")


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """Handle custom application exceptions."""
    data = {"timeout_phase": exc.phase, "timeout_seconds": exc.timeout_seconds} if isinstance(exc, LLMTimeoutError) else (
        {"connection_phase": exc.phase} if isinstance(exc, LLMConnectionError) else None)
    return JSONResponse(
        status_code=exc.status_code,
        content=ApiResponse.error(code=exc.code, message=exc.message, data=data).model_dump()
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle request validation errors."""
    errors = []
    for error in exc.errors():
        loc = " -> ".join(str(x) for x in error.get("loc", []))
        errors.append(f"{loc}: {error.get('msg', '')}")
    
    return JSONResponse(
        status_code=422,
        content=ApiResponse.error(
            code=422,
            message="Validation error: " + "; ".join(errors)
        ).model_dump()
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unhandled exceptions."""
    return JSONResponse(
        status_code=500,
        content=ApiResponse.error(
            code=500,
            message="Internal server error"
        ).model_dump()
    )
