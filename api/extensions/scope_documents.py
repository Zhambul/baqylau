# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one extension scope document from a request."""

from http import HTTPStatus
from typing import Annotated

from baqylau_extension_api.models.scopes import ExtensionScope, InstallationScope
from fastapi import HTTPException, Query
from pydantic import TypeAdapter, ValidationError

SCOPE_ADAPTER: TypeAdapter[ExtensionScope] = TypeAdapter(ExtensionScope)
MAX_SCOPE_CHARACTERS = 16_384
OptionalScopeQuery = Annotated[
    str | None,
    Query(max_length=MAX_SCOPE_CHARACTERS, description="JSON-encoded ExtensionScope; the installation scope if absent"),
]


def request_scope(document: str) -> ExtensionScope:
    """Validate one scope document from a request.

    Returns:
        The checked scope.

    Raises:
        HTTPException: If the document is not a valid extension scope.

    """
    try:
        return SCOPE_ADAPTER.validate_json(document)
    except ValidationError as error:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "scope must be a valid extension scope document") from error


def request_scope_or_installation(document: str | None) -> ExtensionScope:
    """Validate an optional scope document; an absent document selects the installation.

    Returns:
        The checked scope.

    """
    return InstallationScope() if document is None else request_scope(document)
