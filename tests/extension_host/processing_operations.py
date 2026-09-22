# Copyright (c) 2026 Zhambyl Yermagambet
"""Build valid and invalid replies at the actual selected worker request."""

from baqylau_extension_api.models import content, events, raw_transforms, transforms

from tests.extension_host import interpretation_transforms as canonical


def raw_replace(request: transforms.RawTransformRequest, encoded: bytes) -> raw_transforms.RawTransformResult:
    """Replace only content while preserving the original source identity.

    Returns:
        A complete reply with exact replacement bytes.

    """
    blob = content.encode_content(encoded, "application/json")
    source = request.inputs[0].model_copy(update={"content": blob.reference})
    return raw_transforms.RawTransformResult(
        operations=(transforms.Replace[events.RawInput](
            input_id=request.inputs[0].input_id, document=source,
        ),), content_snapshot=content.ContentBundle(blobs=(blob,)),
    )


def raw_drop(request: transforms.RawTransformRequest) -> raw_transforms.RawTransformResult:
    """Suppress all raw output for this original.

    Returns:
        An explicit drop with a retained reason.

    """
    return raw_transforms.RawTransformResult(operations=(transforms.Drop(
        input_id=request.inputs[0].input_id, reason="Fixture raw drop",
    ),))


def canonical_drop(request: transforms.CanonicalTransformRequest) -> transforms.CanonicalTransformResult:
    """Suppress only final candidates, not the complete decoder reply.

    Returns:
        A canonical drop result.

    """
    return transforms.CanonicalTransformResult(operations=(transforms.Drop(
        input_id=request.inputs[0].event_id, reason="Fixture canonical drop",
    ),))


def canonical_insert(request: transforms.CanonicalTransformRequest) -> transforms.CanonicalTransformResult:
    """Add a stable fact whose cause is the original candidate.

    Returns:
        An inserted fact with its anchor suppressed from final output.

    """
    return transforms.CanonicalTransformResult(operations=(
        canonical.insertion(request.inputs[0]),
        transforms.Drop(input_id=request.inputs[0].event_id, reason="Fixture replaced display fact"),
    ))
