# Copyright (c) 2026 Zhambyl Yermagambet
"""Select one schema-checked settings override without selecting a runtime."""

from typing import Literal

from baqylau_extension_api.models.base import Digest, ExtensionId, Identifier, Revision, WireModel
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.scopes import ExtensionScope, InstallationScope

from extensions.models.control_requests import ControlRevisions


class SettingsReadRequest(WireModel):
    """Select one declared scope and optionally pin the expected package bytes."""

    scope: ExtensionScope = InstallationScope()
    package_digest: Digest | None = None


class SettingsRequest(ControlRevisions):
    """Replace one complete scope document, or reset it with an explicit null."""

    action: Literal["settings"] = "settings"
    request_id: Identifier
    package_digest: Digest
    expected_settings_revision: Revision
    scope: ExtensionScope
    document: EncodedDocument | None


class SettingsRequestOrigin(WireModel):
    """Retain the exact requested override for durable retry comparison."""

    extension_id: ExtensionId
    request: SettingsRequest
