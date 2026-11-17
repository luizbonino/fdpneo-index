"""Error types and the JSON error body.

The body keeps the reference implementation's ``ErrorDTO`` fields
(``timestamp`` in epoch milliseconds, ``status``, ``error``, ``message``,
``path``) so existing clients read it unchanged, and adds a stable ``code``.
Request validation failures are ``400``, as in the reference, not FastAPI's
default ``422``.
"""

from __future__ import annotations

import time
from http import HTTPStatus
from typing import ClassVar

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger(__name__)


class IndexServiceError(Exception):
    """Base class for every domain error the index raises."""

    code: ClassVar[str] = "index.error"
    http_status: ClassVar[int] = 500

    def __init__(
        self,
        message: str,
        *,
        errors: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors
        self.headers = headers


class BadRequest(IndexServiceError):
    code = "index.bad_request"
    http_status = 400


class Unauthorized(IndexServiceError):
    code = "index.unauthorized"
    http_status = 401


class Forbidden(IndexServiceError):
    code = "index.forbidden"
    http_status = 403


class NotFound(IndexServiceError):
    code = "index.not_found"
    http_status = 404


class Conflict(IndexServiceError):
    code = "index.conflict"
    http_status = 409


class PingDenied(IndexServiceError):
    code = "index.ping_denied"
    http_status = 403


class RateLimited(IndexServiceError):
    code = "index.rate_limited"
    http_status = 429


def error_body(
    status: int,
    message: str,
    path: str,
    *,
    code: str,
    errors: dict[str, object] | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "timestamp": int(time.time() * 1000),
        "status": status,
        "error": HTTPStatus(status).phrase,
        "code": code,
        "message": message,
        "path": path,
    }
    if errors:
        body["errors"] = errors
    return body


def register_exception_handlers(app: FastAPI) -> None:
    async def _domain(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, IndexServiceError)
        return JSONResponse(
            error_body(
                exc.http_status, exc.message, request.url.path, code=exc.code, errors=exc.errors
            ),
            status_code=exc.http_status,
            headers=exc.headers,
        )

    async def _validation(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        errors: dict[str, object] = {}
        for item in exc.errors():
            location = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
            errors[location or "body"] = item.get("msg", "invalid")
        return JSONResponse(
            jsonable_encoder(
                error_body(
                    400, "Invalid request", request.url.path, code="index.invalid", errors=errors
                )
            ),
            status_code=400,
        )

    async def _http(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)
        return JSONResponse(
            error_body(
                exc.status_code, str(exc.detail), request.url.path, code=f"http.{exc.status_code}"
            ),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path, error=str(exc))
        return JSONResponse(
            error_body(500, "Internal server error", request.url.path, code="index.internal"),
            status_code=500,
        )

    app.add_exception_handler(IndexServiceError, _domain)
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(StarletteHTTPException, _http)
    app.add_exception_handler(Exception, _unexpected)
