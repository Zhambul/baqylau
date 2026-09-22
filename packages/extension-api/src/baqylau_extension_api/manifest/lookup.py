# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve declared source and event schemas without importing feature code."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.data import DocumentDefinition, ScopeKind
from baqylau_extension_api.manifest.package import ExtensionManifest


def source_definition(manifest: ExtensionManifest, source_type: str, scope: ScopeKind) -> DocumentDefinition:
    """Select the owned source declaration for the requested scope.

    Returns:
        The exact source schema registration.

    """
    return _definition(manifest.contributions.source_types, source_type, scope, "observation source")


def event_definition(manifest: ExtensionManifest, event_type: str, scope: ScopeKind) -> DocumentDefinition:
    """Select the owned event declaration for the requested scope.

    Returns:
        The exact event schema registration.

    """
    return _definition(manifest.contributions.event_types, event_type, scope, "extension event")


def _definition(
    declarations: tuple[DocumentDefinition, ...], name: str, scope: ScopeKind, label: str,
) -> DocumentDefinition:
    for declaration in declarations:
        if declaration.name == name:
            if scope not in declaration.scopes:
                message = f"{label} scope is not declared"
                raise ExtensionContractError(message)
            return declaration
    message = f"{label} type is not declared"
    raise ExtensionContractError(message)


def collection_definition(manifest: ExtensionManifest, collection: str, scope: ScopeKind) -> DocumentDefinition:
    """Select an owned record collection for the requested scope.

    Returns:
        The exact record schema registration.

    """
    return _definition(manifest.contributions.collections, collection, scope, "record collection")


def entry_definition(manifest: ExtensionManifest, entry_type: str, scope: ScopeKind) -> DocumentDefinition:
    """Select an owned feed row type for the requested scope.

    Returns:
        The exact feed row schema registration.

    """
    return _definition(manifest.contributions.entry_types, entry_type, scope, "feed entry")
