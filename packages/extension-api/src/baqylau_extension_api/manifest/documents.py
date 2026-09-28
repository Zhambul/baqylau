# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate registered schemas, document namespaces, and operation signatures."""

from collections.abc import Iterator

from baqylau_extension_api import schema_documents
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import migration_rules, rules
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.documents import SchemaDefinition, SchemaRef
from baqylau_extension_api.schemas import SchemaSet


def validate_documents(manifest: ExtensionManifest, peer_schemas: tuple[SchemaDefinition, ...]) -> None:
    """Resolve exact declared schemas and validate default settings.

    Raises:
        ExtensionContractError: If a schema claims another package's owner.

    """
    if any(definition.reference.owner != manifest.extension_id for definition in manifest.schemas):
        message = "bundled schemas must belong to their package"
        raise ExtensionContractError(message)
    registry = SchemaSet((*manifest.schemas, *peer_schemas))
    _validate_schema_dependencies(manifest, peer_schemas)
    permitted = {manifest.extension_id, *(peer.extension_id for peer in manifest.dependencies)}
    for reference in _schema_references(manifest):
        if reference.owner not in permitted:
            message = "a peer schema requires a declared package dependency"
            raise ExtensionContractError(message)
        registry.definition(reference)
    if manifest.settings is not None:
        registry.validate(manifest.settings.defaults)
    _validate_registrations(manifest)
    migration_rules.validate_migration_paths(manifest, registry)


def _schema_references(manifest: ExtensionManifest) -> Iterator[SchemaRef]:
    contributions = manifest.contributions
    for definition in (
        *contributions.event_types, *contributions.entry_types, *contributions.source_types, *contributions.collections,
    ):
        yield definition.schema_ref
    for operation in (*contributions.queries, *contributions.commands):
        yield operation.arguments
        yield operation.result
    if manifest.settings is not None:
        yield manifest.settings.defaults.schema_ref


def _validate_schema_dependencies(manifest: ExtensionManifest, peer_schemas: tuple[SchemaDefinition, ...]) -> None:
    permitted = {manifest.extension_id, *(peer.extension_id for peer in manifest.dependencies)}
    targets = {
        schema_documents.schema_uri(definition)
        for definition in (*manifest.schemas, *peer_schemas)
        if definition.reference.owner in permitted
    }
    for definition in manifest.schemas:
        if not set(schema_documents.external_references(definition)) <= targets:
            message = "schema references require a declared package dependency"
            raise ExtensionContractError(message)


def _validate_registrations(manifest: ExtensionManifest) -> None:
    contributions = manifest.contributions
    for group in (
        contributions.event_types, contributions.entry_types, contributions.source_types, contributions.collections,
    ):
        rules.require_unique((definition.name for definition in group), "document type IDs")
        rules.require_owned((definition.name for definition in group), manifest.extension_id)
        for definition in group:
            rules.require_unique(definition.scopes, "document scopes")
            if definition.schema_ref.owner != manifest.extension_id:
                message = "owned document types require an owned schema"
                raise ExtensionContractError(message)
    _validate_operations(manifest)
    for selection in contributions.processing:
        rules.require_unique(selection.scopes, "processing scopes")
        rules.require_unique(selection.input_types, "processing input types")


def _validate_operations(manifest: ExtensionManifest) -> None:
    operations = (*manifest.contributions.queries, *manifest.contributions.commands)
    rules.require_unique((operation.name for operation in operations), "operation IDs")
    rules.require_owned((operation.name for operation in operations), manifest.extension_id)
    for operation in operations:
        rules.require_unique(operation.scopes, "operation scopes")
    rules.require_unique((process.name for process in manifest.contributions.processes), "process names")
    if manifest.settings is not None:
        rules.require_unique(manifest.settings.scopes, "settings scopes")
        rules.require_unique((secret.name for secret in manifest.settings.secret_references), "secret reference names")
        if manifest.settings.defaults.schema_ref.owner != manifest.extension_id:
            message = "settings require an owned schema"
            raise ExtensionContractError(message)
