# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply raw operations without changing the recorded source bytes."""

from collections.abc import Mapping

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.content import ContentBundle
from baqylau_extension_api.models.events import RawInput
from baqylau_extension_api.models.raw_transforms import RawTransformResult
from baqylau_extension_api.models.transforms import Insert, RawTransformRequest, Replace
from baqylau_extension_api.processing import order, raw_rules


def apply_raw_transform(request: RawTransformRequest, response: RawTransformResult) -> RawTransformRequest:
    """Return a complete checked snapshot for the next processing stage.

    Returns:
        The ordered inputs and exactly the content needed by their translator.

    """
    return _apply_checked(RawTransformRequest.model_validate(request), RawTransformResult.model_validate(response))


def _apply_checked(request: RawTransformRequest, response: RawTransformResult) -> RawTransformRequest:
    inputs = _validated_inputs(request, response)
    output = order.apply_order(inputs, response.operations)
    if len({source.input_id for source in output}) != len(output):
        message = "raw transform output contains repeated input identities"
        raise ExtensionContractError(message)
    return RawTransformRequest(
        context=request.context, inputs=output, content_snapshot=_output_content(request, response, output),
    )


def _validated_inputs(request: RawTransformRequest, response: RawTransformResult) -> Mapping[str, RawInput]:
    inputs = {source.input_id: source for source in request.inputs}
    for operation in response.operations:
        if operation.input_id not in inputs:
            message = "raw transform operation refers to an unknown input"
            raise ExtensionContractError(message)
        if isinstance(operation, Replace):
            raw_rules.validate_replacement(inputs[operation.input_id], operation.document)
        elif isinstance(operation, Insert):
            raw_rules.validate_insertion(inputs[operation.input_id], operation, request.context.extension_id)
    return inputs


def _output_content(
    request: RawTransformRequest, response: RawTransformResult, output: tuple[RawInput, ...],
) -> ContentBundle:
    combined = ContentBundle(blobs=(
        *request.content_snapshot.blobs, *response.content_snapshot.blobs,
    ))
    needed = frozenset(source.content for source in output)
    for reference in needed:
        combined.resolve(reference)
    if any(blob.reference not in needed for blob in response.content_snapshot.blobs):
        message = "raw transform returned unused content"
        raise ExtensionContractError(message)
    return ContentBundle(
        blobs=tuple(blob for blob in combined.blobs if blob.reference in needed),
    )
