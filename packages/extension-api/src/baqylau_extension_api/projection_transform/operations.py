# Copyright (c) 2026 Zhambyl Yermagambet
"""Check identities and protected state for each explicit projection operation."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.identities import DerivedIdentity, derived_projection_change_id
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.projection_changes import CoreEntryChange, ExtensionEntryChange, ProjectionChange
from baqylau_extension_api.models.projection_transforms import ProjectionTransformRequest
from baqylau_extension_api.models.transforms import Drop, Insert, Replace, TransformOperation
from baqylau_extension_api.projection_transform import core_rules, extension_rules
from baqylau_extension_api.schemas import SchemaSet


def validate_operation(
    manifest: ExtensionManifest, schemas: SchemaSet, request: ProjectionTransformRequest,
    original: ProjectionChange, operation: TransformOperation[ProjectionChange],
) -> None:
    """Check one operation before the whole ordered result is assembled."""
    if isinstance(operation, Drop):
        core_rules.validate_core_drop(request.before_core, original)
    elif isinstance(operation, Replace):
        _validate_replacement(original, operation.document)
    elif isinstance(operation, Insert):
        _validate_insertion(manifest, request, original, operation)
        extension_rules.validate_extension_addition(manifest, schemas, operation.document)


def _validate_replacement(original: ProjectionChange, replacement: ProjectionChange) -> None:
    if original.change_id != replacement.change_id or original.kind != replacement.kind:
        message = "projection replacement must keep its change identity and kind"
        raise ExtensionContractError(message)
    core_rules.validate_core_replacement(original, replacement)
    extension_rules.validate_extension_replacement(original, replacement)


def _validate_insertion(
    manifest: ExtensionManifest, request: ProjectionTransformRequest,
    original: ProjectionChange, operation: Insert[ProjectionChange],
) -> None:
    expected_id = derived_projection_change_id(DerivedIdentity(
        extension_id=manifest.extension_id, input_id=original.change_id, output_key=operation.output_key,
    ))
    if operation.document.change_id != expected_id:
        message = "inserted projection change must use its derived identity"
        raise ExtensionContractError(message)
    core_rules.validate_core_addition(request.before_core, operation.document)
    _validate_feed_addition(original, operation.document)


def _validate_feed_addition(original: ProjectionChange, addition: ProjectionChange) -> None:
    if isinstance(addition, CoreEntryChange):
        _validate_core_feed_addition(original, addition)
    elif isinstance(addition, ExtensionEntryChange) and addition.entry.entry_key != addition.change_id:
        message = "inserted extension feed must use its derived change identity as its local key"
        raise ExtensionContractError(message)


def _validate_core_feed_addition(original: ProjectionChange, addition: CoreEntryChange) -> None:
    if not isinstance(original, CoreEntryChange):
        message = "a core feed addition requires a core feed anchor"
        raise ExtensionContractError(message)
    if addition.entry.entry_id != addition.change_id or addition.source_event_id != original.source_event_id:
        message = "inserted core feed must use its derived identity and anchor source"
        raise ExtensionContractError(message)
