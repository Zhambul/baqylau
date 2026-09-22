# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one settings scope without returning other scopes or credential values."""

from baqylau_extension_api.manifest.settings import SettingsDefinition
from baqylau_extension_api.models.base import Identifier, Revision, WireModel
from baqylau_extension_api.models.documents import EncodedDocument, SchemaDefinition
from baqylau_extension_api.models.lifecycle import ExtensionInfo
from baqylau_extension_api.models.scopes import ExtensionScope


class SettingsSnapshot(WireModel):
    """Describe accepted settings and the exact package declaration used to resolve them."""

    extension_info: ExtensionInfo
    scope: ExtensionScope
    lifecycle_revision: Revision
    catalog_revision: Revision
    settings_revision: Revision
    selected_from_committed: bool
    pending_operation: Identifier | None
    definition: SettingsDefinition
    schemas: tuple[SchemaDefinition, ...]
    override: EncodedDocument | None
    effective: EncodedDocument
