# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate source ownership, registration, and captured settings."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import lookup, rules
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.sources import SourceBinding, SourceContext, SourceDescriptor, SourceReadRequest
from baqylau_extension_api.operations import documents
from baqylau_extension_api.schemas import SchemaSet


def validate_source_binding(manifest: ExtensionManifest, binding: SourceBinding) -> SourceBinding:
    """Reject undeclared source access before calling feature code.

    Returns:
        A checked source call binding.

    Raises:
        ExtensionContractError: If the owner, capability, or scope is not declared.

    """
    checked = SourceBinding.model_validate(binding)
    if checked.extension_id != manifest.extension_id or "sources" not in manifest.capabilities:
        message = "source binding does not match a declared source provider"
        raise ExtensionContractError(message)
    declared_sources = manifest.contributions.source_types
    has_scope = any(checked.scope.kind in source.scopes for source in declared_sources)
    if not has_scope:
        message = "source provider scope is not declared"
        raise ExtensionContractError(message)
    return checked


def validate_source_context(manifest: ExtensionManifest, schemas: SchemaSet, request: SourceContext) -> SourceContext:
    """Validate one live source call and its captured settings.

    Returns:
        A checked immutable discovery context.

    """
    checked = SourceContext.model_validate(request)
    validate_source_binding(manifest, checked.binding)
    documents.validate_settings(manifest, checked.settings, schemas)
    return checked


def validate_source_descriptor(
    manifest: ExtensionManifest, schemas: SchemaSet, binding: SourceBinding, source: SourceDescriptor,
) -> None:
    """Check registered source type, watch paths, and owned source state."""
    lookup.source_definition(manifest, source.source_type, binding.scope.kind)
    rules.require_unique(source.watch_paths, "source watch paths")
    if source.state is not None:
        documents.validate_owned_document(source.state, manifest.extension_id, schemas)


def validate_source_read(
    manifest: ExtensionManifest, schemas: SchemaSet, request: SourceReadRequest,
) -> SourceReadRequest:
    """Check the complete selected source before reading external bytes.

    Returns:
        A request with a bounded read limit and declared source state.

    """
    checked = SourceReadRequest.model_validate(request)
    validate_source_context(manifest, schemas, checked.context)
    validate_source_descriptor(manifest, schemas, checked.context.binding, checked.source)
    return checked
