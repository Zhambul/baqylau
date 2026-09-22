# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply complete projection operations only after validating every proposed write."""

from collections.abc import Mapping

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.projection_changes import ProjectionChange
from baqylau_extension_api.models.projection_transforms import ProjectionTransformRequest, ProjectionTransformResult
from baqylau_extension_api.processing import order
from baqylau_extension_api.projection.results import MAX_PROJECTION_RESPONSE_BYTES
from baqylau_extension_api.projection_transform import changes, inputs, operations
from baqylau_extension_api.schemas import SchemaSet


def validate_transform_reply(
    request: ProjectionTransformRequest, response: ProjectionTransformResult,
) -> ProjectionTransformResult:
    """Reject stale or oversized replies before applying any operation.

    Returns:
        A complete typed reply with its exact processing binding.

    Raises:
        ExtensionContractError: If the binding or encoded size is invalid.

    """
    checked_request = ProjectionTransformRequest.model_validate(request)
    checked = ProjectionTransformResult.model_validate(response)
    if checked.binding != checked_request.binding:
        message = "projection transform result must keep its exact binding"
        raise ExtensionContractError(message)
    if len(checked.model_dump_json().encode("utf-8")) > MAX_PROJECTION_RESPONSE_BYTES:
        message = "projection transform result exceeds its encoded size limit"
        raise ExtensionContractError(message)
    return checked


def apply_projection_transform(
    manifest: ExtensionManifest, request: ProjectionTransformRequest,
    response: ProjectionTransformResult, schemas: SchemaSet,
) -> tuple[ProjectionChange, ...]:
    """Return one complete transformed proposal without modifying input or storage.

    Returns:
        Ordered valid changes, or no result if any operation is invalid.

    """
    checked_request = inputs.validate_transform_request(manifest, schemas, request)
    checked = validate_transform_reply(checked_request, response)
    proposed = _validated_changes(manifest, schemas, checked_request, checked)
    transformed = order.apply_order(proposed, checked.operations)
    changes.validate_changes(checked_request, transformed, schemas)
    return transformed


def _validated_changes(
    manifest: ExtensionManifest, schemas: SchemaSet,
    request: ProjectionTransformRequest, response: ProjectionTransformResult,
) -> Mapping[str, ProjectionChange]:
    proposed = {change.change_id: change for change in request.changes}
    for operation in response.operations:
        original = proposed.get(operation.input_id)
        if original is None:
            message = "projection operation refers to an unknown input change"
            raise ExtensionContractError(message)
        operations.validate_operation(manifest, schemas, request, original, operation)
    return proposed
