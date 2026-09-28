# Copyright (c) 2026 Zhambyl Yermagambet
"""Decode the public settings selection without reading application state."""

from typing import Annotated

from baqylau_extension_api.models.base import Digest
from fastapi import Depends

from api.extensions.scope_documents import OptionalScopeQuery, request_scope_or_installation
from extensions.models.settings_requests import SettingsReadRequest


def settings_read_request(
    scope: OptionalScopeQuery = None,
    package_digest: Digest | None = None,
) -> SettingsReadRequest:
    """Decode the typed JSON query parameter before the settings service runs.

    Returns:
        The exact selected scope and optional expected digest.

    """
    return SettingsReadRequest(scope=request_scope_or_installation(scope), package_digest=package_digest)


SettingsSelection = Annotated[SettingsReadRequest, Depends(settings_read_request)]
