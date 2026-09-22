# Copyright (c) 2026 Zhambyl Yermagambet
"""Decode the public settings selection without reading application state."""

from http import HTTPStatus
from typing import Annotated

from baqylau_extension_api.models.base import Digest
from baqylau_extension_api.models.scopes import ExtensionScope, InstallationScope
from fastapi import Depends, HTTPException, Query
from pydantic import TypeAdapter, ValidationError

from extensions.models.settings_requests import SettingsReadRequest

SCOPE_ADAPTER: TypeAdapter[ExtensionScope] = TypeAdapter(ExtensionScope)
MAX_SCOPE_CHARACTERS = 16_384
ScopeQuery = Annotated[
    str | None, Query(max_length=MAX_SCOPE_CHARACTERS, description="JSON-encoded ExtensionScope"),
]


def settings_read_request(
    scope: ScopeQuery = None,
    package_digest: Digest | None = None,
) -> SettingsReadRequest:
    """Decode the typed JSON query parameter before the settings service runs.

    Returns:
        The exact selected scope and optional expected digest.

    Raises:
        HTTPException: If the JSON query does not match a declared scope model.

    """
    try:
        selected = InstallationScope() if scope is None else SCOPE_ADAPTER.validate_json(scope)
    except ValidationError as error:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "settings scope must be a valid ExtensionScope document") from error
    return SettingsReadRequest(scope=selected, package_digest=package_digest)


SettingsSelection = Annotated[SettingsReadRequest, Depends(settings_read_request)]
