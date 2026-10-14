"""Domain-level exceptions + FastAPI exception handlers.

Services raise these instead of ``HTTPException`` so the domain layer stays
framework-agnostic; ``register_exception_handlers`` translates them to HTTP.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base class for expected, client-facing errors."""

    status_code: int = status.HTTP_400_BAD_REQUEST

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT


class UnprocessableEntityError(AppError):
    """422 carrying per-field errors in the same shape as FastAPI's own.

    Used when a value is only invalid in context (e.g. ``num_gpus`` exceeds the
    *group's* ``max_gpus_per_job``, which is unknown to the static schema).
    """

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("Validation failed")
        self.errors = errors


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS

    def __init__(self, detail: str, retry_after: int = 0) -> None:
        super().__init__(detail)
        self.retry_after = retry_after


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        headers: dict[str, str] = {}
        if isinstance(exc, RateLimitError):
            headers["Retry-After"] = str(max(exc.retry_after, 1))
        if isinstance(exc, AuthenticationError):
            headers["WWW-Authenticate"] = "Bearer"
        content: dict[str, Any] = {"detail": exc.detail}
        if isinstance(exc, UnprocessableEntityError):
            # Mirror FastAPI's request-validation body so clients parse 422 the
            # same way whether the error came from the schema or the service.
            content = {"detail": exc.errors}
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
            headers=headers,
        )
