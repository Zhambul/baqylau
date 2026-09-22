# Copyright (c) 2026 Zhambyl Yermagambet
"""Bind validated metadata to an optional prepared capability set."""

from dataclasses import dataclass, field

from baqylau_extension_api.contracts.plugin import ExtensionPlugin
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.directory import DirectoryEntry
from baqylau_extension_api.models.environment import ExtensionEnvironment
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.runtime.service_provider import ServiceProvider
from baqylau_extension_api.schemas import SchemaSet

from extensions.models.registry import RuntimeSettings


@dataclass(frozen=True)
class RegistryPackage:
    """Keep worker ownership with the manager; the registry only borrows capabilities."""

    manifest: ExtensionManifest
    entry: DirectoryEntry
    environment: ExtensionEnvironment | None = None
    plugin: ExtensionPlugin | None = None
    settings: RuntimeSettings = field(default_factory=RuntimeSettings)

    def service_provider(self, schemas: SchemaSet, scope: ExtensionScope) -> ServiceProvider:
        """Select immutable peer data for use while a registry read is held.

        Returns:
            Metadata for an installed package and capabilities only when enabled.

        """
        return ServiceProvider(
            manifest=self.manifest, schemas=schemas, environment=self.environment,
            queries=None if self.plugin is None else self.plugin.capabilities.queries,
            settings_revision=self.settings.revision, settings=self.settings.for_scope(scope),
        )
