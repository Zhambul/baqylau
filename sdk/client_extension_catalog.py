# Copyright (c) 2026 Zhambyl Yermagambet
"""Read and rescan the daemon's extension catalog through its public API."""

from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import urlencode

from baqylau_extension_api.models.scopes import ExtensionScope
from pydantic import TypeAdapter

from api.extensions.models import ExtensionCatalogResponse, RescanExtensionsRequest
from api.extensions.records_models import ExtensionRecordPageResponse
from sdk.client_extension_changes import ExtensionChangesResource
from sdk.client_extension_commands import ExtensionCommandsResource
from sdk.client_extension_jobs import ExtensionJobsResource
from sdk.client_extension_lifecycle import ExtensionLifecycleResource
from sdk.client_extension_queries import ExtensionQueriesResource
from sdk.client_extension_settings import ExtensionSettingsResource
from sdk.transport import HttpTransport

DEFAULT_RECORD_PAGE = 50
RECORD_PAGE = TypeAdapter(ExtensionRecordPageResponse)


@dataclass(frozen=True)
class ExtensionsResource:
    """Keep catalog reads separate from checked lifecycle requests."""

    transport: HttpTransport

    @property
    def settings(self) -> ExtensionSettingsResource:
        """Typed settings reads and revision-checked changes."""
        return ExtensionSettingsResource(self.transport)

    @property
    def lifecycle(self) -> ExtensionLifecycleResource:
        """The checked lifecycle read and mutation API."""
        return ExtensionLifecycleResource(self.transport)

    @property
    def queries(self) -> ExtensionQueriesResource:
        """Typed declared reads."""
        return ExtensionQueriesResource(self.transport)

    @property
    def changes(self) -> ExtensionChangesResource:
        """Typed committed record changes."""
        return ExtensionChangesResource(self.transport)

    @property
    def jobs(self) -> ExtensionJobsResource:
        """Typed durable job reads."""
        return ExtensionJobsResource(self.transport)

    @property
    def commands(self) -> ExtensionCommandsResource:
        """Typed durable command submissions."""
        return ExtensionCommandsResource(self.transport)

    def catalog(self) -> ExtensionCatalogResponse:
        """Read the stored package descriptions without a filesystem rescan.

        Returns:
            The current catalog revision.

        """
        return self.transport.get("/api/extensions", TypeAdapter(ExtensionCatalogResponse))

    def records(
        self,
        extension_id: str,
        collection: str,
        scope: ExtensionScope,
        after: str = "",
        limit: int = DEFAULT_RECORD_PAGE,
    ) -> ExtensionRecordPageResponse:
        """Read one ordered page of a declared record collection.

        Returns:
            The typed record page.

        """
        scope_text = scope.model_dump_json()
        query = urlencode((("scope", scope_text), ("after", after), ("limit", limit)))
        return self.transport.get(f"/api/extensions/{extension_id}/records/{collection}?{query}", RECORD_PAGE)

    def rescan(self, expected_revision: int) -> ExtensionCatalogResponse:
        """Request a revision-checked scan of the configured roots.

        Returns:
            The accepted catalog; stale requests use the standard SDK failure.

        """
        _, response = self.transport.post(
            "/api/extensions/rescan", RescanExtensionsRequest(expected_revision=expected_revision),
            TypeAdapter(ExtensionCatalogResponse), {HTTPStatus.OK},
        )
        return response
