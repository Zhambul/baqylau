# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate a complete data-only registration before feature code runs."""

from packaging.specifiers import SpecifierSet

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import documents, presentation, selections, services, structure
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.documents import SchemaDefinition
from baqylau_extension_api.versions import API_VERSION


def validate_manifest(
    manifest: ExtensionManifest, peer_schemas: tuple[SchemaDefinition, ...] = (),
) -> ExtensionManifest:
    """Validate package-local declarations and exact schema references.

    File existence, content hashes, active dependencies, and installed API
    compatibility are separate host checks. This function executes no code.

    Returns:
        A revalidated immutable manifest.

    """
    checked = ExtensionManifest.model_validate(manifest)
    structure.validate_identities(checked)
    structure.validate_capabilities(checked)
    documents.validate_documents(checked, peer_schemas)
    presentation.validate_views(checked)
    services.validate_services(checked)
    selections.validate_input_types(checked)
    return checked


def require_compatible_api(manifest: ExtensionManifest, api_version: str = API_VERSION) -> None:
    """Apply the declared PEP 440 range, including explicit prerelease rules.

    Raises:
        ExtensionContractError: If the host API does not satisfy the package.

    """
    if not SpecifierSet(manifest.api_requires).contains(api_version):
        message = "extension API range does not include the host API version"
        raise ExtensionContractError(message)
