# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate a complete canonical result before applying any operation."""

from collections.abc import Mapping

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.canonical import CanonicalFact
from baqylau_extension_api.models.transforms import (
    CanonicalTransformRequest,
    CanonicalTransformResult,
    Insert,
    Replace,
)
from baqylau_extension_api.processing import canonical_rules, order
from baqylau_extension_api.schemas import SchemaSet

UNKNOWN_INPUT = "transform operation refers to an unknown input"
DUPLICATE_OUTPUT = "transform output contains repeated event identities"


def apply_canonical_transform(
    canonical_request: CanonicalTransformRequest, response: CanonicalTransformResult, schemas: SchemaSet,
) -> tuple[CanonicalFact, ...]:
    """Apply a valid proposal without changing any input model or host storage.

    Returns:
        The full final batch, or no result if validation fails.

    """
    return _apply_checked(
        CanonicalTransformRequest.model_validate(canonical_request),
        CanonicalTransformResult.model_validate(response),
        schemas,
    )


def _apply_checked(
    request: CanonicalTransformRequest, response: CanonicalTransformResult, schemas: SchemaSet,
) -> tuple[CanonicalFact, ...]:
    inputs = _validated_inputs(request, response, schemas)
    output = order.apply_order(inputs, response.operations)
    if len({fact.event_id for fact in output}) != len(output):
        raise ExtensionContractError(DUPLICATE_OUTPUT)
    return output


def _validated_inputs(
    request: CanonicalTransformRequest, response: CanonicalTransformResult, schemas: SchemaSet,
) -> Mapping[str, CanonicalFact]:
    inputs = {fact.event_id: fact for fact in request.inputs}
    for operation in response.operations:
        if operation.input_id not in inputs:
            raise ExtensionContractError(UNKNOWN_INPUT)
        if isinstance(operation, Replace):
            canonical_rules.validate_replacement(
                inputs[operation.input_id], operation.document, request.context.extension_id,
            )
        elif isinstance(operation, Insert):
            canonical_rules.validate_insertion(inputs[operation.input_id], operation, request.context.extension_id)
        if isinstance(operation, (Replace, Insert)):
            canonical_rules.validate_document(operation.document, schemas)
    return inputs
