# Copyright (c) 2026 Zhambyl Yermagambet
"""Build large raw and canonical replies for journal admission tests."""

from baqylau_extension_api.models import content, raw_transforms, transforms

from extensions.models import interpretation_admission
from tests.extension_host import interpretation_normalization_fixture as evidence
from tests.extension_host.interpretation_large_fixture import large_insertions

RAW_OVERHEAD_BYTES = 4096
RAW_MEDIA_TYPE = "application/json"


def large_canonical_reply(request: transforms.CanonicalTransformRequest) -> transforms.CanonicalTransformResult:
    """Return one valid canonical reply above the call budget.

    Returns:
        The complete reply with distinct large additions.

    """
    return transforms.CanonicalTransformResult(
        operations=large_insertions(request.inputs[0], evidence.INSERTION_COUNT, evidence.LARGE_DOCUMENT),
    )


def large_raw_reply(request: transforms.RawTransformRequest) -> raw_transforms.RawTransformResult:
    """Return one admitted replacement which consumes the call budget.

    Returns:
        The complete reply with one large string replacement.

    """
    source = request.inputs[0]
    body = b"x" * (interpretation_admission.ADMISSION_RESERVE_BYTES - RAW_OVERHEAD_BYTES)
    encoded = b"".join((b'"', body, b'"'))
    blob = content.encode_content(encoded, RAW_MEDIA_TYPE)
    replacement = source.model_copy(update={"content": blob.reference})
    return raw_transforms.RawTransformResult(
        operations=(transforms.Replace(input_id=source.input_id, document=replacement),),
        content_snapshot=content.ContentBundle(blobs=(blob,)),
    )
