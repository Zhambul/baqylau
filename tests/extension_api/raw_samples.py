# Copyright (c) 2026 Zhambyl Yermagambet
"""Build immutable raw content and transform fixtures."""

from baqylau_extension_api.identities import DerivedIdentity, derived_input_id
from baqylau_extension_api.models.content import ContentBundle, encode_content
from baqylau_extension_api.models.events import RawInput
from baqylau_extension_api.models.transforms import Insert, RawTransformRequest

from tests.extension_api import samples


def raw_request() -> RawTransformRequest:
    """Return one recorded source and its exact bytes.

    Returns:
        A complete immutable input snapshot.

    """
    return RawTransformRequest(
        context=samples.processing_context(), inputs=(samples.raw_input(),),
        content_snapshot=ContentBundle(blobs=(encode_content(b"hello", "text/plain"),)),
    )


def insertion(output_key: str = "extra") -> Insert[RawInput]:
    """Add a derived input with the original translator and source context.

    Returns:
        One stable insertion after the original input.

    """
    identity = derived_input_id(DerivedIdentity(
        extension_id=samples.EXTENSION_ID, input_id="raw-1", output_key=output_key,
    ))
    return Insert(
        input_id="raw-1", output_key=output_key, position="after",
        document=samples.raw_input().model_copy(update={"input_id": identity}),
    )
