# Copyright (c) 2026 Zhambyl Yermagambet
"""Read and rescan the daemon's extension catalog through its public API."""

from dataclasses import dataclass

from sdk.client_extension_catalog_reads import ExtensionCatalogReads
from sdk.client_extension_changes import ExtensionChangesResource
from sdk.client_extension_commands import ExtensionCommandsResource
from sdk.client_extension_jobs import ExtensionJobsResource
from sdk.client_extension_lifecycle import ExtensionLifecycleResource
from sdk.client_extension_queries import ExtensionQueriesResource
from sdk.client_extension_secrets import ExtensionSecretsResource
from sdk.client_extension_settings import ExtensionSettingsResource


@dataclass(frozen=True)
class ExtensionsResource(ExtensionCatalogReads):
    """Keep catalog reads separate from checked lifecycle requests."""

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
    def secrets(self) -> ExtensionSecretsResource:
        """Write-only secret values and their stored states."""
        return ExtensionSecretsResource(self.transport)

    @property
    def commands(self) -> ExtensionCommandsResource:
        """Typed durable command submissions."""
        return ExtensionCommandsResource(self.transport)
