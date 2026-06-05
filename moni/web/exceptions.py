"""Custom HTTPException subclasses and uniform error serialisation."""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from moni.web.models import ErrorResponse


# ---------------------------------------------------------------------------
# Custom exception classes
# ---------------------------------------------------------------------------


class WebBaseException(Exception):
    """Base class for all web-layer exceptions.

    Subclasses set ``status_code`` (HTTP status) and ``code`` (machine-readable
    error code) and may set ``details`` for extra context.
    """

    status_code: int = 500
    code: str = "internal_error"
    message: str = "An internal error occurred"

    def __init__(
        self,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.details = details
        super().__init__(self.message)

    def to_response(self) -> ErrorResponse:
        return ErrorResponse(code=self.code, message=self.message, details=self.details)


class NotFoundError(WebBaseException):
    status_code = 404
    code = "not_found"
    message = "Resource not found"


class BadRequestError(WebBaseException):
    status_code = 400
    code = "bad_request"
    message = "Bad request"


class ConflictError(WebBaseException):
    status_code = 409
    code = "conflict"
    message = "Resource conflict"


class UnauthorizedError(WebBaseException):
    status_code = 401
    code = "unauthorized"
    message = "Unauthorized"


class ForbiddenError(WebBaseException):
    status_code = 403
    code = "forbidden"
    message = "Forbidden"


class ValidationError(WebBaseException):
    status_code = 422
    code = "validation_error"
    message = "Validation failed"


class ServiceUnavailableError(WebBaseException):
    status_code = 503
    code = "service_unavailable"
    message = "Service temporarily unavailable"


# ---------------------------------------------------------------------------
# FastAPI exception → JSONResponse hook
# ---------------------------------------------------------------------------


async def _web_base_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Convert any WebBaseException subclass to a typed JSONResponse."""
    if isinstance(exc, WebBaseException):
        web_exc: WebBaseException = exc
        return JSONResponse(
            status_code=web_exc.status_code,
            content=web_exc.to_response().model_dump(mode="json"),
        )
    # Re-raise unexpected exceptions so FastAPI's default handler takes over
    raise exc


def register_exception_handlers(app: FastAPI) -> None:
    """Register the web-layer exception handler on a FastAPI app."""
    app.add_exception_handler(WebBaseException, _web_base_exception_handler)


# ---------------------------------------------------------------------------
# Decorator for uniform error serialisation on route handlers
# ---------------------------------------------------------------------------

F = TypeVar("F", bound=Callable[..., Any])


def handle_errors(fn: F) -> F:
    """Decorator that converts raised WebBaseException to a typed JSONResponse.

    Usage::

        @app.get("/articles/{article_id}")
        @handle_errors
        def get_article(article_id: int):
            raise NotFoundError(message="Article not found")
    """

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Response:
        try:
            return await fn(*args, **kwargs)
        except WebBaseException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.to_response().model_dump(mode="json"),
            )

    return wrapper  # type: ignore[return-value]


__all__ = [
    "WebBaseException",
    "NotFoundError",
    "BadRequestError",
    "ConflictError",
    "UnauthorizedError",
    "ForbiddenError",
    "ValidationError",
    "ServiceUnavailableError",
    "register_exception_handlers",
    "handle_errors",
]
