# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply complete transform checks and build stable typed addition fixtures."""

from baqylau_extension_api.identities import DerivedIdentity, derived_projection_change_id
from baqylau_extension_api.models.projection_changes import CoreEntryChange, ExtensionEntryChange, ProjectionChange
from baqylau_extension_api.models.projection_transforms import ProjectionTransformRequest, ProjectionTransformResult
from baqylau_extension_api.models.transforms import Insert, TransformOperation
from baqylau_extension_api.projection_transform.results import apply_projection_transform
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import operation_samples, projection_transform_samples


def apply(
    request: ProjectionTransformRequest, *operations: TransformOperation[ProjectionChange],
) -> tuple[ProjectionChange, ...]:
    """Apply all proposal and operation checks used by the worker adapter.

    Returns:
        Complete ordered output without changing the request.

    """
    manifest = projection_transform_samples.manifest()
    response = ProjectionTransformResult(binding=request.binding, operations=operations)
    return apply_projection_transform(manifest, request, response, SchemaSet(manifest.schemas))


def extension_addition(original: ProjectionChange, key: str = "extra") -> Insert[ProjectionChange]:
    """Insert an owned feed row with a stable operation and local row identity.

    Returns:
        A valid addition after the selected input change.

    """
    change_id = derived_projection_change_id(DerivedIdentity(
        extension_id=operation_samples.OWNER, input_id=original.change_id, output_key=key,
    ))
    row = projection_transform_samples.extension_entry()
    document = ExtensionEntryChange(
        change_id=change_id, owner=row.owner, scope=row.scope,
        entry=row.entry.model_copy(update={"entry_key": change_id}),
    )
    return Insert(input_id=original.change_id, output_key=key, position="after", document=document)


def core_addition(original: CoreEntryChange, key: str = "core-extra") -> Insert[ProjectionChange]:
    """Insert a new core feed row while preserving the anchor's source references.

    Returns:
        A stable core entry addition with no host commit metadata.

    """
    change_id = derived_projection_change_id(DerivedIdentity(
        extension_id=operation_samples.OWNER, input_id=original.change_id, output_key=key,
    ))
    document = original.model_copy(update={
        "change_id": change_id, "entry": original.entry.model_copy(update={"entry_id": change_id}),
    })
    return Insert(input_id=original.change_id, output_key=key, position="after", document=document)
