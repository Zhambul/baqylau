# Copyright (c) 2026 Zhambyl Yermagambet
"""Convert captured raw overrides through the existing pure migration protocol."""

from dataclasses import dataclass
from threading import Event

from baqylau_extension_api.contracts.migrations import ExtensionMigrations
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.migrations import requests, results
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.migrations import MigrationBinding, SettingsMigrationRequest
from baqylau_extension_api.models.scopes import ExtensionScope, InstallationScope
from baqylau_extension_api.schemas import SchemaSet

from extensions.models.registry import ScopedRuntimeSettings
from extensions.models.settings import SettingsOverrides
from extensions.runtime_preparation_contract import RuntimePreparationError, RuntimePreparationStoppedError


@dataclass(frozen=True)
class SettingsMigration:
    """Own one pure conversion context, with no repository or live service access."""

    manifest: ExtensionManifest
    provider: ExtensionMigrations
    runtime_revision: str
    source: SettingsOverrides
    stop_requested: Event | None

    def resolve(self) -> SettingsOverrides:
        """Convert complete explicit choices without copying inherited defaults.

        Returns:
            New raw values at the next owner revision; no partial result escapes.

        """
        schemas = SchemaSet(self.manifest.schemas)
        installation = None if self.source.installation is None else self._convert(
            self.source.installation, InstallationScope(), schemas, 0,
        )
        scopes = tuple(ScopedRuntimeSettings(
            scope=entry.scope,
            settings=self._convert(entry.settings, entry.scope, schemas, index + 1),
        ) for index, entry in enumerate(self.source.scopes))
        return SettingsOverrides(revision=self.source.revision + 1, installation=installation, scopes=scopes)

    def _convert(
        self, document: EncodedDocument, scope: ExtensionScope, schemas: SchemaSet, index: int,
    ) -> EncodedDocument:
        if self.stop_requested is not None and self.stop_requested.is_set():
            message = "the manager stopped settings migration"
            raise RuntimePreparationStoppedError(message)
        definition = self.manifest.settings
        if definition is None:
            message = "settings migration target declaration is missing"
            raise RuntimePreparationError(message)
        if document.schema_ref == definition.defaults.schema_ref:
            schemas.validate(document)
            return document
        request = requests.validate_settings_request(self.manifest, schemas, SettingsMigrationRequest(
            binding=MigrationBinding(
                extension_id=self.manifest.extension_id, scope=scope, runtime_revision=self.runtime_revision,
                candidate_id=self.runtime_revision, call_id=f"settings-{index}",
            ), source_schema=document.schema_ref, target_schema=definition.defaults.schema_ref,
            source_revision=self.source.revision, source=document,
        ))
        response = results.validate_settings_result(request, self.provider.migrate_settings(request))
        results.validate_migrated_documents(schemas, response)
        if response.status != "ready":
            message = "the extension could not convert candidate settings"
            raise RuntimePreparationError(message)
        return response.document
