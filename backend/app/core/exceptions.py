"""Global exception handling."""
import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .response import ApiResponse

logger = logging.getLogger(__name__)


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


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """Handle custom application exceptions."""
    if exc.code >= 500:
        logger.error(
            "Request failed with app error code=%s path=%s message=%s",
            exc.code,
            request.url.path,
            exc.message,
        )
    else:
        logger.warning(
            "Request rejected code=%s path=%s message=%s",
            exc.code,
            request.url.path,
            exc.message,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content=ApiResponse.error(code=exc.code, message=exc.message).model_dump()
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle request validation errors."""
    errors = []
    for error in exc.errors():
        loc = " -> ".join(str(x) for x in error.get("loc", []))
        errors.append(f"{loc}: {error.get('msg', '')}")
    logger.warning(
        "Request validation failed path=%s details=%s",
        request.url.path,
        "; ".join(errors),
    )

    return JSONResponse(
        status_code=422,
        content=ApiResponse.error(
            code=422,
            message="Validation error: " + "; ".join(errors)
        ).model_dump()
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unhandled exceptions."""
    logger.exception(
        "Unhandled exception path=%s class=%s",
        request.url.path,
        type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content=ApiResponse.error(
            code=500,
            message="Internal server error"
        ).model_dump()
    )
