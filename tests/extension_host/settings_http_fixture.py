# Copyright (c) 2026 Zhambyl Yermagambet
"""Select settings requests only from the public typed HTTP response."""

from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.scopes import ExtensionScope, InstallationScope

from api.extensions.settings_models import SettingsChangeRequest
from sdk.client import BaqylauClient

INSTALLATION = InstallationScope()


def request(
    client: BaqylauClient, owner: str, key: str, document: str | None, scope: ExtensionScope = INSTALLATION,
) -> SettingsChangeRequest:
    """Build an edit with the exact selected package and all observed revisions.

    Returns:
        A typed public request with no client-selected effective fallback.

    """
    current = client.extensions.settings.read(owner, scope).settings
    return SettingsChangeRequest(
        request_id=key, expected_revision=current.lifecycle_revision,
        expected_catalog_revision=current.catalog_revision, expected_settings_revision=current.settings_revision,
        package_digest=current.extension_info.package_digest, scope=scope,
        document=None if document is None else EncodedDocument(
            schema_ref=current.definition.defaults.schema_ref, json_text=document,
        ),
    )
