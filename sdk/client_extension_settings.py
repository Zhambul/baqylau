# Copyright (c) 2026 Zhambyl Yermagambet
"""Use public settings reads and writes without reaching host services."""

from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import quote

from baqylau_extension_api.models.scopes import ExtensionScope, InstallationScope
from httpx import QueryParams
from pydantic import TypeAdapter

from api.extensions.lifecycle_models import LifecycleAdmissionResponse
from api.extensions.settings_models import ExtensionSettingsResponse, SettingsChangeRequest
from sdk.transport import HttpTransport

DEFAULT_SCOPE = InstallationScope()
SCOPE_ADAPTER: TypeAdapter[ExtensionScope] = TypeAdapter(ExtensionScope)


@dataclass(frozen=True)
class ExtensionSettingsResource:
    """Read one accepted scope, then submit its exact checked replacement."""

    transport: HttpTransport

    def read(
        self, extension_id: str, scope: ExtensionScope = DEFAULT_SCOPE, package_digest: str | None = None,
    ) -> ExtensionSettingsResponse:
        """Select the scope with the public JSON query parameter.

        Returns:
            Accepted settings and revisions, not a pending candidate.

        """
        query_items = QueryParams().set("scope", SCOPE_ADAPTER.dump_json(scope).decode())
        if package_digest is not None:
            query_items = query_items.set("package_digest", package_digest)
        selected = quote(extension_id, safe="")
        return self.transport.get(
            f"/api/extensions/{selected}/settings?{query_items}", TypeAdapter(ExtensionSettingsResponse),
        )

    def change(
        self, extension_id: str, settings_change_request: SettingsChangeRequest,
    ) -> LifecycleAdmissionResponse:
        """Keep one stable request ID until the accepted operation reaches its result.

        Returns:
            The accepted or replayed settings operation, not saved values.

        """
        selected = quote(extension_id, safe="")
        _, reply = self.transport.put(
            f"/api/extensions/{selected}/settings", settings_change_request,
            TypeAdapter(LifecycleAdmissionResponse), {HTTPStatus.ACCEPTED},
        )
        return reply
