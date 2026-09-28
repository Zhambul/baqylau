# Copyright (c) 2026 Zhambyl Yermagambet
"""Map known lifecycle refusals to the application's shared error response."""

from http import HTTPStatus

from fastapi import FastAPI, Request, Response

from api.error_responses import error_body
from extensions.control_policy import ExtensionReadOnlyError
from extensions.lifecycle_control_contract import (
    LifecycleConflictError,
    LifecycleRequestError,
    LifecycleUnavailableError,
)
from extensions.manager_contract import ManagerStateError
from extensions.terminal_views import TerminalViewFailedError


def configure(web: FastAPI) -> None:
    """Register typed host refusals without exposing unknown internal exceptions."""
    refusals = (
        LifecycleRequestError, LifecycleUnavailableError, ExtensionReadOnlyError, ManagerStateError,
        TerminalViewFailedError,
    )
    for error_type in refusals:
        web.add_exception_handler(error_type, lifecycle_error)


def lifecycle_error(_request: Request, error: Exception) -> Response:
    """Return bounded messages selected by host code, never feature logs or settings.

    Returns:
        The standard public error body and the corresponding refusal status.

    """
    if isinstance(error, ExtensionReadOnlyError):
        status = HTTPStatus.FORBIDDEN
    elif isinstance(error, LifecycleConflictError):
        status = HTTPStatus.CONFLICT
    elif isinstance(error, (LifecycleUnavailableError, ManagerStateError, TerminalViewFailedError)):
        status = HTTPStatus.SERVICE_UNAVAILABLE
    else:
        status = HTTPStatus.BAD_REQUEST
    return error_body(str(error), status)
