# Copyright (c) 2026 Zhambyl Yermagambet
"""Render and register the API error contract."""

from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from api import config
from api.common.models.replies.error_response import ErrorResponse
from app import provider_audit_storage
from app.injection import resolve
from audit.documents import PathAudit
from domain.errors import ApplicationInputError

AUDIT_PATH_CHARACTER_LIMIT = 200
FRAMEWORK_NOT_FOUND = "Not Found"


def configure(web: FastAPI) -> None:
    """Register API exception handlers."""
    web.add_exception_handler(StarletteHTTPException, _http_error)  # type: ignore[arg-type]
    web.add_exception_handler(RequestValidationError, _validation_error)  # type: ignore[arg-type]
    web.add_exception_handler(ApplicationInputError, _application_input_error)
    web.add_exception_handler(Exception, _internal_error)


def error_body(message: str, status_code: int) -> Response:
    """Render the shared error model with the normal response security headers.

    Returns:
        The checked public error body for a known request failure.

    """
    return Response(
        ErrorResponse(error=message).model_dump_json(),
        status_code,
        headers=config.SECURITY_HEADERS,
        media_type="application/json",
    )


def _http_error(_request: Request, error: StarletteHTTPException) -> Response:
    message = "not found" if str(error.detail) == FRAMEWORK_NOT_FOUND else str(error.detail)
    return error_body(message, error.status_code)


def _validation_error(_request: Request, error: RequestValidationError) -> Response:
    first = error.errors()[0]
    location_parts = (str(part) for part in first["loc"] if part != "body")
    location = ".".join(location_parts)
    message = f"{location}: {first['msg']}" if location else str(first["msg"])
    return error_body(message, HTTPStatus.BAD_REQUEST)


def _application_input_error(_request: Request, error: Exception) -> Response:
    message = error.args[0] if error.args else str(error)
    return error_body(str(message), HTTPStatus.BAD_REQUEST)


def _internal_error(request: Request, _error: Exception) -> Response:
    audit = resolve(request.app.state.instances, provider_audit_storage.recorder)
    action = "POST" if request.method == "POST" else "request"
    path = PathAudit(path=request.url.path[:AUDIT_PATH_CHARACTER_LIMIT])
    audit.error("", f"dashboard {action}", path)
    return error_body("internal", HTTPStatus.INTERNAL_SERVER_ERROR)
