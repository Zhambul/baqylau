# Copyright (c) 2026 Zhambyl Yermagambet
"""Read host-selected peer capabilities and captured settings through a protocol."""

from dataclasses import dataclass
from typing import Protocol

from baqylau_extension_api.contracts.operations import ExtensionQueries
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.environment import ExtensionEnvironment
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.schemas import SchemaSet


@dataclass(frozen=True)
class ServiceProvider:
    """Capture peer metadata, read capability, and effective settings for one scope."""

    manifest: ExtensionManifest
    schemas: SchemaSet
    environment: ExtensionEnvironment | None = None
    queries: ExtensionQueries | None = None
    settings_revision: int = 0
    settings: EncodedDocument | None = None


class ServiceProviderLookup(Protocol):
    """Select an immutable provider snapshot without importing feature code."""

    def get_service_provider(self, owner: str, scope: ExtensionScope) -> ServiceProvider | None:
        """Return installed metadata and active capabilities with settings for this scope."""
