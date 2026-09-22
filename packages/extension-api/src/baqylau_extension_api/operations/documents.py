# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply common schema and settings checks at operation boundaries."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.documents import EncodedDocument, SchemaRef
from baqylau_extension_api.schemas import SchemaSet


def require_document_schema(
    document: EncodedDocument, reference: SchemaRef, schemas: SchemaSet, label: str,
) -> None:
    """Require the exact registered schema before decoding extension content.

    Raises:
        ExtensionContractError: If a document uses a different schema identity.

    """
    if document.schema_ref != reference:
        message = f"{label} does not match its declared schema"
        raise ExtensionContractError(message)
    schemas.validate(document)


def validate_settings(manifest: ExtensionManifest, settings: EncodedDocument | None, schemas: SchemaSet) -> None:
    """Require captured effective settings to match the package declaration.

    Raises:
        ExtensionContractError: If settings are missing or not declared.

    """
    declaration = manifest.settings
    if declaration is None:
        if settings is not None:
            message = "operation settings are not declared"
            raise ExtensionContractError(message)
        return
    if settings is None:
        message = "operation settings do not match the declared schema"
        raise ExtensionContractError(message)
    require_document_schema(settings, declaration.defaults.schema_ref, schemas, "operation settings")


def validate_owned_document(document: EncodedDocument, owner: str, schemas: SchemaSet) -> None:
    """Keep package state and receipts inside their declared schema owner.

    Raises:
        ExtensionContractError: If the document claims another owner.

    """
    if document.schema_ref.owner != owner:
        message = "operation document must use its extension's schema"
        raise ExtensionContractError(message)
    schemas.validate(document)
